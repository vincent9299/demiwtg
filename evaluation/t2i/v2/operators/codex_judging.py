"""Codex 独立补评的输入、响应与同题比较；复用冻结 D7 模板及原计分器。"""
import base64
import hashlib
import json
from pathlib import Path

from demiflow import data
from demiflow.execution.artifacts import immutable
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.sqlite_offline import submit_response
from .images import image_data_url
from .d_evaluation import DIMENSIONS


def configuration(spec):
    spec = dict(spec)
    for key in ('base_run',):
        if not isinstance(spec.get(key), str) or Path(spec[key]).name != spec[key] or spec[key] in {'.', '..'}:
            raise ValueError('Codex comparison requires a single base_run name')
    if type(spec.get('expected_requests')) is not int or not 1 <= spec['expected_requests'] <= 600:
        raise ValueError('Codex comparison requires expected_requests in 1..600')
    if spec.get('selection', 'retry') not in ('retry', 'uncovered'):
        raise ValueError('Codex selection must be retry or uncovered')
    if not isinstance(spec.get('reuse_sources', []), list) or len(spec.get('reuse_sources', [])) > 8:
        raise ValueError('Codex reuse_sources requires at most eight fixed references')
    for ref in spec.get('reuse_sources', []):
        if not Path(ref['uri']).is_absolute() or type(ref['version']) is not int or ref['version'] < 1:
            raise ValueError('Codex reuse requires fixed absolute table references')
    return spec


def identity(row):
    return row['task_id'], row['answer_mode']


def ensure_same_answer(left, right):
    """比较必须绑定同一题面、清单、模型和图像；不校验评语内容。"""
    for key in ('task_id', 'answer_mode', 'answer_model', 'instruction', 'question_revision', 'd_protocol'):
        if left.get(key) != right.get(key):
            raise ValueError('Judge comparison identity mismatch: ' + key)
    for key in ('image_json', 'core_requirements_json'):
        if json.loads(left[key]) != json.loads(right[key]):
            raise ValueError('Judge comparison identity mismatch: ' + key)


def prepare(row, *, root, cases):
    entry = cases[identity(row)]
    frozen = json.loads(row['core_requirements_json'])
    return {**row, **{key: None for key in row if key.startswith('d_')},
            '_case': entry['case'], '_original_call': row['d_call_json'], 'd_protocol': 'd7',
            'd_payload': {'instruction': row['instruction'], 'requirements': frozen['requirements']},
            'd_images': [image_data_url(json.loads(row['image_json']), root)],
            'test_points': [], 'judge_model': 'codex/gpt-6-astra',
            'd_status': 'ready', 'd_reason': '', 'd_result': None, 'd_error': None, 'd_call': None}


def normalized_messages(messages):
    """逐字比较文字；图片比对当时实际 data URL 的摘要。"""
    result = json.loads(json.dumps(messages))
    for message in result:
        if isinstance(message['content'], list):
            for part in message['content']:
                if part['type'] == 'image_url':
                    value = part['image_url']['url']
                    part['image_url']['url'] = (hashlib.sha256(value.encode()).hexdigest()
                        if isinstance(value, str) else value['data_uri_sha256'])
    return result


