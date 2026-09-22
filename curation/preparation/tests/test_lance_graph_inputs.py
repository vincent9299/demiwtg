"""Canonical notebook inputs/checkpoints, entirely in an isolated Lance lake."""
import json
import pyarrow as pa
from demiflow.lance.registry import write_registered_table
from curation.preparation.records import run_records
from curation.preparation.stages import read_stage


def test_knowledge_gather_uses_fixed_lance_sources(tmp_path, monkeypatch):
    from curation.preparation import lake_inputs
    from curation.preparation.pipeline import load_pipeline
    from project import resolve_root
    root = resolve_root()
    from collect.material_schema import DOCUMENTS,IMAGES
    from collect.materials import sha
    specs = {'legacy_concepts':(pa.schema([('raw_payload',pa.string()),('source_row',pa.int64())]),
                 [{'raw_payload':json.dumps({'name':'测试概念'}),'source_row':0}]),
             'legacy_docs':(DOCUMENTS,[{'document_id':'doc','concepts':['测试概念'],
                 'source_identity':'test://page','text':'原始正文','content_sha256':sha('原始正文'),
                 'content_status':'available','sources':[],'sections':[]}]),'legacy_images':(IMAGES,[])}
    refs={}
    for kind,(schema,records) in specs.items():
        ref,_,_=write_registered_table(root,'raw/fixture/'+kind+'.lance',schema_name='fixture',schema_version='v1',
            schema=schema,rows_factory=lambda records=records:iter(records),fingerprint=kind)
        refs[kind]=ref
    def resolve(dataset,kind):return refs[kind],{'kind':kind,'dataset_ref':refs[kind].to_dict()}
    monkeypatch.setattr(lake_inputs,'resolve_source',resolve)
    run = tmp_path/'curation/preparation/runs/knowledge'
    result = load_pipeline()(run,root,project=tmp_path,ids=['legacy:测试概念'],through='gather',source_scope='collected')
    rows = result.take_all()
    assert rows and rows[0]['concept_ref']=='legacy:测试概念'
    assert run_records(run).get('manifest')['sources'][0]['dataset_ref']==refs['legacy_concepts'].to_dict()
    assert not list(run.rglob('*.jsonl'))
    assert not list(run.rglob('*.sqlite'))
    assert read_stage(run,'knowledge_inputs').take_all()==rows


def test_visual_prepare_reads_stage_ref_and_writes_no_jsonl(tmp_path, monkeypatch):
    from curation.preparation.visual_pipeline import load_graph
    from curation.training.tests.test_authoring import publish_fixture
    from project import resolve_root
    from collect.material_schema import IMAGES, IMAGES_URI
    write_registered_table(resolve_root(),IMAGES_URI,schema_name='raw_images',schema_version='v1',schema=IMAGES,
        rows_factory=lambda:iter([]),fingerprint='empty-raw-images')
    run = tmp_path/'curation/preparation/runs/visual'
    source = publish_fixture(tmp_path/'curation/preparation/runs/source','knowledge_base',
                             [{'concept':'测试概念','images':[]}])
    result = load_graph()['run_visual_pipeline'](run,source,resolve_root(),project=tmp_path,through='prepare')
    assert result.take_all()==[]
    assert run_records(run).get('manifest')['source']==source
    assert not list(run.rglob('*.jsonl'))


def test_all_scope_derives_qid_mapping_from_fixed_document_table(tmp_path, monkeypatch):
    """QID/page identity is read from raw documents; no missing side table."""
    from curation.preparation import lake_inputs
    from curation.preparation.pipeline import load_pipeline
    from collect.material_schema import DOCUMENTS, IMAGES, DOCUMENTS_URI, IMAGES_URI
    from collect.materials import sha
    from project import resolve_root
    root = resolve_root()
    documents = [{'document_id':'wiki-en-1', 'document_type':'wiki', 'language':'en',
        'qid':'Q1', 'page_id':'1', 'title':'Example', 'source_identity':'wiki:en:1',
        'sections':[{'title':'Heading', 'text':'Saved source text.', 'attributes_json':'{}'}],
        'content_sha256':sha('Heading\nSaved source text.'), 'concepts':[], 'sources':[],
        'content_status':'available'}]
    specs = [('raw_documents', DOCUMENTS_URI, DOCUMENTS, documents),
             ('raw_images', IMAGES_URI, IMAGES, [])]
    for name, uri, schema, records in specs:
        write_registered_table(root, uri, schema_name=name, schema_version='v2', schema=schema,
            rows_factory=lambda records=records:iter(records), fingerprint=name)
    concept_ref, _, _ = write_registered_table(root, 'master/test.lance', schema_name='fixture',
        schema_version='v1', schema=pa.schema([('raw_payload',pa.string()),('source_row',pa.int64())]),
        rows_factory=lambda:iter([]), fingerprint='empty-concepts')
    original = lake_inputs.resolve_source
    def resolve(dataset, kind):
        if kind == 'legacy_concepts':
            return concept_ref, {'kind':kind, 'dataset_ref':concept_ref.to_dict()}
        return original(dataset, kind)
    monkeypatch.setattr(lake_inputs, 'resolve_source', resolve)
    run = tmp_path / 'curation/preparation/runs/all_scope'
    output = load_pipeline()(run, root, project=tmp_path, ids=['qid:Q1'], through='gather', source_scope='all').take_all()
    assert len(output) == 1 and output[0]['concept_ref'] == 'qid:Q1'
    docs = read_stage(run, 'documents_processed').take_all()
    assert len(docs) == 1 and docs[0]['read_status'] == 'readable'
    sources = run_records(run).get('manifest')['sources']
    assert all('qid_concepts/' not in s['dataset_ref']['relative_uri'] for s in sources)
