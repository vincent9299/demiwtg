"""只读各作答路进度和A/B案例快照；复用benchmark的离线iframe分页容器。"""
from pathlib import Path
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import base64
import gzip
import hashlib
import io
import json
import math
import re
import sqlite3
import tempfile
import lance
from PIL import Image
from demiflow import data
from demiflow.execution.artifacts import run_is_active
from benchmark.t2i.v2.operators.case_viewer import notebook_browser
from benchmark.t2i.v2.operators.images import positive_image_data_url
from .run_tables import RunTables
from .request_view import answer_view, judge_view


MEDIA_BUDGET = 40 * 1024 * 1024  # 全量离线输出，给完整文字及 Notebook 容器保留空间。
HTML_BUDGET = 64 * 1024 * 1024
TEXT_BUDGET = 128 * 1024 * 1024  # Expanded bounded data for 300 questions × up to eight arms.
MAX_MEDIA_ITEMS = 32768


def _encode_thumbnails(media):
    """合并后逐图编码；按目标像素面积分配总字节预算，不删图或裁切内容。"""
    weight = sum(item['side'] ** 2 for item in media.values())
    minimum = min(4096, MEDIA_BUDGET // max(1, len(media)))
    remainder = MEDIA_BUDGET - minimum * len(media)
    encoded, adjusted = {}, 0
    prefix = 'data:image/webp;base64,'
    for key, item in media.items():
        # 各份额向下取整，连同 base64 放大后仍不超过总预算。
        allowance = min(1024 * 1024, minimum + remainder * item['side'] ** 2 // weight)
        raw_budget = (allowance - len(prefix)) // 4 * 3
        if raw_budget < 256:
            raise ValueError('缩略图预算不足以保留全部图片；未发布部分案例')
        path = Path(item['cache']) / f'webp1_{item["side"]}_{key}.webp'
        try:
            if path.exists() and path.stat().st_size <= 1024 * 1024:
                raw = path.read_bytes()
            else:
                url = positive_image_data_url(item['ref'])
                with Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))) as original:
                    original.thumbnail((item['side'], item['side']))
                    buffer = io.BytesIO()
                    original.convert('RGB').save(buffer, format='WEBP', quality=78, method=4)
                    raw = buffer.getvalue()
                # 缓存仅保存固定编码，预算变化不会增加缓存变体；独立临时文件允许并发查看。
                if len(raw) <= 1024 * 1024:
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(raw)
                        temporary.replace(path)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
            with Image.open(io.BytesIO(raw)) as original:
                im = original.convert('RGB')
            changed = len(raw) > raw_budget
            quality = 78
            while len(raw) > raw_budget:
                if quality > 50:
                    quality = max(50, quality - 14)
                elif max(im.size) > 64:
                    side = max(64, int(max(im.size) * .8))
                    im.thumbnail((side, side))
                else:
                    raise ValueError('缩略图预算不足以保留全部图片；未发布部分案例')
                buffer = io.BytesIO()
                im.save(buffer, format='WEBP', quality=quality, method=4)
                raw = buffer.getvalue()
            value = prefix + base64.b64encode(raw).decode()
            encoded[key] = {'src': value, 'uri': item['ref']['uri'],
                            'width': im.width, 'height': im.height}
            adjusted += changed
        except (OSError, ValueError) as error:
            if '预算' in str(error):
                raise
            encoded[key] = {'error': str(error), 'uri': item['ref'].get('uri')}
    size = sum(len(item.get('src', '')) for item in encoded.values())
    if size > MEDIA_BUDGET:
        raise ValueError('合并查看缩略图超过总预算')
    return encoded, {'unique_images': len(encoded), 'encoded_bytes': size,
                     'budget_bytes': MEDIA_BUDGET, 'adjusted_previews': adjusted,
                     'image_errors': sum('error' in item for item in encoded.values())}


