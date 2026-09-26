"""One image entity preserves byte identity, concept scope and frozen releases."""
import hashlib
import json
import lance
from collect.material_writer import write_images
from collect.material_schema import IMAGES_URI
from preparation.operaters.images import write_curation, assessment
from tools.lake_migration.visual_publication import publish_visual_records
from preparation.tests.conftest import raw_metadata_snapshot
from preparation.operaters.inputs import iter_material_rows


def candidate(sha,concept,status='reviewed'):
    return dict(sha256=sha,concept=concept,publication_status=status,
        concept_review={'decision':'keep','reason':'test'},visual_support={'supports':concept,'region':'whole','limitations':''})


def test_image_publication_is_concept_and_release_scoped(tmp_path,monkeypatch):
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT',str(tmp_path))
    key='a'*64
    source=raw_metadata_snapshot(tmp_path,[key])
    first=publish_visual_records(tmp_path,[candidate(key,'A'),candidate(key,'B','not_published')],'first',source_ref=source)
    second=publish_visual_records(tmp_path,[candidate(key,'B')],'second',source_ref=source)
    assert first.relative_uri==second.relative_uri=='demiwtg/preparation/datasets/images.lance'
    assert second.row_count==1
    ds=second.open(tmp_path)
    row=ds.to_table(columns=['concepts','published_concepts','concept_assessments']).to_pylist()[0]
    assert row['concepts']==['A','B'] and row['published_concepts']==['A','B']
    assert len(row['concept_assessments'])==3
    a,source=iter_material_rows({'dataset_ref': first.to_dict(), 'release_id': 'first'})
    assert [r['concept'] for r in a]==['A']
    b,_=iter_material_rows({'dataset_ref': second.to_dict(), 'release_id': 'second'})
    assert [r['concept'] for r in b]==['B']
    assert source['dataset_ref']['lance_version']==first.lance_version
    assert first.open(tmp_path).to_table(columns=['published_concepts'])['published_concepts'][0].as_py()==['A']
    assert ds.count_rows(filter="array_contains(published_concepts, 'B')")==1
    # 直接指定结果表时读取该版本全部已审核可交付关系，不按旧发布名筛选。
    direct, _ = iter_material_rows({'uri': second.relative_uri, 'version': second.lance_version})
    assert sorted(r['concept'] for r in direct) == ['A', 'B']


def test_collection_and_curation_have_independent_tables_and_versions(tmp_path):
    body=b'entity-pixels';key=hashlib.sha256(body).hexdigest()
    source=raw_metadata_snapshot(tmp_path,[key])
    r={'sha256':key,'concept_assessments':[assessment(candidate(key,'reviewed'),'r')]}
    old=write_curation(tmp_path,[r],source_ref=source)
    assert write_curation(tmp_path,[r],source_ref=source).lance_version==old.lance_version
    raw_before=source.open(tmp_path).version
    assert lance.dataset(str(tmp_path/IMAGES_URI)).version==raw_before
    write_images(tmp_path,[{'sha256':key,'ext':'png','data':body,'byte_size':len(body),'storage_mode':'lance_blob',
        'availability':'available','concepts':['collected'],'sources':[],'resolution':None}])
    ds=lance.dataset(str(tmp_path/IMAGES_URI))
    assert ds.to_table(columns=['concepts'])['concepts'][0].as_py()==['collected']
    assert 'concept_assessments' not in ds.schema.names
    assert ds.take_blobs('data',indices=[0])[0].read()==body
    assert source.open(tmp_path).to_table(columns=['availability'])['availability'][0].as_py()=='metadata_only'
    curated=lance.dataset(str(tmp_path/old.relative_uri))
    assert curated.version==old.lance_version and 'data' not in curated.schema.names
    row=curated.to_table().to_pylist()[0]
    assert row['concepts']==['reviewed'] and row['published_concepts']==['reviewed']
    assert json.loads(row['source_refs'][0])==source.to_dict()


def test_curation_rejects_missing_sha_and_wrong_source_entity(tmp_path):
    import pytest
    source=raw_metadata_snapshot(tmp_path,['a'*64])
    with pytest.raises(ValueError,match='absent'):
        write_curation(tmp_path,[{'sha256':'b'*64}],source_ref=source)
    with pytest.raises(ValueError,match='fixed raw'):
        write_curation(tmp_path,[{'sha256':'a'*64}],source_ref={**source.to_dict(),'schema_name':'curated_images'})


def test_article_draft_and_review_share_identity_and_pin_versions(tmp_path):
    from preparation.operaters.article import article_entity, write_articles, article_record
    draft={'concept':'bridge','status':'unreviewed','knowledge':[{'title':'a','content':{'paragraphs':['draft'],'images':[]},'references':[]}]}
    first=write_articles(tmp_path,[article_entity(draft,run_id='r')])
    published={**draft,'status':'reviewed','knowledge':[{'title':'a','content':{'paragraphs':['reviewed'],'images':[]},'references':[]}]}
    second=write_articles(tmp_path,[article_entity(published,run_id='r',release_id='release')])
    assert first.row_count==second.row_count==1
    assert first.open(tmp_path).to_table()['review_status'][0].as_py()=='unreviewed'
    assert article_record(second.open(tmp_path).to_table().to_pylist()[0])['knowledge']==published['knowledge']


def test_annotation_config_selection_does_not_reuse_another_config(tmp_path,monkeypatch):
    import pytest
    from preparation.operaters.images import ReuseImageAnnotations
    from preparation.tests.publication_fixtures import description
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT',str(tmp_path))
    records=[{'annotation_id':cid,'config_id':cid,'status':'done','description':{**description(),'caption':cid}} for cid in ('first','second')]
    ref=write_curation(tmp_path,[{'sha256':'b'*64,'descriptions':records}],source_ref=raw_metadata_snapshot(tmp_path,['b'*64]))
    with pytest.raises(ValueError,match='Multiple valid descriptions'):
        ReuseImageAnnotations(ref.to_dict())({'sha256':'b'*64})
    first=ReuseImageAnnotations(ref.to_dict(),'first')({'sha256':'b'*64})
    second=ReuseImageAnnotations(ref.to_dict(),'second')(first)
    assert second['preannotation']['description']['caption']=='second'
    assert second['preannotation']['config_id']=='second'


def test_visual_entity_input_groups_selected_concepts_and_preserves_source_origin(tmp_path):
    from demiflow import data
    from demiflow.lance.registry import Catalog
    from preparation.operaters.images import read_visual_input
    from collect.materials import source_record
    body=b'raw entity source';key=hashlib.sha256(body).hexdigest()
    source=source_record({'concepts':['A','B'],'generation_origin':'generated','content_url':'test://source'},system='fixture')
    write_images(tmp_path,[{'sha256':key,'ext':'png','data':body,'byte_size':len(body),'storage_mode':'lance_blob',
        'availability':'available','concepts':['A','B'],'sources':[source],'resolution':None}])
    ref=max((r for r in Catalog(tmp_path).registered() if r.relative_uri==IMAGES_URI),key=lambda r:r.lance_version)
    rows=read_visual_input(ref,tmp_path,['B']).take_all()
    assert [r['concept'] for r in rows]==['B']
    assert rows[0]['images'][0]['record']['generation_origin']=='generated'
    assert rows[0]['images'][0]['record']['sources'][0]['content_url']=='test://source'
