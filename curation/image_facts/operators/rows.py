"""图片事实契约：正例身份仅溯源，模型只看实际像素。"""
import io
import json
from PIL import Image
import pyarrow as pa
from demiflow.schema import SchemaValidationError, validate_instance
from demiflow.operator_llm.call_ref import read_call
from demiflow.objects import ObjectRef
from preparation.concept_positive_images.operators.pixels import encode_pixels


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


SCOPE = pa.schema([(k, pa.string()) for k in ('sha256', 'image_uri', 'bindings_json')])
LABELS = pa.schema([(k, pa.large_string()) for k in
    ('sha256', 'image_uri', 'bindings_json', 'model_key', 'model', 'batch_id', 'status', 'error',
     'input_sha256', 'caption', 'composition', 'framing', 'text_presence', 'watermark_presence',
     'annotation_json', 'call_json')] + [(k, pa.int64()) for k in ('width', 'height')])
BATCHES = pa.schema([(k, pa.large_string()) for k in
    ('model_key', 'model', 'batch_id', 'status', 'error', 'response_json', 'call_json')]
    + [(k, pa.int64()) for k in ('image_count', 'ok_count')])
LABEL_FIELDS = pa.schema(list(LABELS) + [
    ('objects', pa.list_(pa.struct([(k, pa.string()) for k in
        ('object_id', 'name', 'location', 'visible_features')] + [('count', pa.int64())]))),
    ('relations', pa.list_(pa.struct([(k, pa.string()) for k in
        ('subject_id', 'object_id', 'type', 'evidence')]))),
    ('view_tags', pa.list_(pa.string())),
    ('observability_issues', pa.list_(pa.struct([(k, pa.string()) for k in
        ('type', 'affected_area', 'detail')]))),
    *[(k, pa.large_string()) for k in ('text_kind', 'text_content', 'text_location', 'text_detail',
                                      'watermark_location', 'watermark_detail')],
])
COMPARISONS = pa.schema([(k, pa.string()) for k in
    ('sha256', 'model_key', 'reference_key', 'field', 'tag', 'status', 'value_json', 'reference_json')]
    + [(k, pa.bool_()) for k in ('model_selected', 'reference_selected', 'equal')])
STAGE = pa.schema([('model_key',pa.string()),('image_count',pa.int64()),
                   ('metrics_json',pa.large_string()),('outputs_json',pa.large_string())])
SUMMARY = pa.schema([('run', pa.string()), ('complete', pa.bool_()), ('image_count', pa.int64()),
    ('config_json', pa.large_string()), ('outputs_json', pa.large_string()), ('models_json', pa.large_string())])


def scope_rows(row):
    return [{'sha256': item['positive_image']['sha256'], 'image_uri': item['positive_image']['uri'],
             'bindings_json': encoded([{'concept': row['concept'], 'concept_id': row['concept_id'],
                'selection_rank': row['selection_rank'], 'image_number': item['image_number'],
                'review_sources': item['review_sources']}])} for item in row['positive_images']]


def merge_scope(acc, row):
    if acc is None:
        return row
    bindings = json.loads(acc['bindings_json']) + json.loads(row['bindings_json'])
    if len(bindings) > 100:
        raise ValueError('Image has more than 100 concept bindings')
    return {**acc, 'bindings_json': encoded(bindings)}


def prepare_batch(row, *, cfg, model_key, model):
    # 单批至多八图；逐张读取/解码，不同时展开整批原始像素。
    items, images, payload = [], [], []
    for source in row['items']:
        item = {**source, 'width': None, 'height': None, 'input_sha256': None,
                'status': 'ready', 'error': '', 'image_number': None}
        try:
            raw = ObjectRef(source['image_uri'], source['sha256']).read(max_bytes=cfg['max_image_bytes'])
            with Image.open(io.BytesIO(raw)) as picture:
                item.update(width=picture.width, height=picture.height)
                if picture.width * picture.height > cfg['max_image_pixels']:
                    raise ValueError('Image exceeds configured pixel budget')
            url, digest = encode_pixels(raw, max_edge=cfg['max_edge'], jpeg_quality=cfg['jpeg_quality'])
            item.update(input_sha256=digest, image_number=len(images) + 1)
            images.append(url)
            payload.append({'image_number': item['image_number']})
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            item.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        items.append(item)
    if sum(len(url) for url in images) > cfg['max_encoded_batch_bytes']:
        raise ValueError('Encoded batch exceeds configured byte budget')
    return {'items': items, 'images': images, 'payload': payload,
        'model_key': model_key, 'model': model,
        'batch_id': f'{model_key}:{row["group_index"]}',
        'status': 'ready' if images else 'failed'}


