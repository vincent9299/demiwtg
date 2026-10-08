"""题目探测的行契约：Z-Image作答、GPT逐考点评审；不读业务表或创建子数据流。"""
import json
import math
from urllib.parse import urlsplit
import pyarrow as pa
from demiflow.execution.artifacts import digest
from .generation import prepare_generation, generate_image
from .images import image_data_url

POINT = pa.struct([('point', pa.string()), ('basis', pa.string()), ('criterion', pa.string())])
REF = pa.struct([('uri', pa.string()), ('sha256', pa.string())])
TABLE_REF = pa.struct([('uri', pa.string()), ('version', pa.int64())])
GENERATIONS = pa.schema([
    ('task_id', pa.string()), ('concept', pa.string()), ('instruction', pa.large_string()),
    ('test_points', pa.list_(POINT)), ('taxonomy', pa.list_(pa.string())), ('authoring_variant', pa.string()),
    ('generation_id', pa.string()), ('model', pa.string()), ('revision', pa.string()),
    ('status', pa.string()), ('reason', pa.large_string()), ('seed', pa.int64()),
    ('image_sha256', pa.string()), ('object_ref', REF), ('width', pa.int64()), ('height', pa.int64()),
    ('latency_s', pa.float64()), ('call_json', pa.large_string()),
])
POINT_RESULT = pa.struct([('index', pa.int64()), ('verdict', pa.string()),
                          ('evidence', pa.large_string()), ('reason', pa.large_string())])
CLASSIFICATION = pa.struct([(name, pa.string()) for name in (
    'knowledge_level', 'common_cn', 'visual_support', 'confidence', 'reason', 'caveat')]
    + [('sources', pa.list_(pa.string()))])
REVIEW = pa.struct([('verdict', pa.string()), ('score', pa.int64()),
    ('reason', pa.large_string()), ('basis', pa.large_string()), ('failure_type', pa.string()),
    ('point_results', pa.list_(POINT_RESULT)), ('case_annotation', CLASSIFICATION)])
REVIEWS = pa.schema([*GENERATIONS, ('generation_source', TABLE_REF),
    ('review_status', pa.string()), ('review_reason', pa.large_string()), ('review', REVIEW),
    ('case_category', pa.int64()),
    ('review_call_json', pa.large_string()), ('review_reasoning', pa.large_string())])


def probe_config(value, *, maximum):
    """明确部署版本和有限预算；服务由平台单独管理，配置解析不启动服务。"""
    if not isinstance(value, dict):
        raise ValueError('probe must be a configuration mapping')
    result = {'model':'Z-Image-Turbo', 'endpoints':['http://127.0.0.1:8003/v1'],
        'image_size':'1024x1024', 'steps':8, 'run_seed':0, 'concurrency':2, 'queue_depth':1,
        'timeout_s':600, 'max_image_bytes':32*1024*1024, 'max_generation_calls':maximum,
        'review_concurrency':2, 'review_queue_depth':1, 'max_review_calls':maximum,
        'review_timeout_s':600, 'review_max_output_tokens':8192, 'review_max_context_chars':60000}
    unknown = set(value) - set(result) - {'revision'}
    if unknown:
        raise ValueError('Unknown probe settings: ' + str(sorted(unknown)))
    result.update(value)
    if not isinstance(result.get('revision'),str) or not result['revision'].strip():
        raise ValueError('probe requires an explicit generation revision')
    if result['model'] != 'Z-Image-Turbo':
        raise ValueError('This probe uses the configured Z-Image-Turbo deployment')
    if not isinstance(result['endpoints'],list) or not 1 <= len(result['endpoints']) <= 8:
        raise ValueError('probe requires 1..8 equivalent local generation endpoints')
    for endpoint in result['endpoints']:
        u=urlsplit(endpoint)
        if u.scheme!='http' or u.hostname not in {'localhost','127.0.0.1','::1'} or u.path.rstrip('/')!='/v1' or u.username or u.password or u.query or u.fragment:
            raise ValueError('probe endpoints must be loopback /v1 services')
    for name in ('steps','concurrency','queue_depth','max_image_bytes','max_generation_calls',
                 'review_concurrency','review_queue_depth','max_review_calls',
                 'review_max_output_tokens','review_max_context_chars'):
        if type(result[name]) is not int or result[name]<1:
            raise ValueError('probe '+name+' must be a positive integer')
    if type(result['run_seed']) is not int:
        raise ValueError('probe run_seed must be an integer')
    for name in ('timeout_s','review_timeout_s'):
        if type(result[name]) not in (int,float) or not math.isfinite(result[name]) or result[name]<=0:
            raise ValueError('probe '+name+' must be finite and positive')
    dimensions=result['image_size'].split('x') if isinstance(result['image_size'],str) else []
    if len(dimensions)!=2 or any(not n.isdigit() or not 64<=int(n)<=4096 for n in dimensions):
        raise ValueError('probe image_size must be WxH with dimensions in 64..4096')
    return result


def candidate_row(row):
    """题目身份绑定作者依据与正例图；探测配置不参与题目身份。"""
    identity=['t2i-author-evidence-v1',row['concept'],row['question'],row['evidence_json'],
              row['authoring_images_json']]
    if row.get('concept_record'): identity.append(row['concept_record'])
    if row['authoring_context_json']!='[]': identity.append(row['authoring_context_json'])
    if row['authoring_variant']!='standard': identity.append(row['authoring_variant'])
    return {'task_id':'t2i_'+digest(identity), 'concept':row['concept'],'taxonomy':row['taxonomy'],
        'concept_record':row.get('concept_record'),'status':'unreviewed',**row['question'],
        'reasoning':row['reasoning'],'evidence_json':row['evidence_json'],
        'authoring_variant':row['authoring_variant'],'authoring_images_json':row['authoring_images_json'],
        'authoring_context_json':row['authoring_context_json']}


