"""读取历史请求证据；不使用当前模板补写历史输入。"""
import base64
import hashlib
import json
from pathlib import Path
from demiflow.execution.file_ref import JsonArtifactRef
from demiflow.operator_llm.call_ref import read_call
from demiflow.objects import LocalObjectStore, ObjectRef
from .answers import historical_answer_identity
from .run_tables import RunTables


def answer_request(root, run, index, model, question, row):
    if row.get('answer_call_json'):
        call=json.loads(row['answer_call_json'])
        if call and call.get('transport') == 'codex_exec' and call.get('request_ref'):
            return read_call(call['request_ref'], root), call['request_ref'], 'codex'
        if call and call.get('input_ref'):
            return JsonArtifactRef(**call['input_ref']).read(), call['input_ref']['artifact_path'], 'native'
    key=historical_answer_identity(model,question)
    locations=[f'demiwtg/evaluation/t2i/v2/datasets/records__{run}__arm{index:02d}.lance']
    reuse=model.get('reuse_answers_from')
    if reuse:
        p=Path(reuse['uri'])
        p=p.with_name(p.name.replace('answer_results__','records__',1))
        locations.append(str(p.relative_to(root) if p.is_absolute() else p))
    for location in locations:
        record=RunTables(root,location).answer_request(key)
        if record:
            if 'artifact_path' in record:
                return JsonArtifactRef(**record).read(), record['artifact_path'], 'historical'
            return record, location+' / '+key, 'historical'
    return None, '', ''


def answer_view(root, run, index, model, question, row, preview):
    request, source, kind=answer_request(root,run,index,model,question,row)
    if request is None:
        return {'available':False,'note':'没有已保存的答题请求输入；不按当前模板推测。','images':[]}
    if kind == 'codex':
        text, refs = [], []
        store = LocalObjectStore(Path(root) / 'objects')
        for message in request['messages']:
            content = message['content']
            for part in ([{'type': 'text', 'text': content}] if isinstance(content, str) else content):
                if part['type'] == 'text': text.append(part['text'])
                elif part['type'] == 'image_url':
                    url = part['image_url']['url']
                    if isinstance(url, dict):
                        from benchmark.t2i.v2.operators.images import positive_image_data_url
                        attached = json.loads(row.get('reference_images_json') or '[]')
                        if len(refs) >= len(attached):
                            raise ValueError('Codex request has no matching attached reference')
                        actual = positive_image_data_url(attached[len(refs)]['object_ref'])
                        if hashlib.sha256(actual.encode()).hexdigest() != url.get('data_uri_sha256'):
                            raise ValueError('Codex attached image differs from its recorded input hash')
                        url = actual
                    refs.append(store.put(base64.b64decode(url.split(',', 1)[1], validate=True)).to_dict())
        call = json.loads(row['answer_call_json'])
        response = read_call(call['response_ref'], root) if call.get('response_ref') else {}
        return {'available': True, 'text': '\n\n'.join(text),
            'images': [preview(ref, 320) for ref in refs], 'image_refs': refs, 'source': source,
            'template': request.get('stage', 'answer_codex') + ' / ' + request.get('version', ''),
            'note': 'Codex模板渲染消息与实际附图；运行时文件上下文和原生工具事件在参数中保留。',
            'parameters': {'execution': request.get('execution'), 'file_context': response.get('file_context'),
                'native_events': response.get('stdout'), 'response_ref': call.get('response_ref')}}
    if kind=='historical':
        # 旧记录已保存实际 prompt 和所选图片对象；未保存供应商完整 wire body。
        refs=request.get('reference_images') or []
        return {'available':True,'text':request['instruction'],'images':[preview(r['object_ref'],320) for r in refs],
            'source':source,'image_refs':refs,'template':'历史请求（未使用答题模板）',
            'note':'文本、传图名单及顺序来自该次调用记录。图片按当时预处理生成预览；旧调用未保存完整 HTTP 请求体。',
            'parameters':request.get('config',{}).get('parameters',{})}
    body=request['body']
    images=body.get('image') or body.get('image[]') or [i['image_url']['url'] for i in body.get('input_references',[])]
    prompt=body.get('prompt')
    if 'messages' in body:
        content=body['messages'][0]['content']
        prompt=''.join(p['text'] for p in content if p['type']=='text')
        images=[p['image_url']['url'] for p in content if p['type']=='image_url']
    refs=[]
    store=LocalObjectStore(Path(root)/'objects')
    for url in images:
        raw=base64.b64decode(url.split(',',1)[1],validate=True)
        refs.append(store.put(raw).to_dict())
    return {'available':True,'text':prompt,'images':[preview(ref,320) for ref in refs],
        'source':source,'image_refs':refs,'template':request['template']['name']+' / '+request['template']['version'],
        'note':'读取算子调用前保存的完整输入；图像为实际传入的像素，按请求顺序显示。',
        'parameters':{k:v for k,v in body.items() if k not in {'prompt','image','image[]','messages','input_references'}}}


def judge_view(call_json, image_json, root, preview, *, positive_preprocessing=False):
    call=json.loads(call_json or 'null')
    if not call or not call.get('request_ref'): return None
    request=read_call(call['request_ref'],root)
    payload=request.get('payload') or (request if 'messages' in request else None)
    if not payload: return {'available':False,'note':'这条历史判分记录没有保存完整消息。'}
    messages=[]
    for message in payload.get('messages',[]):
        parts=[]
        content=message['content']
        for part in ([{'type':'text','text':content}] if isinstance(content,str) else content):
            if part['type']=='text': parts.append({'text':part['text']})
            elif part['type']=='image_url':
                value=part['image_url']['url']
                ref=json.loads(image_json) if image_json else None
                if isinstance(value,dict) and value.get('data_uri_sha256') and ref:
                    from .images import image_data_url
                    if positive_preprocessing:
                        from benchmark.t2i.v2.operators.images import positive_image_data_url
                        actual=positive_image_data_url(ref)
                    else:
                        actual=image_data_url(ref,root)
                    if hashlib.sha256(actual.encode()).hexdigest()!=value['data_uri_sha256']:
                        parts.append({'text':'判分输入图摘要与作答图不一致；未替换为其他图片。'});continue
                    if positive_preprocessing:
                        ref=LocalObjectStore(Path(root)/'objects').put(base64.b64decode(actual.split(',',1)[1])).to_dict()
                    parts.append({'image':preview(ref,320)})
                elif isinstance(value,str) and value.startswith('data:image/'):
                    ref=LocalObjectStore(Path(root)/'objects').put(base64.b64decode(value.split(',',1)[1])).to_dict()
                    parts.append({'image':preview(ref,320)})
                else: parts.append({'text':'原始图像记录：'+json.dumps(value,ensure_ascii=False)})
        messages.append({'role':message['role'],'parts':parts})
    return {'available':True,'messages':messages,'source':call['request_ref'],
        'parameters':{k:v for k,v in payload.items() if k!='messages'}}
