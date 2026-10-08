"""真实 Dataset/HTTP/journal/Lance 链路的隔离验收。"""
import json
import sqlite3
import lance
import pyarrow as pa
import pytest
from demiflow import data
from preparation.images.annotation import image_annotation_pipeline as pipeline
from preparation.images.annotation.tests.conftest import response


def cfg(lake, endpoint, **kwargs):
    values = dict(run='neutral', image_source=lake['source'], model='fixture', base_url=endpoint['url'],
                  prepare_concurrency=2, image_concurrency=2, progress_every=50)
    values.update(kwargs)
    return pipeline.config(**values)


def rows(lake, result):
    ref = result['image_results']
    return lance.dataset(str(lake['root'] / ref['uri']), version=ref['version']).to_table().to_pylist()


def journal(lake, run='neutral'):
    return lake['root'] / pipeline.MODULE_DIR / 'runs' / run / 'model_calls.sqlite'


def test_independent_neutral_pipeline_and_explicit_export(lake, endpoint):
    original = lance.dataset(str(lake['target'])).to_table().to_pylist()
    result = pipeline.run_pipeline(cfg(lake, endpoint))
    assert result['complete'] and not result['committed'] and result['required_count'] == 3
    assert len(endpoint['requests']) == 3
    assert lance.dataset(str(lake['target'])).version == lake['source']['version']
    for request in endpoint['requests']:
        payload = json.dumps(request, ensure_ascii=False)
        assert 'foreign-owner' not in payload and 'retained-release' not in payload
        assert request['model'] == 'fixture'
    assert all(r['image_uri'] and r['description_record']['status'] == 'done' for r in rows(lake, result))
    published = pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    assert published['complete'] and published['committed']
    assert len(endpoint['requests']) == 3
    current = lance.dataset(str(lake['target'])).to_table().to_pylist()
    before = {r['sha256']: r for r in original}
    for row in current:
        assert {k: v for k, v in row.items() if k not in pipeline.OWNED_COLUMNS} == {
            k: v for k, v in before[row['sha256']].items() if k not in pipeline.OWNED_COLUMNS}
        assert len(row['descriptions']) == len(row['image_scores']) == 1
    assert not (lake['root'] / pipeline.MODULE_DIR / 'datasets/concept_results__neutral.lance').exists()


def test_cross_run_public_reuse_is_scoped_and_no_service(lake, endpoint, monkeypatch):
    first = pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    current = {'uri': lake['source']['uri'], 'version': first['target_version']}
    import demiflow.services
    def service_forbidden(*args, **kwargs):
        raise AssertionError('Unexpected service')
    monkeypatch.setattr(demiflow.services, 'VLLMService', service_forbidden)
    result = pipeline.run_pipeline(cfg(lake, endpoint, run='other', image_source=current,
                                      image_shas=[lake['rows'][0]['sha256']], image_max_calls=0))
    assert result['complete'] and result['reused_count'] == 1
    assert len(rows(lake, result)) == 1 and len(endpoint['requests']) == 3
    assert not journal(lake, 'other').exists()


def test_read_only_replay_preserves_journal_and_shrinks_scope(lake, endpoint):
    pipeline.run_pipeline(cfg(lake, endpoint))
    path = journal(lake)
    before = path.read_bytes(), path.stat().st_mtime_ns
    old_calls = len(endpoint['requests']), endpoint['get_count']
    result = pipeline.run_pipeline(cfg(lake, endpoint, run='replay', model_mode='replay', replay_journal=str(path),
                                      image_shas=[lake['rows'][1]['sha256']]))
    assert result['complete'] and len(rows(lake, result)) == 1
    assert (len(endpoint['requests']), endpoint['get_count']) == old_calls
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not journal(lake, 'replay').exists()


def test_deployment_revision_changes_native_request_identity(lake, endpoint):
    one = [lake['rows'][0]['sha256']]
    a = pipeline.run_pipeline(cfg(lake, endpoint, image_shas=one, model_revision='weights-a'))
    b = pipeline.run_pipeline(cfg(lake, endpoint, image_shas=one, model_revision='weights-b'))
    assert len(endpoint['requests']) == 2 and a['image_config_id'] != b['image_config_id']
    pipeline.run_pipeline(cfg(lake, endpoint, image_shas=one, model_revision='weights-b', image_concurrency=1))
    assert len(endpoint['requests']) == 2


