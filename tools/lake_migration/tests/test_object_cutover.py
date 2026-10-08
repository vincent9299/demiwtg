import hashlib
import sqlite3

import lance
import pyarrow as pa
import pytest

from demiflow.objects import ObjectRef
from tools.lake_migration.object_cutover import export_snapshot
from tools.lake_migration.blob_snapshot_rebuild import rebuild
from tools.lake_migration.commit_object_cutover import commit_blob_table, retire_blob_backup
from tools.lake_migration.reference_cutover import stage_references
from tools.lake_migration.commit_object_cutover import commit_reference_table


def test_external_history_survives_deletes_and_plain_uri_cutover(tmp_path):
    bodies = [b'first historical image', b'second current image']
    keys = [hashlib.sha256(body).hexdigest() for body in bodies]
    schema = pa.schema([('sha256', pa.string()), lance.blob_field('data')])
    old = lance.write_dataset(pa.Table.from_arrays([
        pa.array(keys), lance.blob_array(bodies)], schema=schema), str(tmp_path / 'raw.lance'))
    old.delete(f"sha256 = '{keys[0]}'")
    audit = tmp_path / 'audit'
    export_snapshot(tmp_path, 'raw.lance', 1, audit=audit, workers=2)
    # Re-running a completed export must use its durable completion record.
    assert export_snapshot(tmp_path, 'raw.lance', 1, audit=audit)['reused']
    receipt = rebuild(tmp_path, 'raw.lance', 'stage.lance', audit=audit)
    assert receipt['verified_versions'] == [1, 2]
    assert receipt['head']['version'] == 3
    assert rebuild(tmp_path, 'raw.lance', 'stage.lance', audit=audit) == receipt
    for version, expected in ((1, bodies), (2, bodies[1:])):
        saved = lance.dataset(str(tmp_path / 'stage.lance'), version=version)
        assert saved.schema.equals(schema, check_metadata=True)
        assert [blob.read() for blob in saved.take_blobs('data', indices=list(range(len(expected))))] == expected
    head = lance.dataset(str(tmp_path / 'stage.lance'))
    assert 'data' not in head.schema.names
    assert ObjectRef(**{'uri': head.to_table()['image_uri'][0].as_py(), 'sha256': keys[1]}).read() == bodies[1]
    assert not list((tmp_path / 'stage.lance').rglob('*.blob'))
    # Staging cannot mutate the source, including deleted historical records.
    assert lance.dataset(str(tmp_path / 'raw.lance')).version == 2
    committed = commit_blob_table(tmp_path, receipt, audit=audit)
    assert lance.dataset(str(tmp_path / 'raw.lance')).version == 3
    assert commit_blob_table(tmp_path, receipt, audit=audit) == committed
    retired = retire_blob_backup(committed, audit=audit)
    assert retired['removed_lance_bytes'] > 0
    assert not __import__('pathlib').Path(committed['backup']).exists()
    assert lance.dataset(str(tmp_path / 'raw.lance'), version=1).take_blobs('data', indices=[0])[0].read() == bodies[0]


def test_nested_reference_cutover_preserves_judgment_and_history(tmp_path):
    raw = b'verified image'
    key = hashlib.sha256(raw).hexdigest()
    schema = pa.schema([('sha256', pa.string()), lance.blob_field('data')])
    lance.write_dataset(pa.Table.from_arrays([pa.array([key]), lance.blob_array([raw])], schema=schema),
                        str(tmp_path / 'raw.lance'))
    audit = tmp_path / 'audit'
    export_snapshot(tmp_path, 'raw.lance', 1, audit=audit)
    old = {'blob_ref': {'relative_uri':'raw.lance', 'version':1, 'sha256':key, 'column':'data'},
           'decision':'keep', 'score':8}
    lance.write_dataset(pa.Table.from_pylist([old]), str(tmp_path / 'results.lance'))
    receipt = stage_references(tmp_path, 'results.lance', 1, 'references.lance', audit=audit)
    result = commit_reference_table(tmp_path, receipt, audit=audit)
    assert result['head_version'] == 2
    saved = lance.dataset(str(tmp_path / 'results.lance')).to_table().to_pylist()[0]
    assert saved['decision'] == 'keep' and saved['score'] == 8
    assert 'blob_ref' not in saved and ObjectRef(**saved['object_ref']).read() == raw
    assert lance.dataset(str(tmp_path / 'results.lance'), version=1).to_table().to_pylist()[0] == old


