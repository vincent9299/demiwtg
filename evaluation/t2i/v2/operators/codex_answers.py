"""Codex 答题的单行模板输入与原生产物绑定；模型调用留在入口。"""
import base64
import hashlib
import io
import json
from pathlib import PurePosixPath

from PIL import Image
from demiflow.objects import ObjectRef
from demiflow.operator_llm.model import TextPart
from demiflow.operator_llm.template import compile_template, render_template
from .answers import answer_template


def prepare_inputs(row, *, model):
    if not row.get('answer_ready'):
        return {**row, 'codex_answer_text': '', 'codex_images': []}
    urls = ['data:image/jpeg;base64,' + base64.b64encode(raw).decode() for raw in row.get('answer_images', [])]
    template = compile_template(answer_template(model)['template'])
    values = {'instruction': row['instruction']}
    if model['answer_mode'] == 'positive_images':
        values['images'] = urls
    parts = render_template(template, values)
    text = ''.join(part.text for part in parts if isinstance(part, TextPart))
    if len(text) > model['max_context_chars']:
        raise ValueError('Complete Codex answer exceeds configured context budget')
    return {**row, 'codex_answer_text': text, 'codex_images': urls}


def finish_generation(row, *, max_artifact_bytes):
    if not row.get('answer_ready'):
        return row
    error = row.get('codex_error') or {}
    call = dict(row.get('codex_call') or error.get('call') or {})
    out = {**row, 'answer_call': {**call, 'seconds': call.get('elapsed_s')},
        'generated_image': None, 'answer_error': ''}
    if error:
        out['answer_error'] = error.get('detail') or json.dumps(error, ensure_ascii=False)
        return out
    try:
        result = row['codex_result']
        if result['status'] != 'generated':
            raise ValueError(result['reason'] or 'Codex did not generate an image')
        name = result['artifact']
        if not name or PurePosixPath(name).name != name or '\\' in name:
            raise ValueError('Codex artifact must be one exported filename')
        if call.get('transport') != 'codex_exec' or call.get('image_generation') is not True:
            raise ValueError('Image requires native Codex generation evidence')
        artifacts = call.get('artifacts', [])
        if len(artifacts) != 1 or artifacts[0]['name'] != name:
            raise ValueError('Expected exactly the declared image in this call artifacts')
        artifact = artifacts[0]
        if not 0 < artifact['byte_size'] <= max_artifact_bytes:
            raise ValueError('Codex image exceeds artifact byte budget')
        ref = ObjectRef(**artifact['object_ref'])
        raw = ref.read(max_bytes=max_artifact_bytes)
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > 20_000_000:
                raise ValueError('Codex image exceeds 20 million pixels')
            im.load()
        if any(ref.sha256 == hashlib.sha256(value).hexdigest() for value in row.get('answer_images', [])):
            raise ValueError('Codex returned an attached reference unchanged')
        out['generated_image'] = ref.to_dict()
    except (KeyError, TypeError, ValueError, OSError) as exc:
        out['answer_error'] = f'{type(exc).__name__}: {exc}'
    return {k: v for k, v in out.items() if k not in ('codex_images', 'codex_answer_text')}