def check_annotation(annotation):
    identifiers = [o['object_id'] for o in annotation['objects']]
    if len(identifiers) != len(set(identifiers)):
        return 'Duplicate object_id'
    for relation in annotation['relations']:
        if relation['subject_id'] not in identifiers or relation['object_id'] not in identifiers:
            return 'Relation references an unknown object_id'
        if relation['subject_id'] == relation['object_id']:
            return 'Relation relates an object to itself'
    if len(set(annotation['view_tags'])) != len(annotation['view_tags']):
        return 'Duplicate view tag'
    if '无法确认' in annotation['view_tags'] and len(annotation['view_tags']) != 1:
        return 'Unknown view mixed with asserted views'
    return ''


def finish_batch(row, *, annotation_schema=None):
    error = row.get('error')
    call = row.get('call') or (error.get('call') if isinstance(error, dict) else None)
    response = row.get('result')
    message = encoded(error) if error else ''
    recovered = False
    # 完整 HTTP 响应只有某张图违反 schema 时，按图重新校验已有响应。
    # 不修改模型文字，不补答案，不重发请求；编号错配仍令整批失败。
    if (response is None and isinstance(error, dict) and error.get('category') == 'invalid_response'
            and annotation_schema is not None and (call or {}).get('response_ref')):
        saved = read_call(call['response_ref'])
        body = saved.get('body') or {}
        choice = (body.get('choices') or [{}])[0]
        content = (choice.get('message') or {}).get('content')
        if saved.get('status_code') == 200 and choice.get('finish_reason') == 'stop' and isinstance(content, str):
            try:
                parsed = json.loads(content.strip().removeprefix('```json').removesuffix('```').strip())
                candidate = parsed.get('result') if isinstance(parsed, dict) else None
                if isinstance(candidate, dict) and isinstance(candidate.get('annotations'), list):
                    response = candidate
                    recovered = True
                    message = ''
            except ValueError:
                pass
    if row['status'] == 'ready' and response is None and not message:
        message = 'Missing model result'
    annotations = response.get('annotations', []) if isinstance(response, dict) else []
    ids = [a.get('image_number') if isinstance(a, dict) else None for a in annotations]
    expected = {item['image_number'] for item in row['items'] if item['status'] == 'ready'}
    if not message and (any(type(i) is not int for i in ids) or len(ids) != len(set(ids)) or set(ids) != expected):
        message = 'Returned image numbers do not match the supplied images'
    lookup = {a['image_number']: a for a in annotations} if not message else {}
    labels = []
    for item in row['items']:
        annotation = lookup.get(item['image_number']) if item['status'] == 'ready' else None
        validation_error = ''
        if annotation is not None and annotation_schema is not None:
            try:
                validate_instance(annotation, annotation_schema, label='image annotation')
            except SchemaValidationError as exc:
                validation_error = 'Invalid annotation: ' + str(exc)
        item_error = item['error'] or message or validation_error or (check_annotation(annotation) if annotation else 'Missing annotation')
        valid = annotation if not item_error else None
        label = {**item, **{k: row[k] for k in ('model_key', 'model', 'batch_id')},
            'status': 'ok' if valid else 'failed', 'error': item_error,
            'annotation_json': encoded(annotation), 'call_json': encoded(call),
            'caption': valid['caption'] if valid else None,
            'composition': valid['composition'] if valid else None,
            'framing': valid['framing'] if valid else None,
            'text_presence': valid['text']['presence'] if valid else None,
            'watermark_presence': valid['watermark']['presence'] if valid else None}
        labels.append({k: label.get(k) for k in LABELS.names})
    ok = sum(label['status'] == 'ok' for label in labels)
    print(f'[image_facts] {row["batch_id"]} images={len(labels)} ok={ok} failed={len(labels)-ok}', flush=True)
    return {**{k: row[k] for k in ('model_key', 'model', 'batch_id')},
        'status': 'ok' if ok == len(labels) else 'failed', 'error': encoded(error) if recovered else message,
        'response_json': encoded(response), 'call_json': encoded(call),
        'image_count': len(labels), 'ok_count': ok, 'labels': labels}