def answer_input(row):
    # 作答仅发送instruction；考点随行留给评审，不送生成端点。保存版本不进入缓存身份。
    return {name:row[name] for name in ('task_id','concept','taxonomy','instruction','test_points','authoring_variant')}


class AnswerImage:
    """单行生图I/O；由Dataset.map_cached控制复用、并发和生命周期。"""
    def __init__(self, settings, store):
        self.settings,self.store=settings,store
        self.concurrency,self.queue_depth=settings['concurrency'],settings['queue_depth']
        self.calls=0

    async def __call__(self,row):
        c=self.settings
        prepared=prepare_generation({**row,'prompt':row['instruction']},endpoints=c['endpoints'],
            model=c['model'],size=c['image_size'],run_seed=c['run_seed'],revision=c['revision'],steps=c['steps'])
        if self.calls>=c['max_generation_calls']:
            result={**row,'generation_id':prepared['_generation_id'],'model':c['model'],
                'revision':c['revision'],'status':'budget_exhausted','reason':'Probe generation call budget exhausted',
                'seed':prepared['_generation_request']['seed'],'width':0,'height':0,'latency_s':0.,'call_json':'{}'}
            return {name:result.get(name) for name in GENERATIONS.names}
        index=self.calls;self.calls+=1
        result=await generate_image(prepared,base_url=c['endpoints'][index%len(c['endpoints'])],
            revision=c['revision'],object_store=self.store,max_image_bytes=c['max_image_bytes'],timeout_s=c['timeout_s'])
        result.update(generation_id=prepared['_generation_id'],model=c['model'],revision=c['revision'])
        return {name:result.get(name) for name in GENERATIONS.names}


def prepare_review(row, *, max_context_chars, prompt_chars):
    payload={name:row[name] for name in ('concept','taxonomy','instruction','test_points')}
    out={**row,'review_payload':payload,'review_images':[],
         'review_status':'ready' if row['status']=='generated' else 'generation_failed',
         'review_reason':row['reason'] if row['status']!='generated' else ''}
    if out['review_status']=='ready':
        if prompt_chars+len(json.dumps(payload,ensure_ascii=False))>max_context_chars:
            out.update(review_status='needs_context_budget',review_reason='完整题目及判据超过评审上下文预算；未截断')
        else:
            try:out['review_images']=[image_data_url(row['object_ref'])]
            except (OSError,ValueError) as exc:out.update(review_status='invalid_image',review_reason=str(exc))
    return out


def check_review(row):
    error=row.get('review_error') or {}
    call=dict(row.get('review_call') or error.get('call') or {})
    reasoning=call.pop('reasoning',None)
    status,reason=row['review_status'],row['review_reason'];review=None
    if status=='ready' and error:
        status='pending' if error.get('type')=='PromptResponsePending' else 'failed';reason=error.get('detail','')
    elif status=='ready':
        review=row['review_result'];points=review['point_results']
        expected=list(range(1,len(row['test_points'])+1))
        actual=[p['index'] for p in points]
        if (actual!=expected or not review['reason'].strip() or not review['basis'].strip()
                or not review['case_annotation']['reason'].strip()
                or any(not p['evidence'].strip() or not p['reason'].strip() for p in points)):
            status,reason,review='invalid_response','评审必须逐项覆盖所有考点且包含具体可见证据和理由',None
        else:
            verdicts={p['verdict'] for p in points}
            verdict,score=review['verdict'],review['score']
            low,high={'pass':(7,10),'partial':(4,6),'fail':(0,3),'inconclusive':(-1,-1)}[verdict]
            inconsistent=(not low<=score<=high
                or ('invalid_criterion' in verdicts and verdict!='inconclusive')
                or (verdicts=={'pass'} and verdict!='pass')
                or (verdicts=={'fail'} and verdict!='fail')
                or (not verdicts & {'pass','fail'} and verdict!='inconclusive')
                or (verdict=='pass' and 'fail' in verdicts)
                or (verdict=='partial' and not {'pass','fail'}<=verdicts)
                or (verdict=='fail' and 'fail' not in verdicts)
                or ((review['failure_type']=='none') != (verdict=='pass'))
                or ((review['failure_type']=='insufficient_evidence') != (verdict=='inconclusive')))
            if inconsistent:
                status,reason,review='invalid_response','总体结论与逐项判断不一致',None
            else:status='reviewed'
    category=None
    if status=='reviewed':
        c=review['case_annotation']
        if c['knowledge_level']!='undetermined' and c['visual_support']!='insufficient':
            category=(3 if c['knowledge_level']=='specialist' else
                      1 if c['knowledge_level']=='everyday' and c['common_cn']=='yes'
                           and review['verdict'] in {'fail','partial'} else 2)
    result={**row,'review_status':status,'review_reason':reason,'review':review,
            'case_category':category,
            'review_call_json':json.dumps(call,ensure_ascii=False),'review_reasoning':reasoning}
    return {name:result.get(name) for name in REVIEWS.names}
