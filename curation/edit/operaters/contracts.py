"""Explicit lake columns for Edit questions, reviews and accepted training pairs."""
import json
import pyarrow as pa

BLOB = pa.struct([('relative_uri', pa.string()), ('version', pa.int64()),
                  ('sha256', pa.string()), ('column', pa.string())])
CONTENT = pa.struct([('type', pa.string()), ('text', pa.large_string()), ('blob_ref', BLOB)])
QUESTIONS = pa.schema([
    ('task_id', pa.string()), ('concept', pa.string()), ('status', pa.string()),
    ('learning_directions', pa.list_(pa.string())), ('learning_objective', pa.large_string()),
    ('edit_type', pa.string()), ('existing_role', pa.string()), ('role_reason', pa.large_string()),
    ('supervision_signal', pa.large_string()), ('instruction', pa.large_string()),
    ('synthesis_instruction', pa.large_string()), ('preserve', pa.list_(pa.string())),
    ('criteria', pa.list_(pa.string())), ('existing', BLOB), ('source', BLOB), ('target', BLOB),
    ('input_content', pa.list_(CONTENT)), ('design_seconds', pa.float64()),
    ('synthesis_seconds', pa.float64()), ('model_load_seconds', pa.float64()),
    ('review_seconds', pa.float64()), ('reason', pa.large_string()),
    ('details', pa.large_string()),
])
SAMPLES = pa.schema([
    ('sample_id', pa.string()), ('concept', pa.string()), ('task_type', pa.string()),
    ('instruction', pa.large_string()), ('input_content', pa.list_(CONTENT)),
    ('target', BLOB), ('audit_uri', pa.string()), ('audit_version', pa.int64()),
])


def question_row(row):
    """Keep useful question columns queryable; details retain the complete evidence."""
    out = {name: row.get(name) for name in QUESTIONS.names}
    for name in ('existing', 'source', 'target'):
        out[name] = (row.get(name) or {}).get('blob_ref')
    out['details'] = json.dumps({k: v for k, v in row.items() if not k.startswith('prompt_')}, ensure_ascii=False)
    return out


def restore_question(row):
    return json.loads(row['details'])


def training_sample(row, *, audit_uri):
    if row['status'] != 'accepted':
        raise ValueError('Only reviewed pairs can become training samples')
    return {'sample_id': row['task_id'], 'concept': row['concept'], 'task_type': 'edit',
            'instruction': row['instruction'], 'input_content': row['input_content'],
            'target': row['target']['blob_ref'], 'audit_uri': str(audit_uri), 'audit_version': 1}