def export_input(row, *, root, directory):
    call = row.get('d_call') or (row.get('d_error') or {}).get('call')
    if not call or not call.get('request_ref'):
        raise ValueError('Standard prompt operator did not materialize the request')
    request = read_call(call['request_ref'], root)
    original_call = json.loads(row.get('_original_call') or '{}')
    if original_call:
        original = read_call(original_call['request_ref'], root)
        historical_messages = (original.get('payload') or original)['messages']
        if normalized_messages(request['messages']) != normalized_messages(historical_messages):
            raise ValueError('Rendered Codex prompt/image differs from actual Malasci input')
    case = directory / row['_case']
    case.mkdir(parents=True, exist_ok=True)
    texts, image_hashes = [], []
    for index, message in enumerate(request['messages']):
        parts = message['content']
        parts = [{'type': 'text', 'text': parts}] if isinstance(parts, str) else parts
        content = []
        for part in parts:
            if part['type'] == 'text':
                content.append(part['text'])
            elif part['type'] == 'image_url':
                raw = base64.b64decode(part['image_url']['url'].split(',', 1)[1], validate=True)
                image_name = f'image{len(image_hashes) + 1}.png'
                path = case / image_name
                if path.exists() and path.read_bytes() != raw:
                    raise ValueError('Immutable input image differs')
                path.write_bytes(raw)
                image_hashes.append({'file': image_name, 'sha256': hashlib.sha256(raw).hexdigest()})
        name = f'{index:02d}_{message["role"]}.txt'
        value = '\n\n'.join(content)
        path = case / name
        if path.exists() and path.read_text() != value:
            raise ValueError('Immutable input text differs')
        path.write_text(value)
        texts.append({'file': name, 'sha256': hashlib.sha256(value.encode()).hexdigest()})
    receipt = {'case': row['_case'], 'task_id': row['task_id'], 'answer_mode': row['answer_mode'],
               'concept': row['concept'], 'request_ref': call['request_ref'],
               'historical_request_ref': original_call.get('request_ref'),
               'texts': texts, 'images': image_hashes, 'prompt_version': request['prompt_version'],
               'same_actual_input': True if original_call else None,
               'input_sha256': hashlib.sha256(json.dumps(normalized_messages(request['messages']),
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    immutable(case / 'input_receipt.json', receipt)
    return row


def submit_available(root, directory, manifest):
    """外部子代理只提交原始响应；标准节点负责解析，代码只按既有公式计分。"""
    count = 0
    for entry in manifest['cases']:
        case = directory / entry['case']
        response = case / 'response.json'
        if not response.exists():
            continue
        receipt = json.loads((case / 'input_receipt.json').read_text())
        execution = json.loads((case / 'execution.json').read_text())
        if execution.get('transport') != 'codex_subagent' or execution.get('fork_turns') != 'none':
            raise ValueError('Independent Codex execution receipt required')
        if any(execution.get(key) != manifest[key] for key in ('model', 'reasoning_effort')):
            raise ValueError('Codex execution model or reasoning effort differs from frozen configuration')
        for file in receipt['texts'] + receipt['images']:
            if hashlib.sha256((case / file['file']).read_bytes()).hexdigest() != file['sha256']:
                raise ValueError('Codex input artifact changed')
        submit_response(root, receipt['request_ref'], response.read_text(), model='codex/gpt-6-astra',
                        metadata={**execution, 'input_receipt': str(case / 'input_receipt.json')})
        count += 1
    return count


def comparison_summary(base_rows, codex_rows):
    """每个维度仅在双方同题均有分时计算差值；未评及 N/A 不补零。"""
    base = {identity(row): row for row in base_rows}
    if len(base) != len(base_rows) or len({identity(row) for row in codex_rows}) != len(codex_rows):
        raise ValueError('Duplicate judge comparison rows')
    for row in codex_rows:
        if not str(row.get('judge_model', '')).lower().startswith('codex'):
            raise ValueError('Codex comparison source is not a Codex judgment')
        if identity(row) not in base:
            raise ValueError('Codex row outside the comparison cohort')
        ensure_same_answer(base[identity(row)], row)
    arms = []
    for mode in ('text_only', 'positive_images'):
        original = [r for r in base_rows if r['answer_mode'] == mode]
        candidates = [r for r in codex_rows if r['answer_mode'] == mode]
        dimensions = {}
        for name, field in DIMENSIONS.items():
            if name == 'other_instruction_following':
                continue
            pairs = [(base[identity(r)][field], r[field]) for r in candidates
                     if r.get('d_status') == 'reviewed' and r.get(field) is not None
                     and base[identity(r)].get(field) is not None]
            dimensions[name] = {'paired_count': len(pairs),
                'malasci_mean': sum(a for a, b in pairs) / len(pairs) if pairs else None,
                'codex_mean': sum(b for a, b in pairs) / len(pairs) if pairs else None,
                'delta': sum(b - a for a, b in pairs) / len(pairs) if pairs else None}
        arms.append({'answer_mode': mode, 'expected': len(original), 'recorded': len(candidates),
                     'reviewed': sum(r.get('d_status') == 'reviewed' for r in candidates), 'dimensions': dimensions})
    return {'label': 'Codex · gpt-6-astra + xhigh', 'arms': arms}


def read_comparison(root, run_id, base_rows, *, peer_run=None):
    if peer_run:
        from .run_tables import RunTables
        records = lambda name: RunTables(root, f'demiwtg/evaluation/t2i/v2/datasets/records__{name}.lance')
        base, peer = records(run_id).load_manifest(), records(peer_run).load_manifest()
        if not peer:
            return {}, {'label': 'Codex · 尚未提交', 'arms': [], 'source': {'run': peer_run}}
        if (base['source'] != peer['source'] or base['config']['answers'] != peer['config']['answers']
                or base['config']['d_evaluation']['judge_prompt_pack'] != peer['config']['d_evaluation']['judge_prompt_pack']):
            raise ValueError('Independent judges must consume the same frozen source and D7 prompt')
        rows, refs = [], []
        for i in range(2):
            path = root / f'demiwtg/evaluation/t2i/v2/datasets/scores_d__{peer_run}__arm{i:02d}.lance'
            if path.exists():
                import lance
                ref = {'uri': str(path), 'version': lance.dataset(str(path)).version}
                refs.append(ref)
                arm = data.read_lance(**ref).take(301)
                if len(arm) > 300:
                    raise ValueError('Independent Codex arm exceeds question budget')
                rows.extend(arm)
        summary = comparison_summary(base_rows, rows)
        return {identity(row): row for row in rows}, {**summary, 'source': {'run': peer_run, 'refs': refs}}
    path = root / '_demiflow/evaluation_t2i_v2' / run_id / 'codex_comparison.json'
    if not path.exists():
        return {}, None
    ref = json.loads(path.read_text())
    rows = data.read_lance(**ref['target']).take(601)
    if len(rows) > 600:
        raise ValueError('Codex comparison row budget exceeded')
    summary = comparison_summary(base_rows, rows)
    return {identity(row): row for row in rows}, {**summary, 'source': ref}


def input_view(row, root, preview):
    from .request_view import judge_view
    call = json.loads(row.get('d_call_json') or '{}')
    if call.get('request_ref'):
        return judge_view(row['d_call_json'], row['image_json'], root, preview)
    if call.get('transport') == 'codex_subagent' and call.get('historical_request_ref'):
        view = judge_view(json.dumps({'request_ref': call['historical_request_ref']}),
                          row['image_json'], root, preview)
        if view:
            view['note'] = '复用先前独立 Codex 判分：输入取自原请求，执行模型及导出记录见下方。'
            view['source'] = {'input_request': call['historical_request_ref'], 'codex_execution': call}
        return view
    return None
