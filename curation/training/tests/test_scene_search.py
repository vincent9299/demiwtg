"""Real search/download protocol and edit branch; all model responses are fixtures."""
import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from curation.training.tests.test_authoring import inputs, autonomous_inputs, ingest, write_rows, request_record
from curation.preparation.materials import SplitGuard
from curation.training.tests.pipeline_runtime import load_pipeline
from curation.benchmark.scene_search import SceneSearch, SearchExternalScenes
from curation.preparation.records import read, saved_stage, run_state


def edit_design():
    return {'status': 'ok', 'candidates': [{'task_type': 'edit', 'evidence': [1],
        'knowledge_gap': '判断节点形状', 'knowledge_application': '判断角色决定节点形状',
        'edit_intent': '修正一次判断节点', 'scene_requirements': '有清晰判断节点和连线的图',
        'search_queries': ['diagram decision node'], 'draft': None}]}


def source_review():
    return {'status': 'ok', 'image': 1, 'checks': {k: True for k in
        ('identity_supported', 'existing_image', 'anchor_visible', 'knowledge_change_possible')},
        'anchor': '中间节点', 'reason': 'fixture only', 'selection_evidence': '节点、分支与编辑锚点可见'}


def raw_manifest(records):
    import pyarrow as pa
    from demiflow.lance.registry import write_registered_table
    from project import resolve_root
    from curation.preparation.records import digest
    from collect.material_schema import SOURCE
    from collect.materials import source_record
    schema=pa.schema([('sha256',pa.string()),('concepts',pa.list_(pa.string())),
                     ('sources',pa.list_(SOURCE)),('availability',pa.string())])
    rows=[{'sha256':r['sha256'],'concepts':r.get('instances',r.get('concepts',[])),
           'sources':[source_record(r,system='fixture',source_row=i)],'availability':'available'} for i,r in enumerate(records)]
    ref,_,_=write_registered_table(resolve_root(),'raw/fixtures/'+digest(records)+'.lance',
        schema_name='fixture',schema_version='v2',schema=schema,
        rows_factory=lambda:iter(rows),fingerprint=digest(records))
    return ref.to_dict()



def setup_edit(inputs, name='edit', with_local=True):
    runs, cfg, *_ = autonomous_inputs(inputs, 'benchmark')
    asset = inputs[4][2]
    manifest = [{'sha256': asset['sha256'], 'path': asset['path'],
            'caption': 'Diagram decision node', 'instances': ['与知识不同的概念'],
            'content_url': 'test://fixture', 'identity': True}]
    cfg['scene_search']['image_ref'] = raw_manifest(manifest) if with_local else None
    run = inputs[0] / name
    return runs, cfg, asset, manifest, run


def test_cross_concept_search_is_late_and_actual_pixels_precede_finalize(inputs):
    runs, cfg, asset, manifest, run = setup_edit(inputs)
    graph = load_pipeline('benchmark'); graph(run, runs, cfg)
    assert not (run / 'observed_assets').exists()
    ingest(run, 'design_candidates', edit_design()); graph(run, runs, cfg)
    row = saved_stage(run, 'edit_source')[0]
    assert row['status'] == 'pending_select_edit_source'
    assert row['source_candidates'][0]['source_concepts'] == ['与知识不同的概念']
    assert not (run / 'requests/construct').exists()
    request = request_record(run, 'select_edit_source')
    assert [i['role'] for i in request['image_roles']] == ['reference', 'edit_source_candidate']
    ingest(run, 'select_edit_source', source_review()); graph(run, runs, cfg)
    assert saved_stage(run, 'construct')[0]['status'] == 'pending_construct'
    chosen = saved_stage(run, 'construct')[0]['edit_source']
    assert chosen['origin'] == 'not_verified'
    assert 'non_generated' not in chosen['review']  # No fabricated origin certification.
    assert not (run / 'scene_http').exists()  # accepted local source skips external network
    draft = copy.deepcopy(inputs[-1]); draft.update(edit_type='adjust', anchor='中间节点', preserve=['保留其他节点和连线'])
    ingest(run, 'construct', draft); graph(run, runs, cfg)
    assert saved_stage(run, 'validate')[0]['status'] == 'valid_task'
    previous = run_state(run)
    # The source is a fixed dataset version; appending a new version cannot change this run.
    assert graph(run, runs, cfg)['new_stages'] == []
    assert run_state(run)['stages'] == previous['stages']
    Path(asset['path']).write_bytes(b'changed')
    assert graph(run, runs, cfg)['new_stages'] == []  # historical path is not authoritative