@pytest.mark.parametrize('status', [500, 200])
def test_model_failure_never_completes_or_exports(lake, endpoint, status):
    endpoint['status'] = status
    if status == 200:
        endpoint['payload'] = {**response(), 'description': {**response()['description'], 'objects': ['invalid-string']}}
    result = pipeline.run_pipeline(cfg(lake, endpoint, through='export', image_shas=[lake['rows'][0]['sha256']]))
    assert not result['complete'] and not result['stage_complete'] and not result['committed']
    assert result['required_count'] == result['error_count'] == 1
    assert rows(lake, result)[0]['richness'] is None
    assert json.loads(rows(lake, result)[0]['response_ref'])['request_id']
    assert lance.dataset(str(lake['target'])).version == lake['source']['version']


def test_missing_requested_sha_preserved_in_failure_denominator(lake, endpoint):
    wanted = [lake['rows'][0]['sha256'], 'f' * 64]
    result = pipeline.run_pipeline(cfg(lake, endpoint, image_shas=wanted, through='export', publish_policy='valid_rows'))
    assert result['committed'] and not result['complete'] and result['required_count'] == 2
    assert result['error_count'] == 1 and len(rows(lake, result)) == 2
    assert len(endpoint['requests']) == 1


def test_zero_budget_and_empty_input_do_not_send_http(lake, endpoint):
    limited = pipeline.run_pipeline(cfg(lake, endpoint, image_max_calls=0))
    assert not limited['complete'] and limited['required_count'] == limited['error_count'] == 3
    empty = pipeline.run_pipeline(cfg(lake, endpoint, image_shas=[]))
    assert empty['complete'] and empty['required_count'] == 0 and rows(lake, empty) == []
    assert not endpoint['requests'] and endpoint['get_count'] == 0


def test_duplicate_source_sha_is_rejected(lake, endpoint):
    source = lance.dataset(str(lake['target']))
    data.from_arrow(pa.Table.from_pylist([lake['rows'][0]], schema=source.schema)).write_lance(str(lake['target']), mode='append', schema=source.schema)
    with pytest.raises(ValueError, match='Duplicate image SHA'):
        pipeline.run_pipeline(cfg(lake, endpoint, image_source={**lake['source'], 'version': source.version + 1}))
    assert not endpoint['requests']


def test_prepare_only_and_failed_rerun_invalidates_old_success(lake, endpoint, monkeypatch):
    prepared = pipeline.run_pipeline(cfg(lake, endpoint, through='prepare'))
    assert prepared['complete'] and not prepared['committed'] and prepared['image_results'] is None
    assert not endpoint['requests']
    old = pipeline.run_pipeline(cfg(lake, endpoint))
    assert old['complete']
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt
    from demiflow.data.dataset import Dataset
    monkeypatch.setattr(Dataset, 'map_prompt_async', cancel)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    summary_path = lake['root'] / pipeline.MODULE_DIR / 'datasets/summary__neutral.lance'
    summary = lance.dataset(str(summary_path)).to_table().to_pylist()[0]
    assert not summary['complete'] and not summary['committed']
    assert json.loads(summary['details_json'])['image_results'] is None
    assert lance.dataset(str(lake['target'])).version == lake['source']['version']


def test_replay_pending_reservation_stays_pending(lake, endpoint):
    pipeline.run_pipeline(cfg(lake, endpoint, image_shas=[lake['rows'][0]['sha256']]))
    path = journal(lake)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE calls SET response_json=NULL')
    before = path.read_bytes()
    result = pipeline.run_pipeline(cfg(lake, endpoint, run='pending', image_shas=[lake['rows'][0]['sha256']],
                                      model_mode='replay', replay_journal=str(path)))
    assert not result['complete'] and result['error_count'] == 1
    assert rows(lake, result)[0]['status'] == 'pending_response'
    assert len(endpoint['requests']) == 1 and path.read_bytes() == before


def test_duplicate_output_cannot_pass_equal_row_count(lake, endpoint, monkeypatch):
    original = pipeline.finalize_image_result
    def duplicate(row, **kwargs):
        result = original(row, **kwargs)
        result['sha256'] = lake['rows'][0]['sha256']
        return result
    monkeypatch.setattr(pipeline, 'finalize_image_result', duplicate)
    with pytest.raises(ValueError, match='Duplicate image SHA'):
        pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    assert lance.dataset(str(lake['target'])).version == lake['source']['version']