def _build_case_payload(project, run_id, cache_run_id=None, media=None):
    """只读固定快照；最多300题×8路，完整压缩HTML64MiB，媒体40MiB。"""
    project = Path(project)
    root = project.parent
    owner = project / 'evaluation/t2i/v2'
    records = RunTables(root, f'demiwtg/evaluation/t2i/v2/datasets/records__{run_id}.lance')
    manifest = records.load_manifest()
    if not manifest:
        raise ValueError('本轮尚未提交；没有运行manifest，不能读旧批次代替')
    if manifest.get('scoring_scheme') == 'D':
        return _build_d_case_payload(project, run_id, manifest, cache_run_id=cache_run_id, media=media)
    state = records.load() or {}
    settings = manifest['config']
    if manifest['expected_questions'] > 300 or len(settings['answers']) > 8:
        raise ValueError('完整浏览快照超过300题×8路；请显式缩小查看范围')
    columns = ['task_id', 'concept', 'instruction', 'taxonomy', 'test_points',
               'authoring_variant', 'authoring_images_json']
    if any(model.get('view_scores_from') for model in settings['answers']):
        columns += ['question_revision', 'requirements_json', 'review_status']
    scope = data.read_lance(**manifest['source'], columns=columns).take(301)
    if len(scope) > 300:
        raise ValueError('浏览题数超预算；不发布截断快照')
    control = root / '_demiflow/evaluation_t2i_v2' / run_id
    pause_path = control / 'pause_requested.json'
    pause = json.loads(pause_path.read_text()) if pause_path.exists() else None
    cache = root / '_demiflow/evaluation_t2i_v2' / (cache_run_id or run_id) / 'thumbnails'
    cache.mkdir(parents=True, exist_ok=True)
    if media is None:
        media = {}
    def preview(ref, side):
        # 同一实际图像在作答、A/B 输入及其他路中只编码一次，使用所需最大尺寸。
        key = ref['sha256']
        if key in media:
            media[key]['side'] = max(side, media[key]['side'])
            return key
        if len(media) >= MAX_MEDIA_ITEMS:
            raise ValueError('查看图片超过32768个唯一对象；未发布部分案例')
        media[key] = {'ref': ref, 'side': side, 'cache': str(cache)}
        return key
    by_arm, summaries, sources = [], [], {}
    for index, model in enumerate(settings['answers']):
        name = f'{run_id}__arm{index:02d}'
        stages = {}
        for stage in ('answers', 'scores_a', 'scores_b'):
            uri = owner / 'datasets' / f'{stage}__{name}.lance'
            if not uri.exists():
                stages[stage] = {}
                continue
            ref = {'uri': str(uri), 'version': lance.dataset(str(uri)).version}
            sources[f'{index}:{stage}'] = ref
            columns = ['task_id', 'status', 'reason', 'image_json', 'reference_image_count', 'generation_seconds']
            if stage != 'answers':
                columns += ['a_status','a_reason','a_json','a_score','alignment_score','quality_score',
                    'aesthetics_score','a_valid_items_json','b_status','b_reason','b_json','b_score','b_raw_score','b_verdict']
            available = lance.dataset(str(uri), version=ref['version']).schema.names
            columns += [k for k in ('answer_model','answer_mode','answer_call_json','a_call_json','b_call_json')
                        if k in available and k not in columns]
            rows = data.read_lance(**ref, columns=columns).take(301)
            if len(rows) > 300:
                raise ValueError('本路结果超过浏览题数上限')
            stages[stage] = {r['task_id']: r for r in rows}
        if model['answer_mode'] == 'imagerag':
            # 分阶段尚未生成最终答案时，也能查看已提交诊断；不发起补跑或读取图库全文。
            stages['rag'] = {}
            for stage in ('rag_diagnoses', 'rag_inputs'):
                uri = owner / 'datasets' / f'{stage}__{name}.lance'
                if not uri.exists():
                    continue
                ref = {'uri': str(uri), 'version': lance.dataset(str(uri)).version}
                sources[f'{index}:{stage}'] = ref
                rows = data.read_lance(**ref).take(301)
                if len(rows) > 300:
                    raise ValueError('ImageRAG查看超过300题上限')
                for r in rows:
                    if stage == 'rag_inputs':
                        trace = json.loads(r['rag_json'])
                    else:
                        trace = {'status': r['rag_status'], 'reason': r['rag_reason'],
                            'initial_answer': json.loads(r['initial_answer_json'] or 'null'),
                            'fallback_prompt': r['fallback_prompt'],
                            'prompt_protocol': model['imagerag'].get('prompt_protocol', 'adapted_json_v1'),
                            'concept_attempts': json.loads(r.get('concept_attempts_json') or '[]'),
                            'stages': {s: {'result': json.loads(r[s+'_json'] or 'null'),
                                           'call': json.loads(r[s+'_call_json'] or 'null')}
                                       for s in ('decision', 'concepts', 'captions')}}
                    stages['rag'][r['task_id']] = trace
        by_arm.append(stages)
        a_rows, b_rows = stages['scores_a'].values(), stages['scores_b'].values()
        a_values = [r['a_score'] for r in a_rows if r['a_score'] is not None]
        b_values = [r['b_score'] for r in b_rows if r['b_score'] is not None]
        label = {'positive_images': ' · 正例图', 'imagerag': ' · ImageRAG检索图',
                 'legacy_references': ' · 参考信息', 'text_only': ' · 无图'}[model['answer_mode']]
        summaries.append({'index': index, 'label': model['model'] + label,
            **({'imagerag': dict(Counter(r['status'] for r in stages['rag'].values()))} if 'rag' in stages else {}),
            'paused': bool(pause), 'pause_reason': pause.get('reason') if pause else None,
            'expected': len(scope), 'generation': dict(Counter(r['status'] for r in stages['answers'].values())),
            'a': dict(Counter(r['a_status'] for r in a_rows)), 'b': dict(Counter(r['b_status'] for r in b_rows)),
            'b_verdicts': dict(Counter(r['b_verdict'] for r in b_rows if r.get('b_verdict'))),
            'a_mean': sum(a_values) / len(a_values) if a_values else None,
            'b_mean': sum(b_values) / len(b_values) if b_values else None,
            'a_valid': len(a_values), 'b_valid': len(b_values)})
    cases = []
    text_size = 0
    for rank, question in enumerate(scope, 1):
        task_id = question['task_id']
        case = {k: question.get(k) for k in ('task_id','concept','instruction','taxonomy','test_points')}
        case.update(rank=rank, arms=[], references=[])
        for index, stages in enumerate(by_arm):
            row = {**stages['answers'].get(task_id, {}), **stages['scores_a'].get(task_id, {}),
                   **stages['scores_b'].get(task_id, {})}
            arm = {'index': index, 'label': summaries[index]['label'], **row}
            if row.get('image_json'):
                arm['image'] = preview(json.loads(row['image_json']), 640)
            for key in ('a_json', 'b_json', 'a_valid_items_json'):
                arm[key.removesuffix('_json') + '_result'] = json.loads(arm.pop(key, None) or 'null')
            arm['input'] = answer_view(root, run_id, index, settings['answers'][index], question, row, preview)
            for standard in ('a','b'):
                arm[standard+'_input'] = judge_view(row.get(standard+'_call_json'), row.get('image_json'), root, preview)
            if trace := stages.get('rag', {}).get(task_id):
                if trace.get('concept_attempts') and trace['stages']['concepts']['result'] is not None:
                    trace['stages']['concepts']['result']['attempts'] = trace['concept_attempts']
                arm['rag'] = trace
                initial = trace.get('initial_answer') or {}
                if initial.get('image_json'):
                    arm['initial_image'] = preview(json.loads(initial['image_json']), 640)
                for step, details in trace['stages'].items():
                    arm['rag_'+step+'_input'] = judge_view(json.dumps(details['call']),
                        initial.get('image_json'), root, preview,
                        positive_preprocessing=trace.get('prompt_protocol') != 'upstream_16c9502')
            case['arms'].append(arm)
        for item in json.loads(question.get('authoring_images_json') or '[]'):
            case['references'].append(preview(item['object_ref'], 128))
        text_size += len(json.dumps(case, ensure_ascii=False).encode())
        if text_size > 64 * 1024 * 1024:
            raise ValueError('查看文字超过64MiB；未截断或发布部分题目')
        cases.append(case)
    meta = {'run': run_id, 'updated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'active': run_is_active(control), 'phase': 'paused' if pause else state.get('phase', 'starting'),
            'pause': pause,
            'complete': state.get('complete', False), 'expected': len(scope) * len(by_arm),
            'questions': len(scope), 'source': manifest['source'], 'sources': sources,
            'arms': summaries, 'processes': state.get('arms', []), 'judge': settings.get('judge', {}).get('model', '本轮仅作答'),
            'reasoning_effort': settings.get('judge', {}).get('reasoning_effort')}
    payload = {'meta': meta, 'cases': cases, 'media': media}
    if any(model.get('view_scores_from') for model in settings['answers']):
        _attach_existing_scores(payload, settings['answers'], scope, root, preview)
    return payload


