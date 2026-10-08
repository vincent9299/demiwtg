"""固定 Review 来源的逐行投影；题面变化相对最初正式题计算。"""
import json
import pyarrow as pa

QUESTION_FIELDS = ['task_id', 'concept', 'instruction', 'taxonomy', 'test_points',
    'authoring_variant', 'authoring_images_json', 'source_task_id',
    'question_revision', 'requirements_json', 'review_status']


def tag_review(row, *, priority, source):
    return {**row, 'review_priority': priority,
        'review_source_json': json.dumps(source, ensure_ascii=False, sort_keys=True)}


def latest_review(previous, row):
    if previous is None or row['review_priority'] > previous['review_priority']:
        return row
    if row['review_priority'] == previous['review_priority']:
        raise ValueError('Duplicate concept within one Review source')
    return previous


def baseline_question(row):
    return {'concept': row['concept'], 'original_task_id': row['task_id'],
        'baseline_instruction': row['instruction']}


def revision_question(row):
    if not row.get('original_task_id') or not row.get('selection_rank'):
        raise ValueError('Review question is outside the original formal scope')
    if not row.get('question_revision') or not row.get('requirements_json'):
        raise ValueError('Ready Review must carry its frozen revision and requirements')
    return {**row, 'requires_new_answer': row['instruction'] != row['baseline_instruction']}


def question_schema(review_schema):
    return pa.schema([*[review_schema.field(name) for name in QUESTION_FIELDS],
        ('review_priority', pa.int64()), ('review_source_json', pa.string()),
        ('original_task_id', pa.string()), ('baseline_instruction', pa.string()),
        ('selection_rank', pa.int64()), ('requires_new_answer', pa.bool_())])
