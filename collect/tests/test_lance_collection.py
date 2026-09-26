"""Collection boundaries exercised without external services."""
import asyncio
from types import SimpleNamespace
import lance
from collect.material_schema import IMAGES_URI, DOCUMENTS_URI
from collect.materials import sha


def test_download_and_page_sinks_use_single_tables_only(tmp_path, monkeypatch):
    from collect.operators import download, page
    body = b'fixture download already verified by mocked fetch'
    async def fetch(*args, **kwargs):
        return SimpleNamespace(data=body, sha256=sha(body), size_bytes=len(body),
            url='https://test/image', extra={'mime':'image/png','ext':'png','width':10,'height':20})
    monkeypatch.setattr(download, 'fetch_tiers', fetch)
    row = asyncio.run(download.DownloadStage(str(tmp_path))({'name':'A', 'source':'test', 'tiers':['https://test/image']}))
    assert row['sha256'] == sha(body) and 'blob_path' not in row
    ds = lance.dataset(str(tmp_path/IMAGES_URI))
    assert ds.count_rows() == 1 and ds.take_blobs('data',indices=[0])[0].read() == body
    sink = page.DocsSinkStage(str(tmp_path))
    for text in ['original', 'revised']:
        asyncio.run(sink({'name':'A','page_url':'https://test/page','title':'Page','text':text,'passages':[]}))
    documents = lance.dataset(str(tmp_path/DOCUMENTS_URI))
    assert documents.count_rows() == 2
    assert set(documents.to_table(columns=['text'])['text'].to_pylist()) == {'original','revised'}
    assert not list(tmp_path.rglob('*.jsonl')) and not (tmp_path/'pages').exists()


def test_snippet_retains_body_and_never_becomes_full_page(tmp_path):
    from collect.operators.page import PageFetchStage, DocsSinkStage
    stage = PageFetchStage(str(tmp_path))
    row = stage._snippet_row({'name':'A','page_url':'https://test','snippet':'summary ' * 20}, 'url-hash')
    asyncio.run(DocsSinkStage(str(tmp_path))(row))
    record = lance.dataset(str(tmp_path/DOCUMENTS_URI)).to_table().to_pylist()[0]
    import json
    assert json.loads(record['sources'][0]['attributes_json'])['body'] == 'snippet'
    assert 'summary' in record['text'] and not (tmp_path/'pages').exists()


def test_current_collection_graph_reads_fixed_master_and_keeps_both_branches(tmp_path, monkeypatch):
    import pyarrow as pa
    from demiflow.lance.registry import write_registered_table
    from collect import flow
    from collect.operators import search, download, text_engines, page
    ref, _, _ = write_registered_table(tmp_path, 'master/fixture.lance',
        schema=pa.schema([('name',pa.string()),('aliases',pa.list_(pa.string())),('carriers',pa.list_(pa.string()))]),
        schema_name='fixture',schema_version='v1',fingerprint='master',
        rows_factory=lambda:iter([{'name':'skipped','aliases':[],'carriers':['image','text']},
                                 {'name':'selected','aliases':[],'carriers':['image','text']}]))
    monkeypatch.setattr(flow,'resolve_concept_release',lambda *args,**kwargs:{'dataset_ref':ref,'release_id':'fixture'})
    async def image_search(self,row):return {**row,'source':'test','tiers':['https://test/image']}
    async def text_search(self,row):return {**row,'page_url':'https://test/page','title':'Page'}
    async def page_fetch(self,row):return {**row,'text':'Saved page body.','passages':[]}
    body=b'offline image'
    async def fetch(*args,**kwargs):return SimpleNamespace(data=body,sha256=sha(body),size_bytes=len(body),url='https://test/image',extra={'ext':'png','mime':'image/png','width':10,'height':20})
    monkeypatch.setattr(search.SearchStage,'__call__',image_search)
    monkeypatch.setattr(text_engines.TextSearchStage,'__call__',text_search)
    monkeypatch.setattr(page.PageFetchStage,'__call__',page_fetch)
    monkeypatch.setattr(download,'fetch_tiers',fetch)
    output=flow.run(tmp_path,names=['selected'],limit=1)
    assert set(output)=={'images','documents'}
    for uri in (IMAGES_URI,DOCUMENTS_URI):
        ds=lance.dataset(str(tmp_path/uri))
        assert ds.count_rows()==1
        assert ds.to_table(columns=['concepts'])['concepts'][0].as_py()==['selected']


def test_wiki_dump_graph_keeps_page_identity_links_and_revision(tmp_path):
    from collect.operators.wiki_dump import WikiParseStage
    from collect.flow_kb import CommitPages
    from demiflow import data
    row={'lang':'en','title':'Example','page_id':12,'revision_id':34,
         'is_redirect':False,'redirect_target':None,
         'text':'A page about [[Target]] and [[d:Q123]].'}
    output=(data.from_iter(lambda:iter([row])).map_async(WikiParseStage())
        .batch_map(CommitPages(tmp_path),max_batch=2).run_stream())
    assert output.emitted == 1
    saved=lance.dataset(str(tmp_path/DOCUMENTS_URI)).to_table().to_pylist()[0]
    assert saved['page_id']=='12' and saved['revision_id']=='34' and saved['document_type']=='wiki'
    assert 'Target' in saved['links_json'] and saved['sections']