def test_corrupt_source_never_marks_export_complete(tmp_path):
    key = hashlib.sha256(b'expected').hexdigest()
    schema = pa.schema([('sha256', pa.string()), lance.blob_field('data')])
    lance.write_dataset(pa.Table.from_arrays([pa.array([key]), lance.blob_array([b'wrong'])], schema=schema),
                        str(tmp_path / 'raw.lance'))
    audit = tmp_path / 'audit'
    with pytest.raises(ValueError, match='failures=1'):
        export_snapshot(tmp_path, 'raw.lance', 1, audit=audit)
    with sqlite3.connect(audit / 'objects.sqlite') as database:
        assert database.execute('SELECT count(*) FROM objects').fetchone()[0] == 0
        assert database.execute('SELECT complete FROM progress').fetchone()[0] == 0
    assert lance.dataset(str(tmp_path / 'raw.lance')).take_blobs('data', indices=[0])[0].read() == b'wrong'


def test_missing_historical_image_gets_no_invented_uri(tmp_path):
    from tools.lake_migration.object_cutover import journal
    audit = tmp_path / 'audit'
    audit.mkdir()
    key = 'a' * 64
    with journal(audit / 'objects.sqlite') as database:
        database.execute('INSERT INTO absent_objects VALUES(?,?,?,?)', (key, 'raw.lance', 1, 'verified null Blob'))
    old = {'sha256':key, 'concepts':['preserved']}
    lance.write_dataset(pa.Table.from_pylist([old]), str(tmp_path / 'public.lance'))
    receipt = stage_references(tmp_path, 'public.lance', 1, 'next.lance', audit=audit, public_images=True)
    row = lance.dataset(receipt['target']).to_table().to_pylist()[0]
    assert row['image_uri'] is None
    assert row['concepts'] == old['concepts']


def test_preparation_request_images_restore_after_old_lance_files_are_retired(tmp_path):
    import json
    from tools.lake_migration.request_image_cutover import stage_requests
    from preparation.articles.operators.results import from_stage_row
    payload = {'pixel_images':['data:image/png;base64,YWJj'], 'judgment':{'score':9}}
    row = {'stage':'image_requests','row_id':'unchanged','payload':json.dumps(payload)}
    lance.write_dataset(pa.Table.from_pylist([row]), str(tmp_path / 'requests.lance'))
    audit = tmp_path / 'audit'
    audit.mkdir()
    receipt = stage_requests(tmp_path, 'requests.lance', audit=audit)
    committed = commit_blob_table(tmp_path, receipt, audit=audit)
    retire_blob_backup(committed, audit=audit)
    saved = lance.dataset(str(tmp_path / 'requests.lance'), version=1).to_table().to_pylist()[0]
    assert saved['row_id'] == 'unchanged' and 'YWJj' not in saved['payload']
    assert from_stage_row(saved) == payload


def test_column_cutover_preserves_nested_annotations_and_original_file_ids(tmp_path):
    from tools.lake_migration.column_reference_cutover import stage_columns
    key = hashlib.sha256(b'actual image').hexdigest()
    raw_schema = pa.schema([('sha256',pa.string()),lance.blob_field('data')])
    lance.write_dataset(pa.Table.from_arrays([pa.array([key]),lance.blob_array([b'actual image'])],
                        schema=raw_schema),str(tmp_path/'raw.lance'))
    export_snapshot(tmp_path,'raw.lance',1,audit=tmp_path/'audit')
    original = {'sha256':key,'blob_ref':{'relative_uri':'raw.lance','version':1,'sha256':key,'column':'data'},
                'annotation':{'regions':[{'limitations':['','first','second']}]}}
    old = lance.write_dataset(pa.Table.from_pylist([original]),str(tmp_path/'results.lance'))
    old_files = [f['path'] for f in old.get_fragments()[0].metadata.to_json()['files']]
    receipt = stage_columns(tmp_path,'results.lance',1,'new.lance',audit=tmp_path/'audit')
    committed = commit_blob_table(tmp_path,receipt,audit=tmp_path/'audit')
    now = lance.dataset(str(tmp_path/'results.lance'))
    assert now.schema.names == ['sha256','object_ref','annotation']
    assert now.to_table().to_pylist()[0]['annotation'] == original['annotation']
    new_files = [f['path'] for f in now.get_fragments()[0].metadata.to_json()['files']]
    assert set(old_files) <= set(new_files)
    assert lance.dataset(str(tmp_path/'results.lance'),version=1).to_table().to_pylist()[0] == original
    retire_blob_backup(committed,audit=tmp_path/'audit')
    assert ObjectRef(**now.to_table().to_pylist()[0]['object_ref']).read() == b'actual image'