def test_corrupt_pixels_remain_failed_without_model_call(lake, endpoint):
    from demiflow.objects import LocalObjectStore
    store = LocalObjectStore(lake['root'] / 'objects')
    ref = store.put(b'not a decodable image')
    source = lance.dataset(str(lake['target']))
    row = {**lake['rows'][0], 'sha256': ref.sha256, 'image_uri': ref.uri}
    data.from_arrow(pa.Table.from_pylist([row], schema=source.schema)).write_lance(
        str(lake['target']), mode='append', schema=source.schema)
    result = pipeline.run_pipeline(cfg(lake, endpoint, image_source={**lake['source'], 'version': source.version + 1},
                                      image_shas=[ref.sha256]))
    assert not result['complete'] and result['required_count'] == result['error_count'] == 1
    assert rows(lake, result)[0]['status'] == 'pixel_error' and not endpoint['requests']


def test_unused_prompt_removal_keeps_exact_request_replay(lake, endpoint, monkeypatch):
    import yaml
    from demiflow.operator_llm.parser import parse_prompt_pack
    original = pipeline.annotation_prompt_pack
    def with_unrelated(config):
        pack, text = original(config)
        spec = yaml.safe_load(text)
        spec['prompts']['unrelated_old_node'] = {**spec['prompts']['describe_image'], 'version': 'unused-old-node'}
        text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
        return parse_prompt_pack(text), text
    monkeypatch.setattr(pipeline, 'annotation_prompt_pack', with_unrelated)
    first = pipeline.run_pipeline(cfg(lake, endpoint, image_shas=[lake['rows'][0]['sha256']]))
    monkeypatch.setattr(pipeline, 'annotation_prompt_pack', original)
    second = pipeline.run_pipeline(cfg(lake, endpoint, image_shas=[lake['rows'][0]['sha256']]))
    assert len(endpoint['requests']) == 1
    assert first['image_config_id'] == second['image_config_id'] and second['reused_count'] == 1


def test_export_conflict_stops_and_explicit_rerun_reuses_model_results(lake, endpoint, monkeypatch):
    from demiflow.data.dataset import Dataset
    from demiflow.errors import LanceWriteConflict
    original = Dataset.write_lance
    state = {'injected': False}
    def conflicting(self, uri, **kwargs):
        if str(uri) == str(lake['target']) and kwargs.get('mode') == 'merge' and not state['injected']:
            state['injected'] = True
            raise LanceWriteConflict('injected version conflict')
        return original(self, uri, **kwargs)
    monkeypatch.setattr(Dataset, 'write_lance', conflicting)
    with pytest.raises(LanceWriteConflict, match='injected'):
        pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    assert state['injected'] and len(endpoint['requests']) == 3
    assert lance.dataset(str(lake['target'])).version == lake['source']['version']
    summary_path = lake['root'] / pipeline.MODULE_DIR / 'datasets/summary__neutral.lance'
    summary = lance.dataset(str(summary_path)).to_table().to_pylist()[0]
    assert not summary['complete'] and not summary['committed']
    result = pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    assert result['complete'] and len(endpoint['requests']) == 3
    current = lance.dataset(str(lake['target'])).to_table().to_pylist()
    assert {r['sha256']: r['future_column'] for r in current} == {
        r['sha256']: r['future_column'] for r in lake['rows']}
    again = pipeline.run_pipeline(cfg(lake, endpoint, through='export'))
    assert again['target_version'] == result['target_version'] and len(endpoint['requests']) == 3


def test_replay_miss_is_pending_and_distinct_from_budget(lake, endpoint):
    pipeline.run_pipeline(cfg(lake, endpoint, image_shas=[lake['rows'][0]['sha256']]))
    path = journal(lake)
    before = path.read_bytes()
    result = pipeline.run_pipeline(cfg(lake, endpoint, run='missing_replay',
                                      image_shas=[lake['rows'][1]['sha256']],
                                      model_mode='replay', replay_journal=str(path)))
    row = rows(lake, result)[0]
    assert not result['complete'] and result['error_count'] == 1
    assert row['status'] == 'pending_response' and row['richness'] is None
    assert json.loads(row['error'])['type'] == 'PromptReplayMissError'
    assert len(endpoint['requests']) == 1 and path.read_bytes() == before
