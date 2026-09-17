"""Offline contract checks for source joins, budgets, immutable runs and resumption."""
import gzip
import hashlib
import json
from pathlib import Path
import pytest
from PIL import Image
from curation.v4.contracts import IdentityRegistry,read
from curation.v4.flow import prepare,inventory
from curation.v4.ops.operators import verify_image
from curation.v4.review import render


def write_rows(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'wt',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')

@pytest.fixture
def data(tmp_path):
    d=tmp_path/'datasets/demiwtg';meta=d/'meta';meta.mkdir(parents=True)
    (meta/'concepts.json').write_text(json.dumps({'concepts':[{'name':'同名概念','taxonomy':[]}]}))
    (meta/'taxonomy.json').write_text(json.dumps({'tree':{'children':[]}}))
    write_rows(meta/'qid_concepts.fat.jsonl.gz',[{'qid':'Q1','en':{'page_id':10,'title':'Example'}}])
    write_rows(d/'corpus/pages-en-part1.jsonl.gz',[
      {'qid':None,'lang':'en','page_id':10,'revision_id':7,'sections':[{'text':'source text'}]},
      {'qid':None,'lang':'zh','page_id':10,'revision_id':8,'sections':[{'text':'wrong language'}]}])
    write_rows(meta/'docs.jsonl',[{'concepts':['同名概念'],'path':'pages/a.md','url':'https://example.org/a'}])
    (d/'pages').mkdir();(d/'pages/a.md').write_text('原始材料正文')
    im=d/'img.png';Image.new('RGB',(8,8),'white').save(im);raw=im.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    path=f'blobs/{sha[:2]}/{sha}.png';(d/path).parent.mkdir(parents=True);im.rename(d/path)
    write_rows(meta/'qid_images.jsonl.gz',[{'qid':'Q1','path':path,'sha256':sha}, {'qid':'Q1','path':'blobs/missing.png','sha256':'0'*64}])
    write_rows(meta/'images.jsonl',[])
    return tmp_path,d


def test_material_join_preserves_conditions_identity_and_source(data):
    p,d=data;run=p/'state/curation/v4/check'
    requests=[{'kind':'qid','value':'Q1'},{'kind':'legacy','value':'同名概念'}]
    result=prepare(d,p,run,requests,50)
    assert len(result['bundles'])==2
    q,l=[read(b['path']) for b in result['bundles']]
    assert q['concept_id']!=l['concept_id']
    pages=[m for m in q['materials'] if m['kind']=='wiki_pages'];assert len(pages)==1
    assert pages[0]['association_method']=='exact_language_page_id_from_sitelink'
    assert pages[0]['record']['revision_id']==7
    assert pages[0]['provenance']['raw_line_sha256']
    checks=[m['bytes']['status'] for m in q['materials'] if m['kind']=='qid_images']
    assert checks==['verified_bytes','not_local']
    assert next(m for m in l['materials'] if m['kind']=='legacy_docs')['document']['text']=='原始材料正文'
    assert q['knowledge_status']=='not_extracted'
    nb=read(render(run));assert len(nb['cells'])==3


def test_resume_skips_completed_work_and_changed_input_rejected(data,monkeypatch):
    p,d=data;run=p/'state/curation/v4/check';req=[{'kind':'qid','value':'Q1'}]
    first=prepare(d,p,run,req,50)
    import curation.v4.operators as ops
    def fail(*a,**k):raise AssertionError('completed work ran twice')
    monkeypatch.setattr(ops,'scan_source',fail);monkeypatch.setattr(ops,'verify_image',fail)
    second=prepare(d,p,run,req,50)
    assert second['execution']['source_tasks_executed']==0
    assert second['execution']['bundles_reused']==1
    assert first['bundles']==second['bundles']
    with pytest.raises(ValueError,match='new run'):prepare(d,p,run,req,51)


def test_partial_scan_is_not_claimed_complete(data):
    p,d=data;run=p/'state/curation/v4/limited'
    r=prepare(d,p,run,[{'kind':'qid','value':'Q1'}],1)
    s=next(s for s in r['sources'] if s['source']['kind']=='qid_images')
    assert s['status']=='budget_limited';assert s['scan']['complete'] is False


def test_image_hash_and_path_checks(data):
    _,d=data
    assert verify_image({'path':'../escape'},d,[])['status']=='invalid_path'
    path=next((d/'blobs').rglob('*.png'))
    assert verify_image({'path':str(path.relative_to(d)),'sha256':'f'*64},d,[])['status']=='hash_mismatch'


def test_internal_identity_is_stable_not_name_merge(tmp_path):
    p=tmp_path/'ids.sqlite';r=IdentityRegistry(p)
    a=r.get({'kind':'qid','value':'Q1'});b=r.get({'kind':'legacy','value':'Q1'});r.close()
    r=IdentityRegistry(p);assert r.get({'kind':'qid','value':'Q1'})==a;assert a!=b;r.close()


def test_inventory_does_not_claim_total_rows(data):
    p,d=data;r=inventory(d,p,p/'state/curation/inv')
    s=next(s for s in r['sources'] if s['kind']=='qid_images')
    assert s['status']=='readable';assert 'qid' in s['sample_fields']
    assert 'count' not in s


def test_runtime_writes_cannot_enter_dataset(data):
    p,d=data
    with pytest.raises(ValueError,match='state/curation'):
        inventory(d,p,d/'bad_run')
    assert not (d/'bad_run').exists()


def test_historical_clean_docs_not_discovered_or_selectable(data):
    p,d=data
    old=p/'state/collect/docs_clean/pages_clean.jsonl'
    write_rows(old,[{'concepts':['同名概念'],'text':'historical processed text'}])
    from curation.v4.sources import discover
    assert all(s['kind']!='clean_docs' for s in discover(d,p))
    with pytest.raises(ValueError,match='Unknown source paths'):
        prepare(d,p,p/'state/curation/new',[{'kind':'legacy','value':'同名概念'}],50,source_paths=[old])
