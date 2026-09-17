"""Read-only benchmark audit and materialization for a small RAG development pilot.

python3 -m curation.rag_diagnostic build
Notebook consumer: curation/rag_diagnostic.ipynb. Build/review are read-only;
the explicit run command invokes local BAGEL and never calls a paid API.
"""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))

import argparse
import base64
import hashlib
import html
import io
import json
import os
import subprocess
from pathlib import Path

REPO = _ARCHIVE_ROOT
RUN = REPO / 'state/curation/rag_diagnostic_v1'
SELECTION = {
    't2i': ['60001', '60007', '60050', '60079', '60146', '60067'],
    'edit': ['e001', 'e030', 'e044', 'e127', 'e165', 'e173', 'e184', 'e193'],
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    result = {str(row['qid']): row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f'Duplicate qid: {path}')
    return result


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def build():
    from PIL import Image, ImageDraw, ImageOps

    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / 'previews').mkdir(exist_ok=True)
    notes_path = RUN / 'assistant_notes.json'
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else {}
    files, cases = {}, []
    for task, ids in SELECTION.items():
        batch = REPO / 'benchmark' / task / 'bench200'
        qpath = batch / 'questions.jsonl'
        questions = read_rows(qpath)
        files[str(qpath)] = sha(qpath)
        if task == 't2i':
            score_paths = {
                'BAGEL': batch / 'scores/scores_v60_V2_gpt-5.6-sol_bagel.jsonl',
                'Gemini': batch / 'scores/scores_v60_V2_gpt-5.6-sol_gemini-3.1-flash-image.jsonl',
            }
            response_paths = {
                'BAGEL': batch / 'bagel/responses_shard0.jsonl',
                'Gemini': batch / 'gemini/responses_gemini-3.1-flash-image.jsonl',
            }
            responses = {m: read_rows(p) for m, p in response_paths.items()}
            for p in response_paths.values():
                files[str(p)] = sha(p)
        else:
            bagel_responses = {}
            for p in sorted((batch / 'bagel').glob('responses_shard*.jsonl')):
                rows = read_rows(p)
                if set(rows) & set(bagel_responses):
                    raise ValueError('Duplicate BAGEL response across shards')
                bagel_responses.update(rows)
                files[str(p)] = sha(p)
            dirs = {
                'BAGEL': batch / 'scores_qib_v22_astra_medium_20260908/b',
                'Gemini': batch / 'scores_qib_v22_astra_medium_20260908_supplement/g',
            }
            score_paths = {m: p / 'scores.jsonl' for m, p in dirs.items()}
            manifests = {m: read_rows(p / 'blind_manifest.jsonl') for m, p in dirs.items()}
            for p in dirs.values():
                files[str(p / 'blind_manifest.jsonl')] = sha(p / 'blind_manifest.jsonl')
        scores = {m: read_rows(p) for m, p in score_paths.items()}
        for p in score_paths.values():
            files[str(p)] = sha(p)
        for qid in ids:
            q = questions[qid]
            prompt = q['gen_prompt' if task == 't2i' else 'edit_instruction']
            key = f'{task}_{qid}'
            images = {}
            if task == 'edit':
                images['Source'] = str(Path(manifests['BAGEL'][qid]['before']).resolve())
            for m in scores:
                if task == 't2i':
                    response = responses[m][qid]
                    if response.get('ok') is not True:
                        raise ValueError(f'Failed generation: {key}/{m}')
                    images[m] = str((response_paths[m].parent / response['image']).resolve())
                else:
                    manifest = manifests[m][qid]
                    if manifest['edit_instruction'] != prompt:
                        raise ValueError(f'Instruction mismatch: {key}/{m}')
                    expected = manifest['inputs']
                    if scores[m][qid]['inputs'] != expected:
                        raise ValueError(f'Score binding mismatch: {key}/{m}')
                    if sha(manifest['before']) != expected['source_sha256']:
                        raise ValueError(f'Source mismatch: {key}/{m}')
                    if sha(manifest['after']) != expected['output_sha256']:
                        raise ValueError(f'Output mismatch: {key}/{m}')
                    if expected['source_sha256'] != sha(images['Source']):
                        raise ValueError(f'Cross-model source mismatch: {key}')
                    if hashlib.sha256(prompt.encode()).hexdigest() != expected['instruction_sha256']:
                        raise ValueError(f'Prompt hash mismatch: {key}/{m}')
                    images[m] = str(Path(manifest['after']).resolve())
            hashes = {m: sha(p) for m, p in images.items()}
            if task == 'edit':
                response = bagel_responses[qid]
                if (response.get('output_sha256') != hashes['BAGEL']
                        or response.get('source_sha256') != hashes['Source']
                        or response.get('instruction_sha256') != hashlib.sha256(prompt.encode()).hexdigest()):
                    raise ValueError(f'BAGEL historical response binding mismatch: {key}')
            canvas = Image.new('RGB', (600 * len(images), 660), 'white')
            draw = ImageDraw.Draw(canvas)
            for i, (m, path) in enumerate(images.items()):
                with Image.open(path) as im:
                    thumb = ImageOps.contain(ImageOps.exif_transpose(im).convert('RGB'), (592, 620))
                canvas.paste(thumb, (i * 600 + (600 - thumb.width) // 2, 35 + (620 - thumb.height) // 2))
                draw.text((i * 600 + 12, 12), f'{key} | {m}', fill='black')
            preview = RUN / 'previews' / f'{key}.jpg'
            canvas.save(preview, quality=94)
            cases.append(dict(
                id=key, task=task, qid=qid, concept=q.get('instance', q.get('_instance')),
                original_prompt=prompt, question_path=str(qpath), images=images,
                image_sha256=hashes, preview=str(preview), historical_scores={m: s[qid] for m, s in scores.items()},
                bagel_response=(bagel_responses[qid] if task == 'edit' else responses['BAGEL'][qid]),
                assistant_review=notes.get(key, {'status': 'pending'}), human_review='unreviewed',
                provenance_limit=('Current response path and image hash verified; historical T2I scores lack the edit-style input hash contract.'
                                  if task == 't2i' else 'Frozen manifest image, instruction and score input hashes verified.'),
            ))
    payload = dict(schema='rag-diagnostic-v1', role='development_only', author_type='assistant',
                   models={'BAGEL': 'BAGEL-7B-MoT', 'Gemini': 'gemini-3.1-flash-image'},
                   selection='Purposive, score-informed audit; neither random nor representative. No RAG gain measured.',
                   original_files_sha256=files, cases=cases)
    write_json(RUN / 'audit.json', payload)
    print(f'{len(cases)} cases; image and prompt bindings checked; {RUN / "audit.json"}')


def render(run=RUN):
    """Self-contained notebook HTML; original images are embedded, no remote requests."""
    data = json.loads((Path(run) / 'audit.json').read_text())
    esc = lambda x: html.escape(str(x))
    chunks = ['<h2>RAG 开发诊断：已有结果与对照输入</h2>',
              '<p>14 个定向选取案例，不代表全题库比例。旧分数用于选材；助手逐图观察不等于人工标签。'
              '本页展示历史输出及对照输入，不能据此声称 RAG 提升。T2I 对齐分与编辑官方总分不可横向比较。</p>']
    plan_path = Path(run) / 'plan.json'
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        chunks.append('<p><b>首轮生成计划：</b>3 个案例 × 原题/文字知识/显式目标 × 3 个配对随机种子 = 27 张。'
                      '人工选出的知识不是实际检索结果；图片知识臂尚未就绪。旧图片仅用于诊断，原题也会同配置重跑。</p>')
        chunks.append(f'<pre style="white-space:pre-wrap">{esc(json.dumps(plan["interpretation_limits"], ensure_ascii=False, indent=2))}</pre>')
    for c in data['cases']:
        n = c['assistant_review']
        chunks += [f'<hr><h3>{esc(c["id"])} · {esc(c["concept"])}</h3>',
                   f'<p><b>原题面</b>：{esc(c["original_prompt"])}</p>', '<div style="display:flex;gap:12px;flex-wrap:wrap">']
        for label, path in c['images'].items():
            from PIL import Image, ImageOps
            p = Path(path)
            with Image.open(p) as original:
                thumb = ImageOps.contain(ImageOps.exif_transpose(original).convert('RGB'), (900, 900))
            buffer = io.BytesIO()
            thumb.save(buffer, format='JPEG', quality=90)
            uri = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()
            chunks.append(f'<figure style="margin:0;max-width:440px"><figcaption>{esc(label)}</figcaption>'
                          f'<a href="{esc(path)}"><img style="width:100%" src="{uri}"></a></figure>')
        chunks.append('</div>')
        score_summary = {m: s.get('alignment_score', s.get('official_total')) for m, s in c['historical_scores'].items()}
        chunks.append(f'<p><b>旧评分</b>：{esc(score_summary)}（不是知识分）</p>')
        if c.get('bagel_response', {}).get('think'):
            chunks.append('<details><summary>BAGEL 历史生成时的文字计划（不是正确性证明）</summary>'
                          f'<pre style="white-space:pre-wrap">{esc(c["bagel_response"]["think"])}</pre></details>')
        for k, title in [('role', '选择理由'), ('knowledge_dependency', '知识依赖'),
                         ('observations', '助手看图记录'), ('diagnosis', '当前诊断'),
                         ('knowledge', '核验后的知识及适用条件'), ('checks', '可见判据与合理例外'),
                         ('gaps', '材料与实验缺口'), ('next', '处理建议')]:
            if k in n:
                val = n[k] if isinstance(n[k], str) else json.dumps(n[k], ensure_ascii=False, indent=2)
                chunks.append(f'<p><b>{title}</b></p><pre style="white-space:pre-wrap">{esc(val)}</pre>')
        for s in n.get('sources', []):
            chunks.append(f'<p><a href="{esc(s["url"])}">{esc(s["title"])}</a>：{esc(s["support"])}'
                          f'<br>原文短摘：{esc(s.get("quote", "待补"))}</p>')
        for condition, prompt in n.get('conditions', {}).items():
            chunks.append(f'<details><summary>实际对照输入：{esc(condition)}</summary>'
                          f'<pre style="white-space:pre-wrap">{esc(prompt)}</pre></details>')
        chunks.append('<details><summary>历史评分原记录及理由（未改写）</summary>'
                      f'<pre style="white-space:pre-wrap">{esc(json.dumps(c["historical_scores"], ensure_ascii=False, indent=2))}</pre></details>')
        chunks.append(f'<p>事实依据：{esc(n.get("fact_status", "待核验"))}；策展价值：{esc(n.get("value_status", "待诊断"))}；'
                      f'知识图片支持：{esc(n.get("image_support", "待核验"))}。人工：未审核。{esc(c["provenance_limit"])}</p>')
    return '\n'.join(chunks)


def prepare():
    """Make paired question banks compatible with the unchanged BAGEL runner."""
    audit = json.loads((RUN / 'audit.json').read_text())
    selected = [c for c in audit['cases'] if c['assistant_review'].get('conditions')]
    if len(selected) != 3:
        raise ValueError('This pilot expects exactly three fully specified cases')
    if any((RUN / 'conditions').glob('*/outputs/responses*.jsonl')):
        raise ValueError('Outputs exist; create a new version instead of changing pilot inputs')
    runner = REPO / 'bagel/Bagel/scripts/run_wkbench.py'
    frozen = {str(runner): sha(runner), str(RUN / 'assistant_notes.json'): sha(RUN / 'assistant_notes.json')}
    for name in ['inferencer.py', 'data/transforms.py']:
        p = REPO / 'bagel/Bagel' / name
        frozen[str(p)] = sha(p)
    jobs = []
    for condition in ['baseline', 'knowledge', 'explicit']:
        directory = RUN / 'conditions' / condition
        directory.mkdir(parents=True, exist_ok=True)
        questions = []
        for case in selected:
            for repeat in range(1, 4):
                # The same qid in each condition yields the same runner-derived seed.
                qid = f'{case["qid"]}_r{repeat}'
                row = dict(qid=qid, task=case['task'], parent_id=case['id'], condition=condition,
                           repeat=repeat, role='development_only')
                field = 'gen_prompt' if case['task'] == 't2i' else 'edit_instruction'
                row[field] = case['assistant_review']['conditions'][condition]
                if case['task'] == 'edit':
                    row['_sample_image'] = case['images']['Source']
                    frozen[row['_sample_image']] = case['image_sha256']['Source']
                seed = 42 + int.from_bytes(hashlib.sha256(qid.encode()).digest()[:4], 'big')
                jobs.append(dict(qid=qid, parent_id=case['id'], condition=condition, repeat=repeat,
                                 seed=seed, prompt_sha256=hashlib.sha256(row[field].encode()).hexdigest(),
                                 checks=case['assistant_review']['checks']))
                questions.append(row)
        p = directory / 'questions.jsonl'
        p.write_text(''.join(json.dumps(q, ensure_ascii=False) + '\n' for q in questions))
        frozen[str(p)] = sha(p)
    plan = dict(schema='rag-diagnostic-plan-v1', stage='prepared_not_generated', role='development_only',
                cases=[c['id'] for c in selected], jobs=jobs, n_images=len(jobs), seed_base=42,
                runner=str(runner), python=str(REPO.parent / 'env-bagel/bin/python'),
                model_path=str(REPO / 'bagel/models/BAGEL-7B-MoT'), image_size=512, num_timesteps=50,
                frozen_files=frozen, human_review='unreviewed', image_knowledge_arm='pending_verified_reference_and_interface',
                interpretation_limits=[
                    'This is an oracle text-material development pilot, not an actual retrieval or finetuning result.',
                    'Baseline is regenerated with the same runner/settings/seeds. Old T2I outputs have incomplete seed provenance.',
                    'Prompt length, repetition and wording differ; gains alone do not isolate retrieval or internal knowledge.',
                    'Three repeats describe a small pilot; they do not establish benchmark-wide statistical significance.',
                    'Shared initial seeds do not guarantee identical later noise draws when edit thinking lengths differ.',
                    'Knowledge conformity, target/side binding, other instruction compliance and unobservable cases are recorded separately.',
                    'No teacher output or benchmark target is used as a reference image. No automatic core admission.',
                ])
    write_json(RUN / 'plan.json', plan)
    print(f'Prepared {len(jobs)} paired generation jobs; no model called.')


def preflight(gpu=0, run=RUN):
    plan = json.loads((Path(run) / 'plan.json').read_text())
    for p, expected in plan['frozen_files'].items():
        if sha(p) != expected:
            raise ValueError(f'Frozen pilot input changed: {p}')
    if os.environ.get('LORA_PATH'):
        raise ValueError('LORA_PATH is set; this pilot requires original BAGEL weights')
    for p in [plan['python'], str(Path(plan['model_path']) / 'ema.safetensors')]:
        if not Path(p).is_file():
            raise ValueError(f'Missing runtime asset: {p}')
    proc = subprocess.run(['nvidia-smi', f'--id={gpu}', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                          check=True, capture_output=True, text=True)
    free_gib = int(proc.stdout.strip()) / 1024
    result = dict(gpu=gpu, free_gib=round(free_gib, 2), minimum_gib=46,
                  ready=free_gib >= 46, n_images=plan['n_images'],
                  services_stopped=False, paid_calls=0)
    print(json.dumps(result, ensure_ascii=False))
    return result


def run_pilot(gpu=0, condition='all', gpus=None, run=RUN):
    """Explicit local execution only. Never stop another GPU user or call paid APIs."""
    import fcntl
    import shutil

    run = Path(run)
    with (run / '.generation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = json.loads((run / 'plan.json').read_text())
        devices = gpus or [gpu]
        if len(set(devices)) != len(devices):
            raise ValueError('Duplicate GPU assignments')
        for arm in (plan.get('conditions', ['baseline', 'knowledge', 'explicit']) if condition == 'all' else [condition]):
            for device in devices:
                if not preflight(device, run=run)['ready']:
                    raise RuntimeError('Insufficient free GPU memory; no process was stopped and no model was loaded')
            directory = run / 'conditions' / arm
            settings = {**plan.get('inference_settings', {}), **plan.get('condition_parameters', {}).get(arm, {})}
            outputs = directory / 'outputs'
            if (outputs / 'questions.jsonl').exists() and sha(outputs / 'questions.jsonl') != sha(directory / 'questions.jsonl'):
                raise ValueError('Existing output snapshot differs from frozen questions')
            for response_path in outputs.glob('responses*.jsonl'):
                if any(not r.get('ok') for r in read_rows(response_path).values()):
                    raise ValueError('Failed responses exist; preserve them and repair in a new explicit attempt')
            outputs.mkdir(exist_ok=True)
            if not (outputs / 'questions.jsonl').exists():
                shutil.copyfile(directory / 'questions.jsonl', outputs / 'questions.jsonl')
            children, logs = [], []
            try:
                for shard, device in enumerate(devices):
                    work = directory / f'work_{shard}'
                    work.mkdir(exist_ok=True)
                    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(device))
                    log = (directory / f'worker_{shard}.log').open('ab', buffering=0)
                    logs.append(log)
                    command = [plan['python'], '-u', plan['runner'], '--model_path', plan['model_path'],
                            '--questions', str(directory / 'questions.jsonl'), '--out_dir', str(outputs),
                            '--tasks', 't2i,edit', '--seed', str(plan['seed_base']),
                            '--image_size', str(settings.get('image_size', plan['image_size'])), '--num_timesteps', str(plan['num_timesteps']),
                            '--shard', str(shard), '--num_shards', str(len(devices))]
                    if 'cfg_renorm_min' in settings:
                        command += ['--cfg_renorm_min', str(settings['cfg_renorm_min'])]
                    if settings.get('think'):
                        command += ['--think']
                    children.append(subprocess.Popen(command, cwd=work, env=env, stdout=log, stderr=log))
                    print(f'{arm} shard {shard} GPU {device} PID {children[-1].pid}', flush=True)
                codes = [child.wait() for child in children]
                if any(codes):
                    raise RuntimeError(f'BAGEL shard failure: {arm}: {codes}')
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                for child in children:
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait()
                for log in logs:
                    log.close()
            rows = {}
            for response_path in outputs.glob('responses*.jsonl'):
                rows.update(read_rows(response_path))
            if set(rows) != set(read_rows(directory / 'questions.jsonl')) or any(not row.get('ok') for row in rows.values()):
                raise RuntimeError(f'Incomplete or failed images in {arm}; preserve responses for inspection')


def status():
    plan = json.loads((RUN / 'plan.json').read_text())
    counts = {}
    for arm in ['baseline', 'knowledge', 'explicit']:
        directory = RUN / 'conditions' / arm / 'outputs'
        rows = {}
        for p in directory.glob('responses*.jsonl'):
            rows.update(read_rows(p))
        success = 0
        for r in rows.values():
            image = directory / r.get('image', '__missing__')
            if r.get('ok') and image.is_file():
                success += 1
        counts[arm] = dict(expected=9, successful_images=success, response_records=len(rows))
    review = 'assistant_development_review_available' if (RUN / 'pilot_assessment.json').exists() else 'not_evaluated'
    print(json.dumps(dict(n_images=plan['n_images'], conditions=counts, knowledge_gain=review), indent=2))


def render_pilot():
    """Display actual pilot outputs after generation; never substitute historical images."""
    from PIL import Image, ImageOps

    plan = json.loads((RUN / 'plan.json').read_text())
    chunks = ['<h2>新对照结果（未生成的位置显示待运行）</h2>',
              '<p>此处只读本轮实际输出。尚无知识项复评；对照条件可见的浏览不属于盲评。</p>']
    session_path = RUN / 'session.json'
    if session_path.exists():
        chunks.append(f'<p>运行与后台恢复状态：{html.escape(session_path.read_text())}</p>')
    assessment_path = RUN / 'pilot_assessment.json'
    if assessment_path.exists():
        assessment = json.loads(assessment_path.read_text())
        chunks[1] = '<p>助手逐图开发复核，非独立盲评，非人工金标准。判据在生成前已记录；无法观察与明确冲突分开。</p>'
        chunks.append('<h3>首轮观察</h3><pre style="white-space:pre-wrap">'
                      + html.escape(json.dumps(assessment['summary'], ensure_ascii=False, indent=2)) + '</pre>')
    records = {}
    for arm in ['baseline', 'knowledge', 'explicit']:
        directory = RUN / 'conditions' / arm / 'outputs'
        for p in directory.glob('responses*.jsonl'):
            for qid, row in read_rows(p).items():
                key = (arm, qid)
                if key in records:
                    raise ValueError(f'Duplicate generated result: {key}')
                records[key] = row
    valid_keys = {(j['condition'], j['qid']) for j in plan['jobs']}
    if not set(records) <= valid_keys:
        raise ValueError('Unexpected pilot response outside frozen plan')
    for case in plan['cases']:
        for repeat in range(1, 4):
            chunks.append(f'<h3>{html.escape(case)} · repeat {repeat}</h3><div style="display:flex;gap:12px;flex-wrap:wrap">')
            for job in [j for j in plan['jobs'] if j['parent_id'] == case and j['repeat'] == repeat]:
                arm, qid = job['condition'], job['qid']
                chunks.append(f'<figure style="margin:0;max-width:440px"><figcaption>{arm} · seed {job["seed"]}</figcaption>')
                row = records.get((arm, qid))
                if row is None:
                    chunks.append('<p>待运行</p></figure>')
                    continue
                if row.get('seed') != job['seed']:
                    raise ValueError(f'Wrong paired seed: {arm}/{qid}')
                if not row.get('ok'):
                    chunks.append(f'<p>生成错误：{html.escape(str(row.get("error")))}</p></figure>')
                    continue
                directory = RUN / 'conditions' / arm / 'outputs'
                if sha(directory / 'questions.jsonl') != sha(directory.parent / 'questions.jsonl'):
                    raise ValueError('Generated question snapshot mismatch')
                if row['task'] == 'edit' and row.get('instruction_sha256') != job['prompt_sha256']:
                    raise ValueError('Generated instruction hash mismatch')
                path = (directory / row['image']).resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise ValueError('Output image escaped pilot directory')
                if row.get('output_sha256') and sha(path) != row['output_sha256']:
                    raise ValueError('Generated image hash mismatch')
                with Image.open(path) as im:
                    thumb = ImageOps.contain(ImageOps.exif_transpose(im).convert('RGB'), (900, 900))
                buffer = io.BytesIO()
                thumb.save(buffer, format='JPEG', quality=90)
                uri = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()
                chunks.append(f'<a href="{html.escape(str(path))}"><img style="width:100%" src="{uri}"></a></figure>')
            chunks.append('</div>')
            if assessment_path.exists():
                judged = [r for r in assessment['records'] if r['parent_id'] == case and r['repeat'] == repeat]
                chunks.append('<pre style="white-space:pre-wrap">' + html.escape(json.dumps(judged, ensure_ascii=False, indent=2)) + '</pre>')
    return '\n'.join(chunks)


def pilot_panels():
    """Inspection sheets only: preserve originals and expose all conditions/repeats."""
    from PIL import Image, ImageDraw, ImageOps

    plan = json.loads((RUN / 'plan.json').read_text())
    panels = RUN / 'pilot_panels'
    panels.mkdir(exist_ok=True)
    for parent in plan['cases']:
        for view in ['full', 'detail']:
            canvas = Image.new('RGB', (1800, 1560), 'white')
            draw = ImageDraw.Draw(canvas)
            for col, condition in enumerate(['baseline', 'knowledge', 'explicit']):
                for repeat in range(1, 4):
                    qid = next(j['qid'] for j in plan['jobs'] if j['parent_id'] == parent and j['repeat'] == repeat)
                    path = RUN / 'conditions' / condition / 'outputs/imgs' / f'{qid}.png'
                    if not path.exists():
                        continue
                    with Image.open(path) as im:
                        original = ImageOps.exif_transpose(im).convert('RGB')
                    if view == 'detail':
                        box = {'t2i_60050': (.25, .35, .75, 1),
                               'edit_e127': (.34, .65, .67, .94),
                               'edit_e165': (.31, .13, .64, .64)}[parent]
                        w, h = original.size
                        original = original.crop(tuple(int(v * (w if i % 2 == 0 else h)) for i, v in enumerate(box)))
                    thumb = ImageOps.contain(original, (590, 480))
                    x, y = col * 600, (repeat - 1) * 520
                    canvas.paste(thumb, (x + (600-thumb.width)//2, y+35+(480-thumb.height)//2))
                    draw.text((x+8, y+8), f'{parent} {condition} r{repeat} {view}', fill='black')
            canvas.save(panels / f'{parent}_{view}.jpg', quality=95)
    print(panels)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'prepare', 'preflight', 'run', 'status', 'panels'])
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--gpus', help='Comma-separated GPUs for local data-parallel shards')
    parser.add_argument('--condition', choices=['all', 'baseline', 'knowledge', 'explicit'], default='all')
    args = parser.parse_args()
    if args.command in ('build', 'prepare', 'status'):
        globals()[args.command]()
    elif args.command == 'preflight':
        preflight(args.gpu)
    elif args.command == 'panels':
        pilot_panels()
    else:
        run_pilot(args.gpu, args.condition, [int(g) for g in args.gpus.split(',')] if args.gpus else None)
