"""A/B评分的单行转换；原生prompt节点由主入口编排，本模块不调用模型。"""
import json
import pyarrow as pa
from benchmark.t2i.v2.operators.probe import check_review
from .images import image_data_url

PAIRED_FIELDS = [
    ('judge_model', pa.string()), ('test_points_json', pa.large_string()), ('taxonomy_json', pa.large_string()),
    *[(f'{prefix}_{field}', pa.large_string()) for prefix in ('a', 'b')
      for field in ('status', 'reason', 'json', 'call_json')],
    ('a_score', pa.float64()), ('alignment_score', pa.float64()), ('quality_score', pa.float64()),
    ('aesthetics_score', pa.float64()), ('a_valid_items_json', pa.string()),
    ('b_score', pa.float64()), ('b_raw_score', pa.int64()), ('b_verdict', pa.string()),
]


def prepare_judging(row, *, root, judge, max_context_chars):
    """从本行生成图读取像素；完整题面/判据超预算则留失败，不截断。"""
    payload = {key: row.get(key) for key in ('concept', 'taxonomy', 'instruction', 'test_points')}
    out = {**row, 'judge_model': judge['model'], 'prompt_images': [], 'probe_payload': payload,
           'test_points_json': json.dumps(row.get('test_points'), ensure_ascii=False),
           'taxonomy_json': json.dumps(row.get('taxonomy'), ensure_ascii=False),
           'a_status': 'ready', 'a_reason': '', 'b_status': 'ready', 'b_reason': ''}
    for field, _ in PAIRED_FIELDS:
        out.setdefault(field, None)
    if row['status'] != 'generated':
        out.update(a_status='generation_failed', b_status='generation_failed',
                   a_reason=row['reason'], b_reason=row['reason'])
        return out
    if len(json.dumps(payload, ensure_ascii=False)) > max_context_chars:
        out.update(a_status='needs_context_budget', b_status='needs_context_budget',
                   a_reason='完整评审输入超过预算；未截断', b_reason='完整评审输入超过预算；未截断')
        return out
    if not isinstance(row.get('test_points'), list) or not row['test_points']:
        out.update(b_status='invalid_criteria', b_reason='固定题表缺少考点与判据')
    try:
        out['prompt_images'] = [image_data_url(json.loads(row['image_json']), root)]
    except (OSError, ValueError, TypeError, KeyError) as error:
        out.update(a_status='invalid_image', b_status='invalid_image', a_reason=str(error), b_reason=str(error))
    return out


def a_score(row):
    """A按有效维度等权平均，先保留精度再展示；N/A与失败均不写0。"""
    error = row.get('a_error') or {}
    out = {**row, 'a_call_json': json.dumps(row.get('a_call') or error.get('call') or {}, ensure_ascii=False)}
    if row['a_status'] != 'ready':
        return out
    if error:
        return {**out, 'a_status': 'pending' if error.get('type') == 'PromptResponsePending' else 'failed',
                'a_reason': error.get('detail', '')}
    result = row.get('a_result')
    out['a_json'] = json.dumps(result, ensure_ascii=False)
    try:
        means, counts = {}, {}
        for dimension in ('alignment', 'quality', 'aesthetics'):
            values = list(result[dimension].values())
            if not all((type(v) is int and v in (0, 1, 2)) or v == 'N/A' for v in values):
                raise ValueError('评分仅接受0/1/2/N/A')
            valid = [{0: 0, 1: 60, 2: 100}[v] for v in values if v != 'N/A']
            counts[dimension] = len(valid)
            means[dimension] = sum(valid) / len(valid) if valid else None
        valid_means = [v for v in means.values() if v is not None]
        out.update(a_status='scored', a_reason='',
                   a_score=sum(valid_means) / len(valid_means) if valid_means else None,
                   a_valid_items_json=json.dumps(counts),
                   **{dimension + '_score': value for dimension, value in means.items()})
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        out.update(a_status='invalid_response', a_reason=str(error))
    return out


def b_score(row):
    """复用benchmark维护的逐考点一致性校验；有效分×10，-1保持空分。"""
    out = {**row, 'b_json': json.dumps(row.get('b_result'), ensure_ascii=False)}
    checked = check_review({**row, 'review_status': row['b_status'], 'review_reason': row['b_reason'],
                            'review_result': row.get('b_result'), 'review_call': row.get('b_call'),
                            'review_error': row.get('b_error')})
    out.update(b_status=checked['review_status'], b_reason=checked['review_reason'],
               b_call_json=checked['review_call_json'])
    if checked['review_status'] == 'reviewed':
        result = checked['review']
        out.update(b_raw_score=result['score'], b_verdict=result['verdict'],
                   b_score=result['score'] * 10 if result['score'] >= 0 else None)
    return out


class FailureLimit:
    """已持久保存的连续技术失败达到上限后停止该路，避免全批重复消耗故障服务。"""
    def __init__(self, maximum, field, accepted):
        self.maximum, self.field, self.accepted = maximum, field, set(accepted)
        self.consecutive = 0

    def __call__(self, row):
        self.consecutive = 0 if row[self.field] in self.accepted else self.consecutive + 1
        if self.consecutive >= self.maximum:
            raise RuntimeError(f'{self.field}: 连续{self.consecutive}条技术失败，已保存结果并停止该路')
        return row
class ValidateQuestions:
    """小批题目逐行校验；只保存有显式行数和字节上限的ID集合。"""

    def __init__(self, maximum, max_id_bytes=4 * 1024 * 1024):
        self.maximum, self.max_id_bytes = maximum, max_id_bytes
        self.ids, self.id_bytes = set(), 0

    def __call__(self, row):
        identity = row.get('task_id')
        if not isinstance(identity, str) or not identity or not row.get('instruction') or not row.get('test_points'):
            raise ValueError('Each question requires task_id, instruction and original test_points')
        if identity in self.ids:
            raise ValueError('Duplicate task IDs')
        size = len(identity.encode('utf-8'))
        if len(self.ids) >= self.maximum or self.id_bytes + size > self.max_id_bytes:
            raise ValueError('Question identity validation exceeds its row/byte budget')
        self.ids.add(identity)
        self.id_bytes += size
        return row

