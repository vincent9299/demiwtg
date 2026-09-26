"""按 tasks.yaml 的 v6.0-V2 协议判分，保留原有分数归一与一次 schema 重试。"""
import base64
import io
import json
from pathlib import Path

import yaml
from demiflow.operator_llm.client import request_messages
from demiflow.operator_llm.lance_journal import materialize as materialize_offline
from demiflow.operator_llm.model import OperatorLLMRequest
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow.operator_llm.template import render_template

from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import prompt_store, read_record, run_manifest, run_records

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'

V60_AXES = ["subject_presence", "form_structure", "color_material",
            "quantity_scale", "spatial_relation", "text_symbol",
            "action_interaction", "state_context", "scene_environment", "style"]
V60_QUALITY = ["physical_logic", "material_texture", "detail_richness",
               "artifacts", "resolution", "edge_clarity", "naturalness",
               "anatomical_fidelity"]
V60_AESTHETICS = ["composition", "color_harmony", "lighting_atmosphere",
                  "emotional_expression"]
V60_DIMS = {"alignment": V60_AXES, "quality": V60_QUALITY,
            "aesthetics": V60_AESTHETICS}
PHI = {0: 0.0, 1: 60.0, 2: 100.0}


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
                   'request_options': {'temperature': 0.0, 'seed': 42,
                                       'max_tokens': model['max_output_tokens'],
                                       'response_format': {'type': 'json_object'}}}
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
    """Ported from eval_score.encode_image (bytes source, JPEG data URL)."""
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
    """Answer image bytes by blob ref or recorded path (identity re-checked)."""
    from preparation.operaters.inputs import asset_pixels
    return asset_pixels(image)


def prepare_judge(row, *, run, pack, config):
    """绑定本题题面、答题模型及生成图；固定规则由 YAML 模板提供。"""
    if row['status'] != 'generated':
        return row
    # 固定规则只在 YAML 中；原始题面按变量绑定，不再次解析其中的占位文本。
    values = {'gen_prompt': str(row['gen_prompt'] or ''), 'image_model': row.get('image_model'),
              'images': [encode_image_bytes(image_bytes(row['image']), config.get('max_edge', 1024))]}
    roles = [{'role': 'generated_image', 'sha256': row['image'].get('sha256')}]
    return _bind_request(row, 'judge', values, run, pack, roles)


def _bind_request(row, stage, values, run, pack, roles=None):
    prompt = pack.prompt_definitions[stage]
    native = OperatorLLMRequest(stage, prompt.version, prompt.model.name,
        render_template(prompt.template, values), response_schema=prompt.response_schema)
    messages = request_messages(native)
    request = {'stage': stage, 'task_id': row['task_id'], 'messages': messages,
               'image_roles': roles or [], 'input_sha256': digest(messages),
               'prompt_version': native.prompt_version, 'response_envelope': 'result'}
    if run_manifest(Path(run))['config']['mode'] == 'offline':
        _, source = materialize_offline(native, prompt_store(run))
        request['native_offline'] = {'request_ref': source.to_dict()}
    ref = run_records(run).put('request/' + stage + '/' + row['task_id'] + '/' + request['input_sha256'], request)
    return {**row, **{'prompt_' + k: v for k, v in values.items()},
            'image_roles': roles or [],
            stage + '_binding': {'request_ref': ref.to_dict(), 'input_sha256': request['input_sha256']}}