def test_column_cutover_converts_bare_json_image_ref_but_preserves_dataset_ref(tmp_path):
    import json
    from tools.lake_migration.column_reference_cutover import stage_columns
    from tools.lake_migration.image_objects import contains_legacy_image_ref
    body = b'generated pixels'
    key = hashlib.sha256(body).hexdigest()
    schema = pa.schema([('sha256', pa.string()), lance.blob_field('data')])
    lance.write_dataset(pa.Table.from_arrays([pa.array([key]), lance.blob_array([body])], schema=schema),
                        str(tmp_path / 'raw.lance'))
    export_snapshot(tmp_path, 'raw.lance', 1, audit=tmp_path / 'audit')
    image_json = json.dumps({'relative_uri':'raw.lance', 'version':1, 'sha256':key, 'column':'data'})
    table_json = json.dumps({'relative_uri':'questions.lance', 'lance_version':3, 'row_count':8})
    assert contains_legacy_image_ref(image_json)
    assert contains_legacy_image_ref(json.dumps({'image_json': image_json}))
    assert not contains_legacy_image_ref(table_json)
    row = {'image_json':image_json, 'source_json':table_json, 'score':9}
    lance.write_dataset(pa.Table.from_pylist([row]), str(tmp_path / 'answers.lance'))
    receipt = stage_columns(tmp_path, 'answers.lance', 1, 'stage.lance', audit=tmp_path / 'audit')
    assert receipt['changed_columns'] == ['image_json']
    saved = lance.dataset(receipt['target']).to_table().to_pylist()[0]
    assert ObjectRef(**json.loads(saved['image_json'])).read() == body
    assert saved['source_json'] == table_json and saved['score'] == 9


def test_article_illustration_cutover_preserves_text_and_selection_evidence(tmp_path):
    import json
    from tools.lake_migration.column_reference_cutover import stage_columns
    body = b'original illustration'
    key = hashlib.sha256(body).hexdigest()
    schema = pa.schema([('sha256', pa.string()), lance.blob_field('data')])
    lance.write_dataset(pa.Table.from_arrays([pa.array([key]), lance.blob_array([body])], schema=schema),
                        str(tmp_path / 'raw.lance'))
    export_snapshot(tmp_path, 'raw.lance', 1, audit=tmp_path / 'audit')
    info = {'sha256':key, 'path':'/retired/image.png', 'status':'verified_bytes'}
    evidence = {'bytes':info, 'record':{'byte_details':info, 'caption':'unchanged'},
                'selection_review':{'decision':'keep'}, 'provenance':{'path':'original-source.jsonl'}}
    row = {'content':'unchanged article', 'illustrations':[{'sha256':key,'evidence_json':json.dumps(evidence)}]}
    lance.write_dataset(pa.Table.from_pylist([row]), str(tmp_path / 'articles.lance'))
    receipt = stage_columns(tmp_path, 'articles.lance', 1, 'stage.lance', audit=tmp_path / 'audit',
                            article_illustrations=True)
    saved = lance.dataset(receipt['target']).to_table().to_pylist()[0]
    image = json.loads(saved['illustrations'][0]['evidence_json'])
    assert ObjectRef(image['bytes']['image_uri'], key).read() == body
    assert image['selection_review'] == evidence['selection_review']
    assert image['provenance'] == evidence['provenance']
    assert image['record']['caption'] == 'unchanged' and saved['content'] == row['content']
