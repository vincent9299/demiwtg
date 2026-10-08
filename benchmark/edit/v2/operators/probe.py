"""Edit V2 单行探测：实际原图作答与一次前后图联合评审。"""
import json
import math
from urllib.parse import urlsplit

import pyarrow as pa
from demiflow.execution.artifacts import digest
from demiflow.image_edit import edit_image
from demiflow.schema import validate_instance
from .images import image_data_url

POINT = pa.struct([('point', pa.string()), ('basis', pa.string()), ('criterion', pa.string())])
REF = pa.struct([('uri', pa.string()), ('sha256', pa.string())])
TABLE_REF = pa.struct([('uri', pa.string()), ('version', pa.int64())])
GENERATIONS = pa.schema([
    ('task_id', pa.string()), ('concept', pa.string()), ('instruction', pa.large_string()),
    ('test_points', pa.list_(POINT)), ('taxonomy', pa.list_(pa.string())), ('edit_source', REF),
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
    if not isinstance(value, dict):
        raise ValueError('probe must be a configuration mapping')
    result = {'model': 'Qwen-Image-2.1', 'base_url': 'http://127.0.0.1:8005/v1',
        'image_size': '1024x1024', 'steps': 40, 'run_seed': 0, 'concurrency': 1, 'queue_depth': 1,
        'timeout_s': 600, 'max_image_bytes': 32*1024*1024, 'max_generation_calls': maximum,
        'review_concurrency': 1, 'review_queue_depth': 1, 'max_review_calls': maximum,
        'review_timeout_s': 600, 'review_max_output_tokens': 8192, 'review_max_context_chars': 60000,
        'review_reasoning_effort': 'xhigh'}
    unknown = set(value) - set(result) - {'revision'}
    if unknown:
        raise ValueError('Unknown probe settings: ' + str(sorted(unknown)))
    result.update(value)
    if not isinstance(result.get('revision'), str) or not result['revision'].strip():
        raise ValueError('probe requires an explicit generation deployment revision')
    if result['model'] != 'Qwen-Image-2.1':
        raise ValueError('Edit probe requires the local Qwen-Image-2.1 editing deployment')
    u = urlsplit(result['base_url'])
    if u.scheme != 'http' or u.hostname not in {'localhost','127.0.0.1','::1'} or u.path.rstrip('/') != '/v1' or u.username or u.password or u.query or u.fragment:
        raise ValueError('probe base_url must be a loopback /v1 service')
    for name in ('steps','concurrency','queue_depth','max_image_bytes','max_generation_calls',
                 'review_concurrency','review_queue_depth','max_review_calls',
                 'review_max_output_tokens','review_max_context_chars'):
        if type(result[name]) is not int or result[name] < 1:
            raise ValueError('probe ' + name + ' must be a positive integer')
    if result['max_image_bytes'] > 32*1024*1024:
        raise ValueError('probe max_image_bytes exceeds the local service limit')
    if type(result['run_seed']) is not int:
        raise ValueError('probe run_seed must be an integer')
    for name in ('timeout_s', 'review_timeout_s'):
        if type(result[name]) not in (int,float) or not math.isfinite(result[name]) or result[name] <= 0:
            raise ValueError('probe ' + name + ' must be finite and positive')
    if result['review_reasoning_effort'] != 'xhigh':
        raise ValueError('Edit review uses reasoning_effort=xhigh')
    dimensions = result['image_size'].split('x') if isinstance(result['image_size'],str) else []
    if len(dimensions) != 2 or any(not n.isdigit() or not 256 <= int(n) <= 4096 or int(n) % 32 for n in dimensions):
        raise ValueError('Qwen image_size must be multiples of 32 in 256..4096')
    return result


def candidate_row(row):
    return {'task_id': 'edit_' + digest([row['concept'], row['edit_source']['sha256'],
                row['question']['instruction'], row['question']['test_points']]),
        'concept': row['concept'], 'taxonomy': row['taxonomy'], 'status': 'unreviewed', **row['question'],
        **{name: row[name] for name in ('seed_asset','edit_source','references_json','reasoning','call_json')}}


def answer_input(row):
    # These metadata stay in the row; only instruction + edit_source enter edit_image.
    return {name: row[name] for name in ('task_id','concept','taxonomy','instruction','test_points','edit_source')}


class AnswerImage:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.concurrency, self.queue_depth = settings['concurrency'], settings['queue_depth']
        self.calls = 0

    async def __call__(self, row):
        c = self.settings
        seed = int(digest([c['run_seed'], row['edit_source']['sha256'], row['instruction']])[:16], 16) % (2**31)
        if self.calls >= c['max_generation_calls']:
            result = {'model': c['model'], 'revision': c['revision'], 'seed': seed,
                'status': 'budget_exhausted',
                'reason': 'Generation call budget exhausted',
                'width': 0, 'height': 0, 'latency_s': 0., 'call_json': '{}'}
        else:
            self.calls += 1
            result = await edit_image(source=row['edit_source'], instruction=row['instruction'],
                model=c['model'], revision=c['revision'], base_url=c['base_url'], size=c['image_size'],
                steps=c['steps'], seed=seed, object_store=self.store,
                timeout_s=c['timeout_s'], max_image_bytes=c['max_image_bytes'])
        return {name: {**row, **result}.get(name) for name in GENERATIONS.names}


def prepare_review(row, *, max_context_chars, prompt_chars):
    payload = {name: row[name] for name in ('concept','taxonomy','instruction','test_points')}
    result = {**row, 'review_payload': payload, 'review_images': [],
        'review_status': 'ready' if row['status'] == 'generated' else 'generation_failed',
        'review_reason': row['reason'] if row['status'] != 'generated' else ''}
    if result['review_status'] == 'ready':
        if prompt_chars + len(json.dumps(payload, ensure_ascii=False)) > max_context_chars:
            result.update(review_status='needs_context_budget', review_reason='Complete review text exceeds budget; no truncation')
        else:
            try:
                result['review_images'] = [image_data_url(row['edit_source']), image_data_url(row['object_ref'])]
            except (OSError, ValueError) as exc:
                result.update(review_status='invalid_image', review_reason=str(exc))
    return result

def check_review(row, *, response_schema):
    error=row.get('review_error') or {}
    call=dict(row.get('review_call') or error.get('call') or {})
    reasoning=call.pop('reasoning',None)
    status,reason=row['review_status'],row['review_reason'];review=None
    if status=='ready' and error:
        status='pending' if error.get('type')=='PromptResponsePending' else 'failed';reason=error.get('detail','')
    elif status=='ready':
        review=row['review_result']
        try:
            validate_instance({'result': review}, response_schema, label='review')
        except ValueError as exc:
            return {name: {**row, 'review_status': 'invalid_response', 'review_reason': str(exc),
                'review': None, 'case_category': None, 'review_call_json': json.dumps(call, ensure_ascii=False),
                'review_reasoning': reasoning}.get(name) for name in REVIEWS.names}
        points=review['point_results']
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
            inconsistent=((type(score) is not int or not low<=score<=high)
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