@pytest.fixture
def image_server(inputs):
    calls = []
    payload = Path(inputs[4][1]['path']).read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            base = f'http://127.0.0.1:{self.server.server_port}'
            if self.path.startswith('/api'):
                body = {'query': {'pages': {'1': {'index': 1, 'title': 'Scene', 'imageinfo': [{
                    'url': base+'/image', 'descriptionurl': base+'/page',
                    'extmetadata': {'Artist': {'value': 'Fixture photographer'}, 'LicenseShortName': {'value': 'CC0'}}}]}}}}
                data = json.dumps(body).encode()
            elif self.path.startswith('/search'):
                data = json.dumps({'results': [{'img_src': base+'/image', 'url': base+'/page', 'engine': 'fixture'}]}).encode()
            elif self.path == '/image': data = payload
            else:
                self.send_response(503); self.end_headers(); return
            self.send_response(200); self.end_headers(); self.wfile.write(data)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}', calls
    server.shutdown(); server.server_close()


@pytest.mark.parametrize('provider', ['commons', 'searxng'])
def test_rejected_local_image_triggers_bounded_external_search_then_same_review(inputs, image_server, provider):
    runs, cfg, _, _, run = setup_edit(inputs, provider)
    base, calls = image_server
    cfg['scene_search'].update(external_providers=[provider], commons_url=base+'/api', searxng_url=base, trust_env=False)
    graph = load_pipeline('benchmark'); graph(run, runs, cfg)
    ingest(run, 'design_candidates', edit_design()); graph(run, runs, cfg)
    assert not calls
    ingest(run, 'select_edit_source', {'status': 'insufficient', 'reason': 'Initial state unsuitable'})
    graph(run, runs, cfg)
    row = saved_stage(run, 'edit_external_source')[0]
    assert row['status'] == 'pending_select_edit_source_external'
    assert len(row['local_source_candidates']) == len(row['source_candidates']) == 1
    assert row['source_candidates'][0]['source']['landing_url'] == base+'/page'
    assert len(calls) == 2
    assert graph(run, runs, cfg)['new_stages'] == [] and len(calls) == 2
    ingest(run, 'select_edit_source_external', source_review()); graph(run, runs, cfg)
    assert saved_stage(run, 'construct')[0]['status'] == 'pending_construct'
    request = request_record(run, 'construct')
    assert request['image_roles'][-1]['role'] == 'edit_source'
    assert len(calls) == 2


def test_external_failure_remains_gap_and_is_not_retried(inputs, image_server):
    runs, cfg, _, _, run = setup_edit(inputs, 'failed_http', with_local=False)
    base, calls = image_server
    cfg['scene_search'].update(external_providers=['commons'], commons_url=base+'/fail', trust_env=False)
    graph = load_pipeline('benchmark'); graph(run, runs, cfg)
    ingest(run, 'design_candidates', edit_design()); graph(run, runs, cfg)
    row = saved_stage(run, 'incomplete')[0]
    assert row['status'] == 'needs_edit_source'
    assert '503' in row['external_source_search']['searches'][0]['error']
    assert len(calls) == 1
    graph(run, runs, cfg); assert len(calls) == 1
    # Even recomputing the operator cannot repeat the frozen HTTP call.
    search = SceneSearch(run, [], SplitGuard(inputs[2]), cfg)
    asyncio.run(search.external(row)); assert len(calls) == 1


def test_local_scan_ceiling_and_reference_exclusion_are_audited(inputs):
    runs, cfg, asset, manifest, run = setup_edit(inputs, 'scan_ceiling')
    source = manifest[0]
    ref = {**source, **{k: inputs[4][0][k] for k in ('path', 'sha256')}}
    cfg['scene_search']['image_ref'] = raw_manifest([ref, source])
    cfg['scene_search']['max_metadata_records'] = 1
    search = SceneSearch(run, [{'kind':'image','asset':inputs[4][0]}], SplitGuard(inputs[2]), cfg)
    row = {'focus':edit_design()['candidates'][0], 'branch':'benchmark'}
    result = search.local(row)
    assert result['candidates'] == []
    assert result['trace']['records_scanned'] == 1 and not result['trace']['full_manifest_scanned']
    assert result['trace']['excluded'][0]['reason'] == 'Published reference or near duplicate'


