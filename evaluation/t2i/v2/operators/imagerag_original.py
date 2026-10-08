"""固定 ImageRAG 16c9502 的业务状态机；不调用模型、不管理会话客户端。"""
import base64
import io
import json

from PIL import Image
from demiflow.objects import ObjectRef
from demiflow.operator_llm.template import compile_template, render_template

from .imagerag import dumps


def render(cfg, name, **values):
    parts = render_template(compile_template(cfg['original_prompt_spec']['templates'][name]), values)
    return ''.join(part.text for part in parts)


def original_image(ref, maximum):
    raw = ObjectRef(**ref).read(max_bytes=maximum)
    with Image.open(io.BytesIO(raw)) as image:
        suffix = {'PNG': 'png', 'JPEG': 'jpeg', 'WEBP': 'webp'}.get(image.format)
    if suffix is None:
        raise ValueError('Original VLM image must be PNG, JPEG or WEBP')
    return 'data:image/' + suffix + ';base64,' + base64.b64encode(raw).decode('ascii')


def text_message(role, text):
    return {'role': role, 'content': [{'type': 'text', 'text': text}]}


def prepare_messages(row, *, cfg, stage, attempt=1):
    expected = {'decision': 'ready', 'concepts': 'needs_concepts', 'captions': 'needs_captions'}[stage]
    row = {**row, 'rag_messages': [], 'rag_result': None, 'rag_call': None, 'rag_error': None}
    if row['rag_status'] != expected:
        return row
    messages = [text_message('user', render(cfg, 'decision', prompt=row['instruction']))]
    copies = 1
    if stage != 'decision':
        messages.extend([text_message('assistant', json.loads(row['decision_json'])['raw_text']),
                         text_message('user', render(cfg, 'concepts'))])
        copies = attempt
    if stage == 'captions':
        messages.extend([text_message('assistant', json.loads(row['concepts_json'])['raw_text']),
                         text_message('user', render(cfg, 'captions', k_captions_per_concept='1'))])
        copies = len(json.loads(row['concept_attempts_json'])) + 1
    # 上游修改 context_msgs[0]：每次概念尝试及 caption 调用都追加初图。
    text_chars = sum(len(part['text']) for message in messages for part in message['content'])
    if text_chars > cfg['max_context_chars']:
        return {**row, 'rag_status': 'diagnosis_failed', 'rag_reason': stage + ' exceeds complete context budget'}
    messages[0]['content'].extend({'type': 'image_url', 'image_url': {'url': row['rag_images'][0]}}
                                  for _ in range(copies))
    return {**row, 'rag_messages': messages}


def convert_res_to_captions(res):
    """逐句移植官方 utils.py；保留其编号/引号处理及异常边界。"""
    captions = [c.strip() for c in res.split('\n') if c != '']
    for i in range(len(captions)):
        if captions[i][0].isnumeric() and captions[i][1] == '.':
            captions[i] = captions[i][2:]
        elif captions[i][0] == '-':
            captions[i] = captions[i][1:]
        elif f'{i+1}.' in captions[i]:
            captions[i] = captions[i][captions[i].find(f'{i+1}.') + len(f'{i+1}.'):]
        captions[i] = captions[i].strip().replace("'", '').replace('"', '')
    return captions


def caption_result(raw, cfg):
    captions = convert_res_to_captions(raw)
    if not captions or any(not value for value in captions):
        raise ValueError('No usable retrieval captions')
    if len(captions) > cfg['max_queries']:
        raise ValueError('Retrieval caption count exceeds operational max_queries; no truncation')
    return {'raw_text': raw, 'captions': [{'concept_index': i, 'caption': c}
                                        for i, c in enumerate(captions, 1)]}


def finish_stage(row, *, cfg, stage, attempt=1):
    expected = {'decision': 'ready', 'concepts': 'needs_concepts', 'captions': 'needs_captions'}[stage]
    if row['rag_status'] != expected:
        return row
    raw, error = row.get('rag_result'), row.get('rag_error')
    call = row.get('rag_call') or (error or {}).get('call')
    row = {**row, stage + '_call_json': dumps(call) if call else None,
           stage + '_json': dumps({'raw_text': raw})}
    if stage == 'concepts':
        attempts = json.loads(row.get('concept_attempts_json') or '[]')
        attempts.append({'attempt': attempt, 'result': {'raw_text': raw}, 'call': call, 'error': error})
        row['concept_attempts_json'] = dumps(attempts)
    if error:
        return {**row, 'rag_status': 'diagnosis_pending' if error.get('type') == 'PromptResponsePending' else 'diagnosis_failed',
                'rag_reason': stage + ': ' + error['detail']}
    try:
        if not isinstance(raw, str) or len(raw) > cfg['max_context_chars']:
            raise ValueError('Expected bounded plain-text VLM response')
        if stage == 'decision':
            status = 'keep_initial' if 'yes' in raw.lower() else 'needs_concepts'
        elif stage == 'concepts':
            refused = 'unable' in raw or "can't" in raw
            if refused and attempt < 3:
                return row
            row['concepts_json'] = dumps({'raw_text': raw, 'concepts': raw.split('\n') if raw and not refused else []})
            if refused or raw == '':
                row.update(fallback_prompt=True, captions_json=dumps(caption_result(row['instruction'], cfg)))
                status = 'needs_retrieval'
            else:
                status = 'needs_captions'
        else:
            row['captions_json'] = dumps(caption_result(raw, cfg))
            status = 'needs_retrieval'
        return {**row, 'rag_status': status}
    except (ValueError, TypeError, KeyError, IndexError) as error:
        return {**row, 'rag_status': 'diagnosis_failed', 'rag_reason': stage + ': ' + str(error)}


def generation_examples(refs, cfg):
    return ', '.join(render(cfg, 'example', caption=ref['caption'], image_index=str(i))
                     for i, ref in enumerate(refs, 1))