def aggregate_calls(acc, row):
    acc = acc or {'model_key': row['model_key'], 'calls': 0, 'reused': 0, 'images': 0, 'ok': 0,
                  'usage_records': 0, 'input_tokens': 0, 'output_tokens': 0,
                  'reasoning_tokens': 0, 'elapsed_s_sum': 0.0}
    call = json.loads(row['call_json']) or {}
    usage = call.get('usage') or {}
    return {**acc, 'calls': acc['calls'] + bool(call),
        'reused': acc['reused'] + bool(call.get('reused')),
        'images': acc['images'] + row['image_count'], 'ok': acc['ok'] + row['ok_count'],
        'usage_records': acc['usage_records'] + bool(usage),
        'input_tokens': acc['input_tokens'] + (usage.get('prompt_tokens', usage.get('input_tokens', 0)) or 0),
        'output_tokens': acc['output_tokens'] + (usage.get('completion_tokens', usage.get('output_tokens', 0)) or 0),
        'reasoning_tokens': acc['reasoning_tokens'] + ((usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0),
        'elapsed_s_sum': acc['elapsed_s_sum'] + (call.get('elapsed_s') or 0)}


def collect_models(acc, row):
    models = dict((acc or {}).get('models') or {})
    if row['model_key'] in models:
        raise ValueError('Duplicate SHA/model result')
    if len(models) >= 8:
        raise ValueError('Too many model results per image')
    models[row['model_key']] = row
    return {'sha256': row['sha256'], 'models': models}


def expand_label(row):
    """将已有合格响应拆成明确列；失败为 null，实际空列表保持 []。"""
    annotation = json.loads(row['annotation_json']) if row['status'] == 'ok' else None
    fields = {k: annotation[k] if annotation else None for k in
              ('objects', 'relations', 'view_tags', 'observability_issues')}
    for group, names in [('text', ('kind', 'content', 'location', 'detail')),
                         ('watermark', ('location', 'detail'))]:
        fields.update({group+'_'+name: annotation[group][name] if annotation else None for name in names})
    expanded = {**row, **fields}
    return {k: expanded.get(k) for k in LABEL_FIELDS.names}


def tag_definitions(annotation_schema):
    """直接使用实际 prompt 的枚举；不另设一套标签或合并集合分数。"""
    props = annotation_schema['properties']
    return {
        'composition': list(props['composition']['enum']),
        'framing': list(props['framing']['enum']),
        'view_tags': list(props['view_tags']['items']['enum']),
        'relation_types': list(props['relations']['items']['properties']['type']['enum']),
        'observability_issue_types': list(props['observability_issues']['items']['properties']['type']['enum']),
        'text_presence': list(props['text']['properties']['presence']['enum']),
        'text_kind': [v for v in props['text']['properties']['kind']['enum'] if v is not None],
        'watermark_presence': list(props['watermark']['properties']['presence']['enum']),
    }


def compare(row, *, reference_key, tags):
    reference = row['models'].get(reference_key)
    for key, model in row['models'].items():
        if key == reference_key:
            continue
        ready = reference is not None and reference['status'] == model['status'] == 'ok'
        left = json.loads(model['annotation_json']) if ready else {}
        right = json.loads(reference['annotation_json']) if ready else {}
        for field, values in tags.items():
            a, b = field_value(left, field), field_value(right, field)
            for tag in values:
                selected = (tag in a if isinstance(a, list) else tag == a) if ready else None
                reference_selected = (tag in b if isinstance(b, list) else tag == b) if ready else None
                # False 只表示输出中未标出该值；不能推断图中事实不存在。
                yield {'sha256': row['sha256'], 'model_key': key, 'reference_key': reference_key,
                    'field': field, 'tag': tag, 'status': 'comparable' if ready else 'missing_or_failed',
                    'value_json': encoded(a), 'reference_json': encoded(b),
                    'model_selected': selected, 'reference_selected': reference_selected,
                    'equal': selected == reference_selected if ready else None}


def field_value(annotation, field):
    if not annotation:
        return None
    if field in {'text_presence', 'watermark_presence'}:
        return annotation[field.removesuffix('_presence')]['presence']
    if field == 'text_kind':
        return annotation['text']['kind']
    if field == 'observability_issue_types':
        return sorted({i['type'] for i in annotation['observability_issues']})
    if field == 'relation_types':
        return sorted({r['type'] for r in annotation['relations']})
    value = annotation[field]
    return sorted(set(value)) if isinstance(value, list) else value
