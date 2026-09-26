"""ImgEdit three-perspective judge preparation and scoring, ported from eval_score.py.

The per-edit_type rubric texts live byte-identical in edit_score_prompts.json
(the frozen contract). The judge replies in free text; the business layer
parses "Dimension: 1-5" lines and applies the hard clamp (dims 2/3 <= dim 1).
"""
import json
import re
from functools import partial
from pathlib import Path

import yaml
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template

from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import prompt_store, run_manifest, run_records

CONTRACT_FILE = Path(__file__).resolve().parents[1] / 'prompts/edit_score_prompts.json'
PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'

EDIT_DIMS = {
    "replace": ["Prompt Compliance", "Visual Naturalness", "Physical & Detail Integrity"],
    "add": ["Prompt Compliance", "Visual Naturalness", "Physical & Detail Coherence"],
    "adjust": ["Prompt Compliance", "Visual Seamlessness", "Physical & Detail Fidelity"],
    "remove": ["Prompt Compliance", "Visual Naturalness", "Physical & Detail Integrity"],
    "style": ["Style Fidelity", "Content Preservation", "Rendering Quality"],
    "action": ["Action Fidelity", "Identity Preservation", "Visual & Anatomical Coherence"],
    "extract": ["Object Identity", "Mask Precision", "Visual Quality"],
    "background": ["Instruction Compliance", "Visual Seamlessness", "Physical Consistency"],
    "compose": ["Instruction Compliance", "Visual Naturalness", "Physical Consistency & Fine Detail"],
}


def prompt_config(run, config):
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
    model = config['judge']
    if config['mode'] == 'local':
        import os
        from preparation.prompts import validate_local_endpoint
        validate_local_endpoint(model['base_url'], model['name'])
        os.environ.setdefault('CURATION_DOWNSTREAM_MODEL_KEY', 'local-no-auth')
        options = {'lance_journal': prompt_store(run, 'calls'), 'timeout_s': model['timeout_s'],
                   'verify_model': True, 'require_finish_reason_stop': True, 'trust_env': False,
                   'request_options': {'temperature': 0.0, 'seed': 42, 'max_tokens': 2048}}
        name = model['name']
    else:
        name = config['judge_model']
        options = {'offline_store': prompt_store(run)}
    for prompt in spec['prompts'].values():
        prompt['model'] = {**prompt['model'], 'name': name, 'base_url': model['base_url']}
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    run_records(run).put('prompt_config', {'yaml': text, 'execution_options': options})
    return parse_prompt_pack(text), options


def encode_image_bytes(data: bytes, max_edge: int) -> str:
    """Ported from eval_score.encode_image（bytes 来源，JPEG data URL）."""
    import base64
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        k = max_edge / max(w, h)
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def image_bytes(image):
    from preparation.operaters.inputs import asset_pixels
    return asset_pixels(image)


def prepare_judge(row, *, run, pack, config):
    """Build the judge request: rubric text + before/after images (historical order)."""
    if row['status'] != 'generated':
        return row
    etype = row.get('edit_type', '')
    prompts = json.loads(CONTRACT_FILE.read_text(encoding='utf-8'))
    if etype not in prompts:
        return {**row, 'status': 'unsupported_edit_type', 'fail_reason': f'未知 edit_type: {etype!r}'}
    text = prompts[etype].replace("<edit_prompt>", row['edit_instruction'])
    max_edge = config.get('max_edge', 1024)
    # 历史顺序：判分文本 → before → after；平台模板图片置于末尾，正文内显式编号指引。
    payload = {'判分指令全文': text,
               '图片顺序': ['Image 1 = before（原图）', 'Image 2 = after（编辑结果图）']}
    values = {'payload': payload,
              'images': [encode_image_bytes(image_bytes(row['source_image']), max_edge),
                         encode_image_bytes(image_bytes(row['image']), max_edge)]}
    roles = [{'role': 'edit_source_before', 'sha256': row['source_image'].get('sha256')},
             {'role': 'edit_result_after', 'sha256': row['image'].get('sha256')}]
    return _bind_request(row, 'judge', values, run, pack, roles)