def _attach_existing_scores(payload, models, questions, root, preview):
    """只读附加固定历史 D7 评分；核对同题同图，不提交新评分或重写旧表。"""
    from .d_evaluation import summarize, paired_summary
    from .d_rubric import frozen_requirements
    from .codex_judging import input_view
    meta, cases = payload['meta'], payload['cases']
    by_id = {q['task_id']: q for q in questions}
    rows_by_arm, judges = [], set()
    for index, model in enumerate(models):
        ref = model.get('view_scores_from')
        rows = []
        if ref:
            if set(ref) != {'uri', 'version'} or type(ref['version']) is not int or ref['version'] < 1:
                raise ValueError('Viewing existing scores requires a fixed table reference')
            rows = data.read_lance(**ref).filter(lambda r: r['task_id'] in by_id
                and r['answer_mode'] == model['answer_mode']).take(len(questions) + 1)
            if len(rows) != len(questions) or len({r['task_id'] for r in rows}) != len(rows):
                raise ValueError('Existing scores do not cover the exact question scope')
            meta['sources'][f'{index}:view_scores'] = ref
        scored = {r['task_id']: r for r in rows}
        for case in cases:
            arm, question = case['arms'][index], by_id[case['task_id']]
            score = scored.get(case['task_id'])
            if score:
                if (score['instruction'] != question['instruction'] or score.get('d_protocol') != 'd7'
                    or json.loads(score.get('image_json') or 'null') != json.loads(arm.get('image_json') or 'null')
                    or score.get('question_revision') != question.get('question_revision')
                    or score['answer_model'] != arm.get('answer_model')):
                    raise ValueError('Existing score does not match the actual question, model and image')
                # 既有同批 subagent 评分使用过这两个明确的记录名；原行仍保留原名。
                judges.add('codex/gpt-6-astra' if score['judge_model'] in
                    {'codex/gpt-6-astra', 'codex-subagent/gpt-6-astra'} else score['judge_model'])
                # 保留本轮实际生图输入，评分字段从固定来源读取，不伪造本轮评分调用。
                arm.update({k: v for k, v in score.items() if k.startswith('d_') or k == 'judge_model'})
                arm.update(scoring_scheme='D', d_single_pass=True,
                    d_result=json.loads(arm.pop('d_json', None) or 'null'),
                    d_metrics_result=json.loads(arm.pop('d_metrics_json', None) or 'null'),
                    core_requirements=frozen_requirements(question),
                    d_input=input_view(score, root, preview))
            elif not ref:
                arm.update(scoring_scheme='D', d_single_pass=True, d_status='not_requested',
                    d_reason='本轮仅生成 ImageRAG 作答，尚未提交 D7 评分。',
                    d_result=None, d_metrics_result={}, core_requirements=frozen_requirements(question))
            arm['generation_model'] = model['model']
        summary = meta['arms'][index]
        summary.update(summarize(rows, len(questions)), generation_model=model['model'])
        rows_by_arm.append(rows)
    if len(judges) != 1:
        raise ValueError('Existing score comparison requires exactly one judge')
    paired = {}
    for name in {m['model'] for m in models}:
        arms = {m['answer_mode']: rows_by_arm[i] for i, m in enumerate(models) if m['model'] == name}
        if 'text_only' in arms and 'positive_images' in arms:
            paired[name] = paired_summary([arms['text_only'], arms['positive_images']])
    meta.update(scoring_scheme='D', prompt_version='t2i-v2-d-judge-7-review',
        judge=next(iter(judges)), reasoning_effort='xhigh', paired_by_model=paired,
        paired_dimensions=next(iter(paired.values()), {}),
        score_note='历史两路 D7 原样复用；ImageRAG 本轮仅生成，未提交评分，空分不计为0。')


