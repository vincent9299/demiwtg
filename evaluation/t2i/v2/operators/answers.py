"""答题材料准备、模板选择和结果落表；模型调用由平台原生图片算子执行。"""
import base64
import hashlib
import io
import json

from demiflow.execution.artifacts import digest
from demiflow.objects import ObjectRef


ANSWER_MODES = ('text_only', 'positive_images', 'legacy_references', 'imagerag')


def answer_service(config):
    """声明标准节点的本地 HTTP 服务；共享服务的租约与生命周期由平台管理。"""
    from demiflow.services import ManagedHTTPService
    from demiflow.services.shared_http import SharedHTTPService
    if config.get('shared_service') and config.get('service'):
        raise ValueError('Specify either shared_service or service')
    if config.get('shared_service'):
        return SharedHTTPService(**config['shared_service'])
    return ManagedHTTPService(**config['service']) if config.get('service') else None


def answer_mode(config):
    """旧 use_references 仅映射到历史图文材料路；新正例图路须明确指定。"""
    return config.get('answer_mode') or ('legacy_references' if config.get('use_references') else 'text_only')


def historical_answer_identity(config, row):
    """绑定实际题面、作答方式、图像顺序与生成参数，参考图变化不复用旧答案。"""
    mode = answer_mode(config)
    return digest({
        'contract': 't2i-answer-modes-2', 'task_id': row['task_id'], 'instruction': row['instruction'],
        'answer_mode': mode,
        'model': {key: config.get(key) for key in (
            'backend', 'model', 'model_path', 'revision', 'seed', 'parameters', 'api', 'base_url')},
        'references': (row.get('authoring_images_json') if mode == 'positive_images'
                       else row.get('references_json') if mode == 'legacy_references' else None),
        'authoring_variant': row.get('authoring_variant') if mode == 'positive_images' else None,
        'positive_image_limit': config.get('max_reference_images', 5) if mode == 'positive_images' else None,
        'positive_preprocessing': 'author-positive-1536-jpeg90' if mode == 'positive_images' else None,
        **({'provider_model': config['provider_model']} if config.get('provider_model') else {}),
    })


def positive_image_refs(row, maximum):
    """使用题目冻结的审核正例，保持顺序；缺失、越界或混入文字均报错，不静默降级。"""
    if row.get('authoring_variant') != 'with_positive_images':
        raise ValueError('Positive-image answers require a with_positive_images question snapshot')
    refs = json.loads(row.get('authoring_images_json') or '[]')
    if not isinstance(refs, list) or not 1 <= len(refs) <= maximum:
        raise ValueError(f'Expected 1..{maximum} frozen positive images; no truncation or text-only fallback')
    result = []
    for index, ref in enumerate(refs, 1):
        if not isinstance(ref, dict) or ref.get('kind') != 'image':
            raise ValueError('Positive references must contain images only')
        result.append({'number': index, 'object_ref': ObjectRef(**ref['object_ref']).to_dict()})
    return result


def answer_template(config):
    """唯一答题正文来自独立模板文件；配置保留装配时的模板快照。"""
    if config.get('prompt_template'):
        return config['prompt_template']
    from pathlib import Path
    import yaml
    pack=yaml.safe_load((Path(__file__).parents[1]/'prompts/answer.yaml').read_text())
    if pack['schema_version']!='demiflow_image_templates_v1':
        raise ValueError('Unsupported answer template schema')
    name=answer_mode(config)
    if name == 'imagerag' and config['imagerag'].get('prompt_protocol') == 'upstream_16c9502':
        name = 'imagerag_original'
    return {'name':name,**pack['prompts'][name]}


def answer_identity(config,row):
    identity = {'request':historical_answer_identity(config,row),'template':answer_template(config)}
    # 旧配置缺省即原 PNG 请求；无图初答身份不随参考图传输选项改变。
    if answer_mode(config) != 'text_only' and config.get('image_encoding', 'png') != 'png':
        identity['image_encoding'] = config['image_encoding']
    if config.get('backend') == 'codex_exec':
        identity.update(codex_exec=config['codex_exec'], codex_prompt_pack=config['codex_prompt_pack'])
    if answer_mode(config) == 'imagerag':
        if config.get('reference_preprocessing'):
            identity['reference_preprocessing'] = config['reference_preprocessing']
        from .imagerag import semantic_input
        identity.update(imagerag={k: v for k, v in config['imagerag'].items() if k != 'reuse'},
                        actual_retrieval=semantic_input(row))
    return digest(identity)


