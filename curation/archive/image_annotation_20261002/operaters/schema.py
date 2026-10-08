"""中性图片证据的字段、身份和校验；不包含概念匹配或用途判断。"""
import json
import pyarrow as pa
from demiflow.execution.artifacts import digest
from preparation.images.catalog.operaters.schema import DESCRIPTION_RECORD, IMAGE_SCORE

IMAGE_INPUTS = pa.schema([
    pa.field('sha256', pa.string(), nullable=False), ('image_uri', pa.string()),
    ('status', pa.string()), ('reason', pa.large_string()),
])
IMAGE_RESULTS = pa.schema([
    pa.field('sha256', pa.string(), nullable=False), ('image_uri', pa.string()),
    ('annotation_id', pa.string()), ('config_id', pa.string()), ('status', pa.string()),
    ('description_record', DESCRIPTION_RECORD), ('score_record', IMAGE_SCORE),
    ('richness', pa.int32()), ('input_sha256', pa.string()), ('attempts', pa.int64()),
    ('response_ref', pa.large_string()), ('error', pa.large_string()),
    ('details_json', pa.large_string()),
])
PATCH_ROW = pa.schema([
    pa.field('sha256', pa.string(), nullable=False),
    ('descriptions', pa.list_(DESCRIPTION_RECORD)), ('image_scores', pa.list_(IMAGE_SCORE)),
])
SUMMARY = pa.schema([
    ('run', pa.string()), ('through', pa.string()), ('model_mode', pa.string()),
    ('input_count', pa.int64()), ('required_count', pa.int64()), ('skipped_count', pa.int64()),
    ('done_count', pa.int64()), ('reused_count', pa.int64()), ('error_count', pa.int64()),
    ('stage_complete', pa.bool_()), ('complete', pa.bool_()), ('committed', pa.bool_()),
    ('target_version', pa.int64()), ('details_json', pa.large_string()),
])
REP = set('photo illustration diagram flowchart map chart document screenshot mixed unknown'.split())
VIEWS = set('front side top oblique closeup interior cross_section exploded multi_panel stage_comparison unknown'.split())
ISSUES = set('blur occlusion cropping small_text watermark glare other'.split())


def semantic_config_id(pack, config):
    """仅图片节点的实际语义；范围、run、并发及其他 pipeline 不改变身份。"""
    prompt = pack.prompt_definitions['describe_image']
    return 'image-' + digest({
        'version': prompt.version, 'template': prompt.template.source,
        'response_schema': json.loads(json.dumps(dict(prompt.response_schema), default=dict)),
        'model': config['model'], 'base_url': config['base_url'],
        'model_revision': config['model_revision'],
        'max_edge': config['max_edge'], 'jpeg_quality': config['jpeg_quality'],
        'temperature': config['temperature'], 'max_output_tokens': config['max_output_tokens'],
        'enable_thinking': config['enable_thinking'],
    })[:24]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def image_annotation_id(sha256, image_config_id, input_sha256):
    """图片级标注 ID = SHA + 实际模型输入摘要 + 图片级协议/模型配置；不含 run 名。

    描述记录与图片评分记录共用该 ID；同 ID 同业务内容幂等，同 ID 不同内容按冲突报错。
    """
    return digest(['image_annotation_v1', sha256, image_config_id, input_sha256])[:32]


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_description(value):
    """中性描述契约校验：与公共 DESCRIPTION 字段一一对应，缺项/越界/空值均拒绝。

    caption 的 80-180 汉字是 prompt 目标而非硬校验；这里只拒绝空值与结构错误。
    """
    if not isinstance(value, dict) or not nonempty(value.get('caption')) or value.get('representation') not in REP:
        raise ValueError('invalid caption/representation')
    tags = value.get('view_tags')
    if (not isinstance(tags, list) or not tags or len(tags) > 12
            or any(t not in VIEWS for t in tags)):
        raise ValueError('invalid views')
    for key, limit, fields in [('objects', 8, ('name', 'location', 'visible_features')),
                               ('text_regions', 12, ('location',)),
                               ('observability_issues', 20, ('location', 'detail'))]:
        rows = value.get(key)
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError('invalid ' + key)
        for row in rows:
            if not isinstance(row, dict) or any(not nonempty(row.get(f)) for f in fields):
                raise ValueError('invalid ' + key + ' item')
    for r in value['text_regions']:
        if not isinstance(r.get('text'), str) or r.get('readability') not in ('readable', 'partial', 'unreadable'):
            raise ValueError('invalid OCR')
        if r['readability'] != 'unreadable' and not nonempty(r['text']):
            raise ValueError('empty readable OCR')
    for r in value['observability_issues']:
        if r.get('type') not in ISSUES:
            raise ValueError('invalid issue type')
    issues = value.get('uncertainties')
    if (not isinstance(issues, list) or len(issues) > 8
            or any(not nonempty(v) for v in issues)):
        raise ValueError('invalid uncertainty')
    return value


def _score(value, name):
    """0-10 严格整数：拒绝 bool、字符串、浮点与越界；null（None）合法表示无法确认。"""
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 10:
        raise ValueError('invalid ' + name)
    return value


def validate_image_response(result):
    """图片级响应业务校验：结构合法 + richness/richness_reason 严格性。"""
    if not isinstance(result, dict):
        raise ValueError('image response must be an object')
    description = validate_description(result.get('description'))
    richness = _score(result.get('richness'), 'richness')
    if richness is None:
        raise ValueError('richness must be a concrete 0-10 integer')
    if not nonempty(result.get('richness_reason')):
        raise ValueError('invalid richness_reason')
    return description, richness, result['richness_reason']


def record_provenance(*, run_id, config_id, model, details):
    """公共 PROVENANCE 记录：details 只放固定输入引用、请求/响应引用与输入摘要。"""
    return {
        'run_id': run_id, 'config_id': config_id,
        'config_path': 'preparation/images/annotation/prompts/tasks.yaml',
        'model': model, 'source_file': None, 'source_row': None,
        'record_sha256': digest(details), 'details_json': canonical(details),
    }


def call_reference(call):
    """调用元数据 → 可落表的响应引用；剔除 reasoning 大文本，保留复用/耗时/usage。"""
    call = dict(call or {})
    call.pop('reasoning', None)
    if isinstance(call.get('attempts'), list):
        call['attempts'] = [{k: v for k, v in attempt.items() if k != 'reasoning'}
                            for attempt in call['attempts']]
    return canonical(call)