def _build_d_case_payload(project, run_id, manifest, *, cache_run_id=None, media=None):
    """在同一查看框架展示 D 小批比较及真实请求，保持标准暂停和媒体预算。"""
    from .d_evaluation import summarize, paired_summary
    root, owner = project.parent, project / 'evaluation/t2i/v2'
    limit = manifest['expected_questions']
    if not 1 <= limit <= 300:
        raise ValueError('D viewer question budget exceeded')
    scope = data.read_lance(**manifest['source']).take(limit + 1)
    if len(scope) != limit:
        raise ValueError('D viewer scope differs from frozen sample')
    records = RunTables(root, f'demiwtg/evaluation/t2i/v2/datasets/records__{run_id}.lance')
    state = records.load() or {}
    cfg = manifest['config']
    control = root / '_demiflow/evaluation_t2i_v2' / run_id
    pause_path = control / 'pause_requested.json'
    pause = json.loads(pause_path.read_text()) if pause_path.exists() else None
    cache = root / '_demiflow/evaluation_t2i_v2' / (cache_run_id or run_id) / 'thumbnails'
    cache.mkdir(parents=True, exist_ok=True)
    media = {} if media is None else media
    def preview(ref, side):
        key = ref['sha256']
        if key in media:
            media[key]['side'] = max(side, media[key]['side'])
        else:
            if len(media) >= MAX_MEDIA_ITEMS:
                raise ValueError('D viewer media item budget exceeded')
            media[key] = {'ref': ref, 'side': side, 'cache': str(cache)}
        return key
    sources = {}
    def read_live(name):
        uri = owner / 'datasets' / name
        if not uri.exists():
            return []
        ref = {'uri': str(uri), 'version': lance.dataset(str(uri)).version}
        sources[name] = ref
        rows = data.read_lance(**ref).take(limit + 1)
        if len(rows) > limit:
            raise ValueError('D viewer rows exceed frozen sample')
        return rows
    single_pass = 'core_prompt_pack' not in cfg['d_evaluation']
    core_rows = [] if single_pass else read_live(f'd_core__{run_id}.lance')
    core = {r['task_id']: r for r in core_rows}
    arm_rows = [read_live(f'scores_d__{run_id}__arm{i:02d}.lance') for i in range(2)]
    from .codex_judging import read_comparison, input_view
    scored = [{r['task_id']: r for r in rows} for rows in arm_rows]
    ids = {r['task_id'] for r in scope}
    answers, rag_traces = [], []
    summaries = []
    for i, model in enumerate(cfg['answers']):
        ref = {**model['source'], 'uri': str(root / model['source']['uri'])}
        refs = [ref, *[item['source'] for item in model.get('additional_sources', [])]]
        source_rows = []
        for item in refs:
            source_rows.extend(data.read_lance(**item).filter(lambda r: r['task_id'] in ids).take(limit + 1))
        if len(source_rows) > limit:
            raise ValueError('D source answer identities are duplicated')
        answers.append({r['task_id']: r for r in source_rows})
        traces = {}
        if model.get('rag_inputs'):
            trace_ref = model['rag_inputs']
            sources[f'rag_arm{i}'] = trace_ref
            trace_rows = data.read_lance(**trace_ref).filter(lambda r: r['task_id'] in ids).take(limit + 1)
            if len(trace_rows) != limit or len({r['task_id'] for r in trace_rows}) != len(trace_rows):
                raise ValueError('D viewer RAG inputs are missing or duplicated')
            traces = {r['task_id']: json.loads(r['rag_json']) for r in trace_rows}
        rag_traces.append(traces)
        sources[f'answer_arm{i}'] = ref
        generation_model = model['model'].removesuffix('+正例参考图').removesuffix('+ImageRAG')
        summaries.append({'index': i, 'generation_model': generation_model,
                          'answer_mode': model['answer_mode'],
                          'label': generation_model + ' · ' + {'text_only': '无图', 'positive_images': '正例图', 'imagerag': 'ImageRAG检索图'}[model['answer_mode']],
                          'paused': bool(pause), **summarize(arm_rows[i], limit)})
    from .d_rubric import frozen_requirements
    peer_run = cfg['d_evaluation'].get('codex_run')
    base_rows = ([{**answers[i].get(q['task_id'], {}), 'task_id': q['task_id'],
                  'instruction': q['instruction'], 'question_revision': q.get('question_revision'),
                  'd_protocol': 'd7', 'core_requirements_json': json.dumps(frozen_requirements(q), ensure_ascii=False),
                  **scored[i].get(q['task_id'], {})}
                 for i in range(2) for q in scope] if peer_run else [r for arm in arm_rows for r in arm])
    codex_rows, codex_summary = read_comparison(root, run_id, base_rows, peer_run=peer_run)
    cases = []
    for rank, question in enumerate(scope, 1):
        key = question['task_id']
        case = {name: question.get(name) for name in ('task_id', 'concept', 'instruction', 'taxonomy', 'test_points', 'question_revision')}
        case.update(rank=rank, arms=[], references=[])
        for i, model in enumerate(cfg['answers']):
            row = {**answers[i].get(key, {}), **scored[i].get(key, {})}
            prepared = core.get(key, {})
            arm = {**row, 'index': i, 'label': summaries[i]['label'], 'scoring_scheme': 'D',
                   'generation_model': summaries[i]['generation_model'],
                   'd_single_pass': single_pass, 'core_status': prepared.get('core_status'), 'core_reason': prepared.get('core_reason')}
            for field in ('d_json', 'd_metrics_json'):
                arm[field.removesuffix('_json') + '_result'] = json.loads(arm.pop(field, None) or 'null')
            arm['core_plan'] = json.loads(prepared.get('core_plan_json') or 'null')
            arm['core_requirements'] = json.loads((row if single_pass else prepared).get('core_requirements_json') or 'null')
            if arm['core_requirements'] is None and question.get('requirements_json'):
                arm['core_requirements'] = frozen_requirements(question)
            if row.get('image_json'):
                arm['image'] = preview(json.loads(row['image_json']), 640)
            arm['input'] = (answer_view(root, run_id, i, model, question, row, preview) if row.get('answer_call_json')
                            else {'available': False, 'note': '该答案未保存实际输入记录，未用模板补写。', 'images': []})
            arm['d_input'] = (input_view(row, root, preview) if str(row.get('judge_model', '')).startswith('codex')
                              else judge_view(row.get('d_call_json'), row.get('image_json'), root, preview))
            if trace := rag_traces[i].get(key):
                initial = trace.get('initial_answer') or {}
                if (initial.get('instruction') != question['instruction']
                        or initial.get('answer_model') != summaries[i]['generation_model']):
                    raise ValueError('D viewer RAG initial question/model differs')
                if trace.get('status') == 'keep_initial' and initial.get('image_json') != row.get('image_json'):
                    raise ValueError('D viewer kept initial image differs from the scored image')
                arm['rag'] = trace
                if initial.get('image_json'):
                    arm['initial_image'] = preview(json.loads(initial['image_json']), 640)
                for step, details in trace['stages'].items():
                    arm['rag_' + step + '_input'] = judge_view(json.dumps(details['call']),
                        initial.get('image_json'), root, preview,
                        positive_preprocessing=trace.get('prompt_protocol') != 'upstream_16c9502')
            if codex_summary is not None:
                comparison = codex_rows.get((key, model['answer_mode']))
                arm['codex'] = ({'status': comparison['d_status'], 'reason': comparison.get('d_reason'),
                    'result': json.loads(comparison.get('d_json') or 'null'),
                    'metrics': json.loads(comparison.get('d_metrics_json') or 'null'),
                    'call': json.loads(comparison.get('d_call_json') or 'null')}
                    if comparison else {'status': 'not_requested', 'result': None, 'metrics': None})
                arm['codex_input'] = input_view(comparison, root, preview) if comparison else None
            if not single_pass:
                arm['core_input'] = judge_view(prepared.get('core_call_json'), None, root, preview)
            case['arms'].append(arm)
        for item in json.loads(question.get('authoring_images_json') or '[]'):
            case['references'].append(preview(item['object_ref'], 128))
        cases.append(case)
    prompt_version = cfg['d_evaluation']['judge_prompt_pack']['prompts']['judge_d']['version']
    meta = {'run': run_id, 'scoring_scheme': 'D', 'prompt_version': prompt_version,
            'updated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'active': run_is_active(control), 'phase': state.get('phase', 'starting'), 'pause': pause,
            'complete': state.get('complete', False), 'expected': limit * 2, 'questions': limit,
            'source': manifest['source'], 'sources': sources, 'arms': summaries,
            'core_states': dict(Counter(r['core_status'] for r in core_rows)),
            'paired_dimensions': paired_summary(arm_rows), 'judge': cfg['judge']['model'],
            'reasoning_effort': cfg['judge'].get('reasoning_effort'), 'codex_comparison': codex_summary,
            'paired_by_model': {summaries[0]['generation_model']: paired_summary(arm_rows)}, 'processes': []}
    if codex_summary:
        for arm in codex_summary['arms']:
            arm['generation_model'] = summaries[0]['generation_model']
    return {'meta': meta, 'cases': cases, 'media': media}