def test_commons_filters_documents_and_keeps_original_download_provenance(inputs, monkeypatch):
    _, cfg, _, _, run = setup_edit(inputs, 'commons_media')
    search = SceneSearch(run, [], SplitGuard(inputs[2]), cfg)
    original = 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Photo.jpg?utm_source=commons&download=1'
    seen = []
    async def fetch(client, url, **kwargs):
        seen.append(kwargs['params'])
        return json.dumps({'query': {'pages': {
            '1': {'index': 1, 'imageinfo': [{'url': 'https://example.test/document.pdf', 'mime': 'application/pdf'}]},
            '2': {'index': 2, 'imageinfo': [{'url': original, 'mime': 'image/jpeg',
                'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Photo.jpg'}]}}}}).encode()
    monkeypatch.setattr(search, 'fetch', fetch)
    hits = asyncio.run(search.provider(None, 'commons', 'subject scene'))
    assert seen[0]['gsrsearch'] == 'subject scene filetype:bitmap'
    assert len(hits) == 1
    assert hits[0]['content_url'] == original.split('?')[0] + '?download=1'
    assert hits[0]['original_content_url'] == original


def test_commons_query_backoff_keeps_scene_requirements_and_frozen_budget(inputs, image_server, monkeypatch):
    runs, cfg, _, _, run = setup_edit(inputs, 'query_backoff', with_local=False)
    base, calls = image_server
    cfg['scene_search'].update(external_providers=['commons'], commons_url=base+'/api', trust_env=False,
                               max_external_queries=1, max_external_downloads=1)
    design = edit_design()
    full = 'diagram decision node close view'
    design['candidates'][0]['search_queries'] = [full, 'must not be searched']
    original_provider = SceneSearch.provider
    seen = []
    async def provider(self, client, name, query):
        seen.append(query)
        if query == full:
            return []
        return await original_provider(self, client, name, query)
    monkeypatch.setattr(SceneSearch, 'provider', provider)
    graph = load_pipeline('benchmark')
    graph(run, runs, cfg)
    ingest(run, 'design_candidates', design)
    graph(run, runs, cfg)
    row = saved_stage(run, 'edit_external_source')[0]
    assert row['status'] == 'pending_select_edit_source_external'
    assert seen == [full, 'diagram decision node']
    assert row['focus']['scene_requirements'] == design['candidates'][0]['scene_requirements']
    trace = row['external_source_search']
    assert [s['query_mode'] for s in trace['searches']] == ['full', 'prefix3']
    assert all(s['author_query'] == full for s in trace['searches'])
    assert trace['downloads'] == 1 and len(calls) == 2
    graph(run, runs, cfg)
    assert seen == [full, 'diagram decision node'] and len(calls) == 2


@pytest.mark.parametrize('has_thumbnail', [True, False])
def test_commons_optional_thumbnail_keeps_original_source_and_pixels(inputs, image_server, monkeypatch, has_thumbnail):
    _, cfg, _, _, run = setup_edit(inputs, 'thumbnail', with_local=False)
    base, calls = image_server
    cfg['scene_search'].update(external_providers=['commons'], commons_thumbnail_width=1280, commons_url=base+'/api', trust_env=False,
                               max_external_downloads=1, max_external_queries=1)
    search = SceneSearch(run, [], SplitGuard(inputs[2]), cfg)
    original = base + ('/original-not-downloaded' if has_thumbnail else '/image')
    real_fetch = search.fetch
    seen = []
    async def fetch(client, url, **kwargs):
        if url == base+'/api':
            seen.append(kwargs['params'])
            info = {'url': original, 'mime': 'image/png', 'descriptionurl': base+'/page'}
            if has_thumbnail:
                info.update(thumburl=base+'/image', thumbwidth=1280, thumbheight=960)
            return json.dumps({'query': {'pages': {'1': {'index': 1, 'imageinfo': [info]}}}}).encode()
        return await real_fetch(client, url, **kwargs)
    monkeypatch.setattr(search, 'fetch', fetch)
    result = asyncio.run(search.external({'focus': edit_design()['candidates'][0], 'branch': 'benchmark'}))
    assert seen[0]['iiurlwidth'] == 1280
    asset = result['candidates'][0]
    assert asset['source']['original_content_url'] == original
    assert asset['source']['download_variant'] == ('thumbnail' if has_thumbnail else 'original')
    assert asset['source']['content_url'] == base+'/image'
    assert __import__('curation.preparation.materials',fromlist=['asset_pixels']).asset_pixels(asset) == Path(inputs[4][1]['path']).read_bytes()
    assert calls == ['/image']