class PrepareAnswer:
    """只准备固定题目字段和图片；模型调用在主线 map_image_async 节点。"""
    def __init__(self,config,records):self.config,self.records=config,records

    def __call__(self,row):
        mode,key=answer_mode(self.config),answer_identity(self.config,row)
        if previous := row.get('rag_previous_answer'):
            # restore_input checked the original request. Keep its identity and actual call;
            # an old PNG image is not relabelled as a new preserve-encoding generation.
            key = previous['request_id']
            result = {k: v for k, v in previous.items() if k != 'request_id'}
            if not self.records.answer(key):
                self.records.save_answer(key, result)
            return {**row, **result, 'answer_ready': False, 'answer_key': key}
        if previous:=self.records.answer(key):
            return {**row,**previous,'answer_ready':False,'answer_key':key}
        if reused:=row.get('reused_answer'):
            if reused['request_id']!=key:
                raise ValueError('Reused answer template/input differs; start a new run without the old answer cache')
            result={k:v for k,v in reused.items() if k!='request_id'}
            self.records.save_answer(key,result)
            return {**row,**result,'answer_ready':False,'answer_key':key}
        result={**row,'answer_key':key,'answer_ready':False,
            'answer_model':self.config['model']+{'text_only':'','positive_images':'+正例参考图','legacy_references':'+参考信息','imagerag':'+ImageRAG'}[mode],
            'answer_mode':mode,'reference_images_json':'[]','reference_image_count':0,
            'status':'generation_failed','reason':'','image_json':None,'generation_seconds':None,
            'answer_call_json':None,'answer_images':[],'answer_materials':[]}
        try:
            if mode=='imagerag':
                trace=json.loads(row['rag_json'])
                if trace['status']=='keep_initial':
                    initial=trace['initial_answer']
                    result.update(status='generated', image_json=initial['image_json'],
                        answer_call_json=initial['answer_call_json'], generation_seconds=0.0)
                    return result
                if trace['status']!='retrieved':
                    result['reason']=trace['status']+': '+trace['reason']
                    return result
                refs=trace['references']
                if not 1 <= len(refs) <= self.config['max_reference_images']:
                    raise ValueError('ImageRAG references exceed the declared image budget')
                if self.config['imagerag'].get('prompt_protocol') == 'upstream_16c9502':
                    from .imagerag_original import generation_examples
                    result['answer_examples'] = generation_examples(refs, self.config['imagerag'])
                result['answer_captions']=[{'number':r['number'],'caption':r['caption']} for r in refs]
                result['answer_images']=[ObjectRef(**r['object_ref']).read(max_bytes=32*1024*1024) for r in refs]
                if self.config.get('reference_preprocessing'):
                    from PIL import Image
                    from demiflow.collect.image_resize import resize_image
                    sizes = []
                    for raw in result['answer_images']:
                        with Image.open(io.BytesIO(raw)) as picture:
                            sizes.append(picture.size)
                    aggregate_exceeded = sum(w*h for w,h in sizes) > 48_000_000
                    refs = [dict(r) for r in refs]
                    for i, (width, height) in enumerate(sizes):
                        if width*height <= 24_000_000 and not aggregate_exceeded:
                            continue
                        raw = resize_image(result['answer_images'][i], max_side=3840, quality=90)
                        result['answer_images'][i] = raw
                        with Image.open(io.BytesIO(raw)) as picture:
                            actual_size = picture.size
                        refs[i]['transport_preprocessing'] = {
                            'policy': self.config['reference_preprocessing'],
                            'original_size': [width, height], 'actual_size': list(actual_size),
                            'actual_sha256': hashlib.sha256(raw).hexdigest(), 'mime': 'image/jpeg'}
            elif mode=='positive_images':
                refs=positive_image_refs(row,self.config.get('max_reference_images',5))
                from benchmark.t2i.v2.operators.images import positive_image_data_url
                result['answer_images']=[base64.b64decode(positive_image_data_url(r['object_ref']).split(',',1)[1]) for r in refs]
            elif mode=='legacy_references':
                materials=json.loads(row['references_json'])
                if len(materials)>32:raise ValueError('Too many legacy materials')
                refs=[{'number':r['number'],'object_ref':r['object_ref']} for r in materials if r['kind']=='image']
                if len(refs)>self.config.get('max_reference_images',5):raise ValueError('Too many reference images')
                result['answer_materials']=[{k:v for k,v in r.items() if k!='object_ref'} for r in materials]
                result['answer_images']=[ObjectRef(**r['object_ref']).read(max_bytes=32*1024*1024) for r in refs]
            else:refs=[]
            result.update(reference_images_json=json.dumps(refs,ensure_ascii=False),reference_image_count=len(refs),answer_ready=True)
        except (ValueError,TypeError,KeyError,OSError) as exc:
            result['reason']=f'{type(exc).__name__}: {exc}'
        return result


class FinishAnswer:
    def __init__(self,records):self.records=records

    def __call__(self,row):
        if row.get('answer_ready'):
            call=row.get('answer_call')
            row={**row,'status':'generated' if row.get('generated_image') else 'generation_failed',
                'reason':row.get('answer_error') or '',
                'image_json':json.dumps(row['generated_image']) if row.get('generated_image') else None,
                'answer_call_json':json.dumps(call,ensure_ascii=False) if call else None,
                'generation_seconds':(call or {}).get('seconds')}
        fields=('task_id','concept','instruction','answer_model','answer_mode','reference_images_json',
                'reference_image_count','status','reason','image_json','generation_seconds','answer_call_json')
        result={k:row.get(k) for k in fields}
        if not self.records.answer(row['answer_key']):self.records.save_answer(row['answer_key'],result)
        return {k:v for k,v in row.items() if k not in ('answer_images','answer_materials','answer_captions','answer_examples','generated_image')}


def answer_bindings(config):
    mode=answer_mode(config)
    if mode == 'imagerag' and config['imagerag'].get('prompt_protocol') == 'upstream_16c9502':
        return {'instruction': 'instruction', 'images': 'answer_images', 'examples': 'answer_examples'}
    return {'instruction':'instruction',**({'images':'answer_images'} if mode!='text_only' else {}),
            **({'materials':'answer_materials'} if mode=='legacy_references' else {}),
            **({'captions':'answer_captions'} if mode=='imagerag' else {})}