def _mode_comparisons(cases):
    """Each dimension uses its own common valid cohort within one generation model."""
    from .d_evaluation import DIMENSIONS, paired_summary
    result = {}
    models = sorted({a.get('generation_model', a.get('answer_model')) for c in cases for a in c['arms']})
    for model in models:
        by_mode = {}
        for case in cases:
            for arm in case['arms']:
                if arm.get('generation_model', arm.get('answer_model')) == model:
                    by_mode.setdefault(arm['answer_mode'], []).append(arm)
        pairs = {}
        for name, left, right in [('positive_text', 'text_only', 'positive_images'),
                                  ('rag_positive', 'positive_images', 'imagerag'),
                                  ('rag_text', 'text_only', 'imagerag')]:
            if left in by_mode and right in by_mode:
                pairs[name] = {'left': left, 'right': right,
                               'dimensions': paired_summary([by_mode[left], by_mode[right]])}
        modes = ('text_only', 'positive_images', 'imagerag')
        maps = {mode: {a['task_id']: a for a in by_mode.get(mode, [])} for mode in modes}
        triple = {}
        for dimension, field in DIMENSIONS.items():
            ids = [c['task_id'] for c in cases if all(
                maps[m].get(c['task_id'], {}).get(field) is not None for m in modes)]
            means = {m: sum(maps[m][k][field] for k in ids) / len(ids) if ids else None for m in modes}
            triple[dimension] = {'valid': len(ids), 'means': means}
        result[model] = {'pairs': pairs, 'three_modes': triple}
    return result


def _denoise_log_samples(text):
    """Pair serial progress loops with server receipts; never infer task identities.

    tqdm prints the completed bar twice. PNG serialization can overlap the next
    loop, so a loop start must not discard a completed but unacknowledged loop.
    Ambiguous or incomplete receipts are excluded and counted.
    """
    pattern = re.compile(
        r'(?P<bar>(?P<percent>\d+)%\|[^|\n]*\|\s*(?P<step>\d+)/(?P<total>\d+)\s+\[(?P<elapsed>[\d:]+)[<\]])'
        r'|(?P<serve>\[serve\] t2i ok (?P<size>\d+x\d+) seed=\S+ model=(?P<model>\S+) steps=(?P<steps>\d+) (?P<server>[\d.]+)s)'
        r'|(?P<http>"POST /v1/images/(?P<route>generations|edits) HTTP/[^"\s]+" (?P<status>\d+))')
    completed, loops, receipts, samples = None, [], [], []
    counts = Counter()

    def flush():
        nonlocal completed
        if completed is not None:
            loops.append(completed)
            counts['completed_loops'] += 1
            completed = None

    for line, value in enumerate(text.splitlines(), 1):
        for event in pattern.finditer(value):
            if event['bar']:
                total, step = int(event['total']), int(event['step'])
                if total not in (40, 49):
                    continue  # Loading progress is not image denoising.
                if step == total:
                    seconds = 0
                    for component in event['elapsed'].split(':'):
                        seconds = seconds * 60 + int(component)
                    if completed is not None:
                        counts['duplicate_completed_bars'] += 1
                    completed = {'denoise_s': seconds, 'loop_steps': total, 'progress_line': line}
                else:
                    flush()
            elif event['serve']:
                flush()
                counts['server_successes'] += 1
                configured = int(event['steps'])
                if len(loops) == 1 and loops[0]['loop_steps'] == (49 if configured == 50 else configured):
                    receipts.append({**loops[0], 'model': event['model'], 'configured_steps': configured,
                                     'size': event['size'], 'service_wall_s': float(event['server']),
                                     'server_line': line})
                else:
                    counts['ambiguous_server_receipts'] += 1
                loops.clear()
            else:
                if event['status'] == '200':
                    counts['http_successes'] += 1
                    if len(receipts) == 1:
                        samples.append({**receipts[0], 'route': event['route'], 'http_line': line})
                    else:
                        counts['unmatched_http_successes'] += 1
                    receipts.clear()
                elif receipts:
                    counts['ambiguous_http_receipts'] += len(receipts)
                    receipts.clear()
    flush()
    counts['unmatched_completed_loops'] = len(loops)
    counts['unmatched_server_receipts'] = len(receipts)
    return {'samples': samples, 'counts': dict(counts)}


