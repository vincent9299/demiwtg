"""T2I lake contracts: audit rows and compact, pixel-bound training samples."""
import json
import pyarrow as pa

from demiflow.execution.artifacts import digest
from demiflow.lance.blobs import BlobRef
from preparation.operaters.inputs import pixels
from preparation.operaters.runfiles import run_records
from project import resolve_root
from curation.t2i.operaters.operators import fail
from curation.t2i.operaters.review import sample_identity

BLOB = pa.struct([pa.field('relative_uri', pa.string(), False),
                  pa.field('version', pa.int64(), False),
                  pa.field('sha256', pa.string(), False),
                  pa.field('column', pa.string(), False)])
RECORD = pa.struct([pa.field('relative_uri', pa.string(), False),
                    pa.field('version', pa.int64(), False), pa.field('key', pa.string(), False)])
CONTENT = pa.struct([pa.field('type', pa.string(), False), pa.field('text', pa.large_string()),
                     pa.field('mime', pa.string()), pa.field('blob_ref', BLOB)])
TRAINING_SAMPLES = pa.schema([
    pa.field('sample_id', pa.string(), False), pa.field('concept', pa.string(), False),
    pa.field('task_type', pa.string(), False), pa.field('instruction', pa.large_string(), False),
    pa.field('input_content', pa.list_(CONTENT), False),
    pa.field('target', pa.struct([pa.field('mime', pa.string(), False),
                                 pa.field('blob_ref', BLOB, False)]), False),
    pa.field('audit_ref', RECORD, False),
])
AUDIT_ROWS = pa.schema([pa.field('concept', pa.string()), pa.field('task_id', pa.string()),
                        pa.field('status', pa.string(), False), pa.field('payload', pa.large_string(), False)])


def encode_audit(row):
    """业务行保存为带索引字段的审计记录，阶段及版本由所在表记录。"""
    return {**{key: row.get(key) for key in ('concept', 'task_id', 'status')},
            'payload': json.dumps(row, ensure_ascii=False, sort_keys=True)}


def decode_audit(row):
    return json.loads(row['payload'])


def pin_asset(asset, raw_ref):
    ref = asset.get('blob_ref') or BlobRef(raw_ref['relative_uri'], raw_ref['lance_version'], asset['sha256']).to_dict()
    if ref['sha256'] != asset['sha256']:
        raise ValueError('Conflicting image and Blob identity')
    fixed = {**asset, 'blob_ref': ref}
    pixels(fixed)
    return fixed


class PinMaterials:
    def __init__(self, raw_ref):
        self.raw_ref = raw_ref

    def __call__(self, row):
        try:
            materials = [{**m, 'asset': pin_asset(m['asset'], self.raw_ref)} if m['kind'] == 'image'
                         else m for m in row['materials']]
            targets = [pin_asset(a, self.raw_ref) for a in row.get('target_pool', [])]
            return {**row, 'materials': materials, 'target_pool': targets}
        except (ValueError, OSError, KeyError) as error:
            return fail(row, 'invalid_material_pixels', str(error))


def portable_input(row):
    """Preserve the exact assembled text/order; replace only data URLs by fixed Blobs."""
    assets = iter(m['asset'] for m in row['answer_materials'] if m['kind'] == 'image')
    result = []
    for part in row['answer_input']['messages'][0]['content']:
        if part['type'] == 'text':
            result.append({'type': 'text', 'text': part['text'], 'mime': None, 'blob_ref': None})
        else:
            asset = next(assets)
            _, mime, _ = pixels(asset)
            result.append({'type': 'image_blob', 'text': None, 'mime': mime, 'blob_ref': asset['blob_ref']})
    return result


def sample_key(row):
    """Exact training-row identity, not question identity; targets may differ."""
    return digest([row['concept'], row['draft']['instruction'],
        [(m['kind'], m['text'] if m['kind'] == 'text' else m['asset']['sha256'])
         for m in row['answer_materials']], row['design_targets'][0]['sha256']])


class ExportSample:
    def __init__(self, run):
        self.run = run

    def __call__(self, row):
        row = {**row, 'export_ready': False}
        if row['status'] != 'accepted_task':
            return row
        try:
            if row['sample_sha256'] != sample_identity(row):
                raise ValueError('Reviewed sample changed before export')
            target = row['design_targets'][0]
            _, mime, _ = pixels(target)
            content = portable_input(row)
            audit = {k: v for k, v in row.items() if k != 'answer_input'}
            audit.update(status='accepted_sample', export_ready=True, frozen_input_content=content)
            audit_ref = run_records(self.run).put('sample_audit/' + row['task_id'], audit)
            sample = {'sample_id': 't2i_' + sample_key(row), 'concept': row['concept'],
                      'task_type': 't2i', 'instruction': row['draft']['instruction'],
                      'input_content': content, 'target': {'mime': mime, 'blob_ref': target['blob_ref']},
                      'audit_ref': audit_ref.to_dict()}
            return {**audit, 'training_sample': sample}
        except (ValueError, OSError, KeyError, StopIteration) as error:
            return fail(row, 'invalid_export', str(error))


def sample_messages(sample, root=None):
    """Training consumer adapter: resolve image Blobs only when loading a sample."""
    import base64
    content = []
    for part in sample['input_content']:
        if part['type'] == 'text':
            content.append({'type': 'text', 'text': part['text']})
        elif part['type'] == 'image_blob':
            raw = BlobRef(**part['blob_ref']).read(resolve_root(root))
            content.append({'type': 'image_url', 'image_url': {
                'url': 'data:' + part['mime'] + ';base64,' + base64.b64encode(raw).decode()}})
        else:
            raise ValueError('Unknown sample content type')
    return [{'role': 'user', 'content': content}]
