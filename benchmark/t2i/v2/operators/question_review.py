"""题目审校的单行材料绑定与交付；文档正文由标准算子按需读取。"""
import hashlib
import json
import pyarrow as pa
from demiflow.schema import validate_instance
from .concept_context import document_resources
from .images import image_data_url, positive_image_data_url

REVIEW_FIELDS = pa.schema([
    *[(k, pa.large_string()) for k in ('source_task_id', 'question_revision', 'requirements_json',
        'review_status', 'review_reason', 'review_result_json', 'review_call_json', 'review_context_json',
        'review_source_json', 'original_instruction', 'original_test_points_json', 'review_prompt_version')],
    ('requires_new_answer', pa.bool_()),
])
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 8
MAX_IMAGE_INPUT_BYTES = 12 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def prepare(row, *, source, prompt_version):
    """绑定已固定的本题、文档引用与实际图片；不预读文档正文或混入作答。"""
    out = {**row, 'source_task_id': row['task_id'], 'original_instruction': row['instruction'],
           'original_test_points_json': encoded(row['test_points']), 'review_source_json': encoded(source),
           'review_status': 'ready_to_review', 'review_reason': '', 'review_result_json': None,
           'review_call_json': None, 'review_context_json': None, 'question_revision': None,
           'requirements_json': None, 'requires_new_answer': None, 'review_prompt_version': prompt_version,
           'review_payload': {}, 'document_resources': {}, 'prompt_images': []}
    if len(encoded(row).encode()) > MAX_ROW_BYTES:
        out.update(review_status='invalid_materials', review_reason='单题记录超过2MiB，未截断材料')
        return out
    try:
        record = row.get('concept_record')
        examples = json.loads(row.get('authoring_images_json') or '[]')
        if len(examples) > MAX_IMAGES:
            raise ValueError('作者正例图超过8张，未截断图片')
        out['review_payload'] = {
            'concept': row['concept'], 'instruction': row['instruction'], 'test_points': row['test_points'],
            'concept_definition': ({'name': record['canonical_name'], 'definition': record['definition']}
                                   if record else None),
            'document_evidence': record['evidence'] if record else [],
            'evidence': json.loads(row.get('evidence_json') or '[]'),
            'positive_examples': [{'number': item['number'], 'image_number': i}
                                  for i, item in enumerate(examples, 1)],
        }
        out['document_resources'] = document_resources(record) if record else {}
        if len(out['document_resources']) > 64:
            raise ValueError('本题固定文档超过64份，未截断材料')
        image_bytes = 0
        encode_image = image_data_url if row.get('authoring_variant', 'standard') == 'standard' else positive_image_data_url
        for example in examples:
            image = encode_image(example['object_ref'])
            image_bytes += len(image)
            if image_bytes > MAX_IMAGE_INPUT_BYTES:
                raise ValueError('实际图片输入编码超过12MiB，未截断图片')
            out['prompt_images'].append(image)
    except (OSError, ValueError, TypeError, KeyError) as error:
        out.update(review_status='invalid_materials', review_reason=str(error), prompt_images=[])
    return out


def finish(row, *, result_schema):
    """接收审核结论；身份、版本与是否改题由代码派生，不验证引用或替模型重判。"""
    error = row.get('question_review_error') or {}
    call = row.get('question_review_call') or error.get('call') or {}
    out = {**row, 'review_call_json': encoded(call),
           'review_context_json': encoded(call.get('environment', {}).get('observations', [])),
           'review_result_json': encoded(row.get('question_review_result'))}
    if out['review_status'] != 'ready_to_review':
        return out
    if error:
        return {**out, 'review_status': 'pending' if error.get('type') == 'PromptResponsePending' else 'failed',
                'review_reason': error.get('detail') or error.get('type') or str(error)}
    result = row.get('question_review_result')
    try:
        validate_instance(result, result_schema)
        if result['decision'] == 'ready' and (result['question'] is None or not result['requirements'] or result['issues']):
            raise ValueError('ready requires a complete question/checklist and no unresolved issues')
        if result['decision'] == 'hold' and (result['question'] is not None or result['requirements'] or not result['issues']):
            raise ValueError('hold requires issues and no finalized question/checklist')
        if any((item['dimension'] == 'task_correctness' and type(item['is_core']) is not bool)
               or (item['dimension'] == 'other_instruction_following' and item['is_core'] is not None)
               for item in result['requirements']):
            raise ValueError('core flag must match the dimension field type')
    except (ValueError, TypeError) as error:
        return {**out, 'review_status': 'invalid_response', 'review_reason': str(error)}
    out.update(review_status=result['decision'], review_reason=result['reason'])
    if result['decision'] == 'hold':
        return out
    question = result['question']
    requirements = [{'id': f'r{i:03d}', **item} for i, item in enumerate(result['requirements'], 1)]
    revision = hashlib.sha256(encoded({'question': question, 'requirements': requirements,
        'prompt_version': row['review_prompt_version']}).encode()).hexdigest()
    changed = question['instruction'] != row['original_instruction']
    task_id = ('t2i_' + hashlib.sha256(encoded([row['source_task_id'], question['instruction']]).encode()).hexdigest()
               if changed else row['source_task_id'])
    out.update(**question, task_id=task_id, status='reviewed', question_revision=revision,
               requirements_json=encoded(requirements), requires_new_answer=changed)
    return out