def _denoise_timing(groups, root):
    """Read only logs named by immutable original generation manifests.

    A log has no task/request ID. Its batch statistics therefore cannot be
    relabelled as timings of the selected final question cohort.
    """
    bindings, missing, manifests = {}, set(), {}
    for group in groups:
        for event in group['stages']['generation']['call_evidence']:
            ref = event.get('request_ref') or {}
            match = re.fullmatch(r'calls_answers__(.+)__arm(\d+)(?:__[0-9a-f]+)?\.sqlite', Path(ref.get('journal_path', '')).name)
            if not match:
                missing.add(event['identity'])
                continue
            run, arm_index = match[1], int(match[2])
            manifest_path = root / '_demiflow/run_manifests/demiwtg/evaluation/t2i/v2/datasets' / ('records__'+run+'.json')
            try:
                if manifest_path not in manifests:
                    raw = manifest_path.read_bytes()
                    manifests[manifest_path] = (json.loads(raw), hashlib.sha256(raw).hexdigest())
                manifest, manifest_sha = manifests[manifest_path]
                model = manifest['config']['answers'][arm_index]
                service = ((model.get('shared_service') or {}).get('configuration') or model['service'])
                path = Path(service['log_path'])
                if not path.is_absolute():
                    path = Path(service.get('root', root)) / path
                mode = model['answer_mode']
                identity = (str(path), group['generation_model'], mode)
                binding = bindings.setdefault(identity, {'path': path, 'generation_model': group['generation_model'],
                    'answer_mode': mode, 'runs': {}, 'selected_calls': set()})
                binding['runs'][run] = {'path': str(manifest_path), 'sha256': manifest_sha,
                                        'expected_questions': manifest.get('expected_questions')}
                binding['selected_calls'].add(event['identity'])
            except (OSError, KeyError, ValueError, IndexError, TypeError):
                missing.add(event['identity'])
    cache, rows = {}, []
    for binding in bindings.values():
        path = binding['path']
        if path not in cache:
            try:
                raw = path.read_bytes()
                cache[path] = {**_denoise_log_samples(raw.decode(errors='replace')),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'error': None}
            except OSError as error:
                cache[path] = {'samples': [], 'counts': {}, 'error': str(error), 'sha256': None, 'bytes': None}
        log = cache[path]
        route = 'generations' if binding['answer_mode'] == 'text_only' else 'edits'
        # If positive and RAG requests share a log, the endpoint cannot separate them.
        ambiguous = len({b['answer_mode'] for b in bindings.values() if b['path'] == path
                         and b['answer_mode'] != 'text_only'}) > 1 and route == 'edits'
        samples = [] if ambiguous else [s for s in log['samples']
            if s['model'] == binding['generation_model'] and s['route'] == route]
        values = sorted(s['denoise_s'] for s in samples)
        def percentile(fraction):
            if not values:
                return None
            position = (len(values)-1)*fraction
            lo, hi = math.floor(position), math.ceil(position)
            return values[lo] + (values[hi]-values[lo])*(position-lo)
        rows.append({k: v for k,v in binding.items() if k not in ('path', 'selected_calls')} | {
            'log_path': str(path), 'log_sha256': log['sha256'], 'log_bytes': log['bytes'],
            'log_counts': log['counts'], 'error': log['error'], 'ambiguous_modes': ambiguous,
            'selected_generation_calls': len(binding['selected_calls']), 'samples': len(values),
            'mean_s': math.fsum(values)/len(values) if values else None,
            'p50_s': percentile(.5), 'p95_s': percentile(.95),
            'configured_steps': sorted({s['configured_steps'] for s in samples}),
            'sample_evidence': samples, 'exact_question_binding': False})
    return {'groups': rows, 'missing_source_calls': len(missing), 'strict_gpu_s': None,
        'basis': '去噪循环墙钟来自服务日志的tqdm完成条，已排除启动与请求锁排队；不包含循环前的文本/参考图编码、循环后的解码保存，也不是CUDA事件测得的纯GPU时间。日志精度为整数秒。',
        'cohort_note': '按历史服务批次分组。日志没有题目/请求ID，不能精确绑定最终299题；原299题批次与91道改题批次分别展示，不混为最终题集均值。RAG的保留初图不计作再次生成。'}