def _bind_request(row, stage, values, run, pack, roles=None):
    prompt = pack.prompt_definitions[stage]
    native = OperatorLLMRequest(stage, prompt.version, prompt.model.name,
        render_template(prompt.template, values), response_schema=prompt.response_schema,
        response_format=prompt.response_format)
    messages = request_messages(native)
    request = {'stage': stage, 'task_id': row['task_id'], 'messages': messages,
               'image_roles': roles or [], 'input_sha256': digest(messages),
               'prompt_version': native.prompt_version, 'response_envelope': 'text-result'}
    if run_manifest(Path(run))['config']['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            'image_roles': roles or [],
            stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def parse_scores(raw: str, etype: str) -> list:
    """Ported from eval_score.score_edit 解析：缺项按 1.0，随后硬钳制。"""
    dims = EDIT_DIMS[etype]
    scores = []
    for d in dims:
        m = re.search(re.escape(d) + r"\s*[:：]\s*([1-5](?:\.\d)?)", raw)
        scores.append(float(m.group(1)) if m else 1.0)
    scores[1] = min(scores[1], scores[0])
    scores[2] = min(scores[2], scores[0])
    return scores


def apply_judge(row, *, run, config):
    """Parse the free-text judge reply into the historical score row."""
    if row['status'] != 'generated':
        return row
    row = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    error = row.get('judge_error')
    if error:
        status = 'pending_judge' if error['type'] == 'PromptResponsePending' else 'failed_judge'
        return {**row, 'status': status, 'fail_reason': error['detail']}
    raw = row['judge_result']
    if not isinstance(raw, str) or not raw.strip():
        return {**row, 'status': 'failed_judge', 'fail_reason': 'judge returned empty text'}
    etype = row['edit_type']
    try:
        scores = parse_scores(raw, etype)
    except (KeyError, ValueError) as exc:
        return {**row, 'status': 'failed_judge', 'fail_reason': str(exc)}
    dims = EDIT_DIMS[etype]
    score = {"qid": row['qid'], "task": "edit", "edit_type": etype,
             "suite": row.get('suite', 'basic'),
             "dims": dict(zip(dims, scores)),
             "total": round(sum(scores) / 3, 2), "raw": raw}
    saved = {'binding': row['judge_binding'], 'score': score,
             'call': row.get('judge_call') or {}}
    ref = run_records(run).put('response/judge/' + row['task_id'] + '/' + digest(saved), saved)
    return {**row, 'score': score, 'status': 'scored', 'response_ref': ref.to_dict()}


def prompt_responses(run, stage='judge', task_prefix=None):
    from preparation.operaters.runfiles import response_records
    return response_records(run, stage, task_prefix)


def aggregate(rows: list) -> dict:
    """Ported from eval_score.aggregate."""
    scored = [r['score'] for r in rows if r.get('status') == 'scored']
    by_type: dict = {}
    for r in scored:
        by_type.setdefault(r["edit_type"], []).append(r)
    rep = {"mode": "edit", "n": len(scored),
           "judge_fail": sum(1 for r in rows if r.get('status') == 'failed_judge'),
           "pending": sum(1 for r in rows if r.get('status') == 'pending_judge'),
           "overall": round(sum(r["total"] for r in scored) / len(scored), 2) if scored else None,
           "by_type": {
               t: {"n": len(v),
                   "total": round(sum(r["total"] for r in v) / len(v), 2),
                   "dim_means": {
                       d: round(sum(r["dims"].get(d, 0) for r in v) / len(v), 2)
                       for d in EDIT_DIMS[t]}}
               for t, v in sorted(by_type.items())}}
    by_suite: dict = {}
    for r in scored:
        by_suite.setdefault(r.get("suite", "basic"), []).append(r)
    rep["by_suite"] = {s: round(sum(r["total"] for r in v) / len(v), 2)
                       for s, v in by_suite.items()}
    return rep