def _v60_norm(v):
    """Ported from eval_score._v60_norm：档位归一，非法 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v in (0, 1, 2):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.upper() in ("N/A", "NA"):
            return "N/A"
        if s in ("0", "1", "2"):
            return int(s)
    return None


def validate_v60(parsed: dict) -> str:
    """Ported from eval_score.validate_v60：三维度 + 三个 *_reasons 全键校验。"""
    errs = []
    for dim, keys in V60_DIMS.items():
        obj = parsed.get(dim)
        if not isinstance(obj, dict):
            errs.append(f"{dim} 非 object")
            continue
        got = set(obj)
        if missing := [k for k in keys if k not in got]:
            errs.append(f"{dim} 缺键 {missing}")
        if extra := sorted(got - set(keys)):
            errs.append(f"{dim} 多键 {extra}")
        for k in sorted(got & set(keys)):
            if _v60_norm(obj[k]) is None:
                errs.append(f"{dim}.{k}={obj[k]!r} 越界")
        rkey = f"{dim}_reasons"
        robj = parsed.get(rkey)
        if not isinstance(robj, dict):
            errs.append(f"{rkey} 非 object")
        elif rmiss := [k for k in keys if k not in robj]:
            errs.append(f"{rkey} 缺键 {rmiss}")
    return "; ".join(errs)


def finalize_v60(q: dict, parsed: dict, raw, cond: str, model: str, source: str) -> dict:
    """Ported from eval_score.finalize_v60：已校验 judge JSON → 三线分 + 全量明细。"""

    def _line(dim: str) -> float | None:
        vals = [PHI[n] for k in V60_DIMS[dim]
                if (n := _v60_norm(parsed[dim][k])) in (0, 1, 2)]
        return round(sum(vals) / len(vals), 2) if vals else None

    scores = {d: {k: _v60_norm(parsed[d][k]) for k in V60_DIMS[d]} for d in V60_DIMS}
    reasons = {d: {k: (parsed.get(f"{d}_reasons") or {}).get(k)
                   for k in V60_DIMS[d]} for d in V60_DIMS}
    nas = {d: [k for k, v in scores[d].items() if v == "N/A"] for d in V60_DIMS}
    return {
        "qid": q.get("qid"), "task": "t2i", "schema": f"v6.0-{cond}",
        "judge_model": model, "image_model": source, "level": q.get("level"),
        "alignment_score": _line("alignment"),
        "quality_score": _line("quality"),
        "aesthetic_score": _line("aesthetics"),
        "alignment_scores": scores["alignment"],
        "quality_scores": scores["quality"],
        "aesthetic_scores": scores["aesthetics"],
        "alignment_reasons": reasons["alignment"],
        "quality_reasons": reasons["quality"],
        "aesthetic_reasons": reasons["aesthetics"],
        "na_keys": nas,
        "raw": raw,
    }


def apply_judge(row, *, run, config):
    """Normalize the judge result into the historical three-line score row."""
    if row['status'] != 'generated':
        return row
    row = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    error = row.get('judge_error')
    if error:
        status = 'pending_judge' if error['type'] == 'PromptResponsePending' else 'failed_judge'
        return {**row, 'status': status, 'fail_reason': error['detail']}
    parsed = row['judge_result']
    call = row.get('judge_call') or {}
    invalid = validate_v60(parsed)
    if invalid:
        return {**row, 'status': 'failed_judge', 'fail_reason': invalid}
    score = finalize_v60(row, parsed, json.dumps(parsed, ensure_ascii=False),
                         config.get('cond', 'V2'), config.get('judge_model', 'local'),
                         row.get('image_model'))
    saved = {'binding': row['judge_binding'], 'score': score, 'call': call}
    ref = run_records(run).put('response/judge/' + row['task_id'] + '/' + digest(saved), saved)
    return {**row, 'score': score, 'status': 'scored', 'judge_provenance': call,
            'response_ref': ref.to_dict()}


def prompt_responses(run, stage='judge', task_prefix=None):
    from preparation.operaters.runfiles import response_records
    return response_records(run, stage, task_prefix)


def aggregate(rows: list) -> dict:
    """Ported from eval_score.aggregate (v60 branch)."""
    scored = [r['score'] for r in rows if r.get('status') == 'scored']

    def mean(key: str):
        vals = [s[key] for s in scored if isinstance(s.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {"mode": "t2i-v60", "condition": "V2",
            "n": len(scored),
            "judge_fail": sum(1 for r in rows if r.get('status') == 'failed_judge'),
            "pending": sum(1 for r in rows if r.get('status') == 'pending_judge'),
            "alignment": mean("alignment_score"),
            "quality": mean("quality_score"),
            "aesthetics": mean("aesthetic_score")}