def _timing_summary(cases):
    """Historical request wall times, deduplicated by original call, never cache lookup time.

    Embeddings are timed per native batch. Missing retrieval/worker timing is not
    reconstructed from file timestamps or divided by configured concurrency.
    """
    from demiflow.operator_llm.call_ref import read_call
    from project import resolve_root
    labels = {'initial_generation': ('初图生成', '张'), 'decision': ('缺点诊断', '次'),
              'concepts': ('概念提取（含链内尝试）', '次'), 'captions': ('检索描述生成', '次'),
              'embedding': ('查询编码', '批'), 'retrieval': ('向量检索', '查询'),
              'generation': ('本路生图 / RAG再生成', '张'), 'judge': ('D7判分原调用', '次')}
    groups, duration_cache, model_cache = {}, {}, {}

    def number(value):
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

    def parse(value):
        parsed = json.loads(value or '{}') if isinstance(value, str) else value
        return parsed if isinstance(parsed, dict) else {}

    def events(call, fallback, identity):
        call = parse(call)
        attempts = call.get('attempts') or [call]
        result = []
        for index, attempt in enumerate(attempts):
            ref = attempt.get('request_ref') or call.get('request_ref')
            token = json.dumps(ref, sort_keys=True) if ref else identity + ':' + str(index)
            seconds = next((number(attempt[k]) for k in ('seconds', 'elapsed_s')
                            if number(attempt.get(k)) is not None), None)
            # Some historical adapters recorded the cheap cache hit instead of
            # the inference. The saved original response is the authoritative fallback.
            if seconds is None or (attempt.get('reused') and seconds == 0):
                response_ref = attempt.get('response_ref') or call.get('response_ref')
                if response_ref:
                    response_key = json.dumps(response_ref, sort_keys=True)
                    if response_key not in duration_cache:
                        try:
                            response = read_call(response_ref, resolve_root())
                            duration_cache[response_key] = next((number(response.get(k))
                                for k in ('seconds', 'elapsed_s')
                                if isinstance(response, dict) and number(response.get(k)) is not None), None)
                        except (OSError, KeyError, ValueError, sqlite3.Error):
                            duration_cache[response_key] = None
                    seconds = duration_cache[response_key]
                if seconds is None and len(attempts) == 1 and not (attempt.get('reused') and number(fallback) == 0):
                    seconds = number(fallback)
            result.append({'identity': token, 'seconds': seconds,
                           'request_ref': ref, 'duration_source': 'saved_call_or_response',
                           'model': attempt.get('model') or call.get('model')})
        return result

    for case in cases:
        for arm in case['arms']:
            model = arm.get('generation_model') or arm.get('answer_model', '').removesuffix('+正例参考图').removesuffix('+ImageRAG')
            mode = arm.get('answer_mode')
            if not model or mode not in ('text_only', 'positive_images', 'imagerag'):
                continue
            key = model, mode
            group = groups.setdefault(key, {'generation_model': model, 'answer_mode': mode,
                'questions': 0, 'keep_initial_questions': 0, 'judge_reused_initial': 0,
                'generation_parameters': [],
                'stages': {name: {'events': {}, 'question_ids': set(), 'skipped_questions': 0}
                           for name in labels if mode == 'imagerag' or name in ('generation', 'judge')}})
            group['questions'] += 1
            actual_parameters = (arm.get('input') or {}).get('parameters') or {}
            parameters = {k: actual_parameters[k] for k in ('num_inference_steps', 'size',
                'output_resolution', 'seed', 'guidance_scale') if k in actual_parameters}
            if parameters and parameters not in group['generation_parameters']:
                group['generation_parameters'].append(parameters)
            task = case['task_id']

            def add(stage, call=None, fallback=None, *, skip=False, identity=None):
                bucket = group['stages'][stage]
                if skip:
                    bucket['skipped_questions'] += 1
                    return
                bucket['question_ids'].add(task)
                for event in events(call, fallback, identity or task + ':' + stage):
                    if not event['model'] and stage in ('initial_generation', 'generation'):
                        event['model'] = model
                    if not event['model'] and stage == 'embedding' and event['request_ref']:
                        token = event['identity']
                        if token not in model_cache:
                            try:
                                request = read_call(event['request_ref'], resolve_root())
                                body = request.get('body') if isinstance(request, dict) else None
                                model_cache[token] = body.get('model') if isinstance(body, dict) else None
                            except (OSError, KeyError, ValueError, sqlite3.Error):
                                model_cache[token] = None
                        event['model'] = model_cache[token]
                    previous = bucket['events'].get(event['identity'])
                    if previous and previous['seconds'] != event['seconds']:
                        raise ValueError('Historical timing differs for the same call')
                    bucket['events'][event['identity']] = event

            trace = arm.get('rag') or {}
            kept = mode == 'imagerag' and trace.get('status') == 'keep_initial'
            group['keep_initial_questions'] += kept
            if mode == 'imagerag':
                initial = trace.get('initial_answer') or {}
                add('initial_generation', initial.get('answer_call_json'), initial.get('generation_seconds'))
                stages = trace.get('stages') or {}
                for stage in ('decision', 'concepts', 'captions'):
                    calls = ([a.get('call') for a in trace.get('concept_attempts', [])]
                             if stage == 'concepts' and trace.get('concept_attempts') else
                             [(stages.get(stage) or {}).get('call')])
                    if kept and stage != 'decision':
                        add(stage, skip=True)
                    else:
                        for i, call in enumerate(calls):
                            add(stage, call, identity=task + ':' + stage + ':' + str(i))
                hits = trace.get('retrievals') or []
                if kept:
                    add('embedding', skip=True)
                    add('retrieval', skip=True)
                elif hits:
                    for hit in hits:
                        query = hit.get('query_id') or task + ':' + str(hit.get('concept_index'))
                        add('embedding', hit.get('embedding_call_json'), identity=query + ':embedding')
                        add('retrieval', identity=query + ':retrieval')
                else:
                    add('embedding')
                    add('retrieval')
            add('generation', arm.get('answer_call_json'), arm.get('generation_seconds'), skip=kept)
            judge_call = parse(arm.get('d_call_json'))
            group['judge_reused_initial'] += bool(judge_call.get('score_reuse'))
            add('judge', judge_call)

    def percentile(values, fraction):
        if not values:
            return None
        position = (len(values) - 1) * fraction
        lo, hi = math.floor(position), math.ceil(position)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    for group in groups.values():
        for stage, bucket in group['stages'].items():
            calls = list(bucket['events'].values())
            values = sorted(c['seconds'] for c in calls if c['seconds'] is not None)
            group['stages'][stage] = {'label': labels[stage][0], 'unit': labels[stage][1],
                'models': sorted({c['model'] for c in calls if isinstance(c['model'], str) and c['model']}),
                'calls': len(calls), 'timed_calls': len(values), 'missing_calls': len(calls)-len(values),
                'applicable_questions': len(bucket['question_ids']), 'skipped_questions': bucket['skipped_questions'],
                'sum_s': math.fsum(values) if values else None,
                'mean_s': math.fsum(values)/len(values) if values else None,
                'p50_s': percentile(values, .5), 'p95_s': percentile(values, .95),
                'call_evidence': calls}
        measured = [v for k,v in group['stages'].items() if k not in ('judge', 'retrieval')]
        group['recorded_answer_model_call_sum_s'] = (math.fsum(v['sum_s'] or 0 for v in measured)
            if any(v['timed_calls'] for v in measured) else None)
        group['missing_answer_model_timing_calls'] = sum(v['missing_calls'] for v in measured)
        group['recorded_answer_model_call_s_per_question'] = (group['recorded_answer_model_call_sum_s']/group['questions']
            if group['recorded_answer_model_call_sum_s'] is not None else None)
        group['pipeline_wall_s'] = None
    return {'unit': 'seconds', 'groups': list(groups.values()),
        'denoising': _denoise_timing(list(groups.values()), Path(resolve_root())),
        'net_inference': {'complete': False, 'strict_gpu_s': None,
            'vlm': '原生VLM请求计时可排除调用之前的本地节点等待，但包含远端排队与网络；历史API未返回可扣除的服务端等待。',
            'embedding': '原生编码批计时起于服务就绪与本地请求槽准入之后；仍含HTTP传输、服务内排队和响应解析。',
            'retrieval': '历史向量检索没有独立计时，净检索秒数缺失。',
            'total': '无法从现存记录精确计算剔除全部启动/排队的整套方案总耗时；不以调用时长减去噪时间推算排队。'},
        'basis': '交付结果沿用的原生调用墙钟计时，不等于纯GPU计算。生图计时从请求渲染到图片保存，可能包含服务启动、资源等待、排队和传输。复用追溯原调用，跳过阶段不造一次0秒调用。',
        'aggregation': '同路同阶段按调用引用去重；查询编码以原生批为单位。累计为已记录模型调用秒数/题，RAG包含初图；不叠加D7、未计时的CPU检索、调用之外的等待和独立失败run开销。原调用内已记录的服务等待不扣除；累计不是端到端时延或吞吐。',
        'comparison_note': '历史批次的冷启动、并发与队列负载可能不同；各阶段均显示计时覆盖数，缺失不补0。D7列复用原判分时长，不代表再次发起评分。',
        'percentile_method': 'linear interpolation at (n-1)*p'}


def _render(payload):
    if payload['meta'].get('scoring_scheme') == 'D':
        comparisons = _mode_comparisons(payload['cases'])
        payload['meta']['comparisons_by_model'] = comparisons
        # Retain the legacy fields with an explicit condition binding.
        selected = {model: group['pairs'].get('positive_text', group['pairs'].get('rag_text'))
                    for model, group in comparisons.items()}
        selected = {model: pair for model, pair in selected.items() if pair}
        payload['meta']['paired_by_model'] = {m: p['dimensions'] for m, p in selected.items()}
        payload['meta']['paired_modes_by_model'] = {m: [p['left'], p['right']] for m, p in selected.items()}
        if selected:
            payload['meta']['paired_dimensions'] = next(iter(selected.values()))['dimensions']
        payload['meta']['performance'] = _timing_summary(payload['cases'])
    # 同题各路的判分正文和 schema 通常完全相同；无损共享全文，避免重复文本随路数放大。
    texts, indexes = [], {}
    def pack(view):
        if isinstance(view.get('text'), str) and len(view['text']) >= 256:
            value = view.pop('text')
            if value not in indexes:
                indexes[value] = len(texts)
                texts.append(value)
            view['text_index'] = indexes[value]
    for case in payload['cases']:
        for arm in case['arms']:
            for name in ('input', 'a_input', 'b_input', 'd_input', 'core_input', 'codex_input', 'rag_decision_input', 'rag_concepts_input', 'rag_captions_input'):
                view = arm.get(name)
                if not view:
                    continue
                pack(view)
                for message in view.get('messages', []):
                    for part in message['parts']:
                        pack(part)
    payload['prompt_texts'] = texts
    text_bytes = len(json.dumps({'cases': payload['cases'], 'prompt_texts': texts,
        'performance': payload['meta'].get('performance')}, ensure_ascii=False).encode())
    if text_bytes > TEXT_BUDGET:
        raise ValueError('合并查看展开文字超过128MiB')
    payload['meta']['text_bytes'] = text_bytes
    payload['meta']['text_budget_bytes'] = TEXT_BUDGET
    payload['media'], payload['meta']['thumbnails'] = _encode_thumbnails(payload['media'])
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    if len(encoded.encode()) > 8 * 1024 * 1024:
        # Lossless inline data; browser decompression uses no network, kernel or external file.
        encoded = json.dumps({'encoding': 'gzip-base64', 'payload': base64.b64encode(
            gzip.compress(encoded.encode(), compresslevel=6, mtime=0)).decode()})
    template = Path(__file__).with_suffix('.html').read_text()
    document = template.replace('__CASE_DATA__', encoded)
    size = len(document.encode())
    if size > HTML_BUDGET:
        raise ValueError('完整离线查看 HTML 超过64MiB；未发布截断快照')
    payload['meta']['serialized_html_bytes'] = size
    return document, payload['meta']


def build_case_browser(project, run_id):
    """独立只读单个运行的全部案例。"""
    return _render(_build_case_payload(project, run_id))


def build_comparison_browser(project, run_ids):
    """按题目ID合并同一固定题表的独立运行；用于不停旧任务地补充模型路。"""
    if not 1 <= len(run_ids) <= 8 or len(set(run_ids)) != len(run_ids):
        raise ValueError('Specify 1..8 unique runs')
    # 在读媒体之前核对来源与总路数，避免误合并不同批次或超预算展开。
    manifests = [RunTables(Path(project).parent,
        f'demiwtg/evaluation/t2i/v2/datasets/records__{run}.lance').load_manifest() for run in run_ids]
    if not all(manifests) or any(m['source'] != manifests[0]['source'] for m in manifests):
        raise ValueError('Comparison requires the same frozen source URI and version')
    if sum(len(m['config']['answers']) for m in manifests) > 8:
        raise ValueError('合并查看超过8路')
    combined = _build_case_payload(project, run_ids[0])
    meta = combined['meta']
    meta['runs'] = [{k: meta[k] for k in ('run', 'active', 'phase', 'complete')}]
    meta['sources'] = {run_ids[0] + ':' + k: v for k, v in meta['sources'].items()}
    by_id = {case['task_id']: case for case in combined['cases']}
    for run in run_ids[1:]:
        other = _build_case_payload(project, run, cache_run_id=run_ids[0], media=combined['media'])
        current = other['meta']
        if (current['judge'], current['reasoning_effort']) != (meta['judge'], meta['reasoning_effort']):
            raise ValueError('Comparison requires the same judge and reasoning effort')
        if {c['task_id'] for c in other['cases']} != set(by_id):
            raise ValueError('Comparison question identities differ')
        offset = len(meta['arms'])
        index_map = {a['index']: a['index'] + offset for a in current['arms']}
        if current.get('scoring_scheme') == 'D':
            existing = {(a['generation_model'], a.get('answer_mode')): a['index'] for a in meta['arms']}
            next_index = offset
            for arm in current['arms']:
                identity = arm['generation_model'], arm.get('answer_mode')
                if identity in existing:
                    index_map[arm['index']] = existing[identity]
                else:
                    index_map[arm['index']] = next_index
                    next_index += 1
        for case in other['cases']:
            target = by_id[case['task_id']]
            if any(target[k] != case[k] for k in ('concept', 'instruction', 'taxonomy', 'test_points', 'references')):
                raise ValueError('Comparison question content differs')
            for arm in case['arms']:
                index = index_map[arm['index']]
                if index < offset:
                    previous = next(a for a in target['arms'] if a['index'] == index)
                    for key in ('instruction', 'image_json', 'question_revision', 'core_requirements',
                                'd_status', 'd_result', 'd_metrics_result', 'd_call_json'):
                        if previous.get(key) != arm.get(key):
                            raise ValueError('Repeated baseline score differs: ' + key)
                else:
                    target['arms'].append({**arm, 'index': index})
        meta['arms'].extend({**arm, 'index': index_map[arm['index']]} for arm in current['arms']
                           if index_map[arm['index']] >= offset)
        meta['processes'].extend({**p, 'run': run} for p in current.get('processes', []))
        if current.get('scoring_scheme') == 'D':
            meta.setdefault('paired_by_model', {}).update(current.get('paired_by_model', {}))
            if current.get('codex_comparison'):
                if not meta.get('codex_comparison'):
                    meta['codex_comparison'] = current['codex_comparison']
                else:
                    meta['codex_comparison']['arms'].extend(current['codex_comparison']['arms'])
        meta['sources'].update({run + ':' + k: v for k, v in current['sources'].items()})
        meta['runs'].append({k: current[k] for k in ('run', 'active', 'phase', 'complete')})
    meta.update(run=' + '.join(run_ids), expected=meta['questions'] * len(meta['arms']),
        active=any(r['active'] for r in meta['runs']), complete=all(r['complete'] for r in meta['runs']),
        phase=' / '.join(r['phase'] for r in meta['runs']))
    return _render(combined)
