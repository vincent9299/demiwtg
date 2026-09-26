import json
import lance
from collect.materials import sha,source_record,document_identity
from collect.material_writer import write_images,write_documents
from collect.material_schema import IMAGES_URI,DOCUMENTS_URI
from demiflow.lance.registry import Catalog


def image_row(data,concept,available=True):
    source=source_record({'concepts':[concept],'caption':concept},system='test')
    return {'sha256':sha(data),'data':data if available else None,'ext':'png',
        'byte_size':len(data) if available else 0,'storage_mode':'lance_blob',
        'concepts':[concept],'sources':[source],'availability':'available' if available else 'metadata_only','resolution':None}


def test_image_concepts_merge_and_missing_bytes_can_be_filled(tmp_path):
    first=image_row(b'bytes','A',False);write_images(tmp_path,[first])
    before=Catalog(tmp_path).registered()[-1]
    write_images(tmp_path,[image_row(b'bytes','B')])
    ds=lance.dataset(str(tmp_path/IMAGES_URI))
    assert ds.count_rows()==1
    assert ds.to_table(columns=['concepts'])['concepts'][0].as_py()==['A','B']
    assert ds.take_blobs('data',indices=[0])[0].read()==b'bytes'
    assert before.open(tmp_path).take_blobs('data',indices=[0])[0] is None
    write_images(tmp_path,[image_row(b'bytes','B')])
    assert lance.dataset(ds.uri).count_rows()==1


def test_document_versions_keep_distinct_bodies_and_merge_concepts(tmp_path):
    def row(text,concept):
        return {'document_id':document_identity('https://source',sha(text)),
            'source_identity':'https://source','content_sha256':sha(text),'text':text,
            'sections':[],'concepts':[concept],'sources':[source_record({'concepts':[concept]},system='test')],
            'content_status':'available'}
    write_documents(tmp_path,[row('v1','A'),row('v1','B'),row('v2','A')])
    ds=lance.dataset(str(tmp_path/DOCUMENTS_URI))
    assert ds.count_rows()==2
    assert ds.to_table(filter="text = 'v1'",columns=['concepts'])['concepts'][0].as_py()==['A','B']


def test_generated_transport_keeps_provenance_and_concept_associations(tmp_path):
    from PIL import Image
    from tools.lake_migration.import_generated_evidence import import_results
    from collect.materials import generation_origin
    path=tmp_path/'image.png';Image.new('RGB',(7,9),'blue').save(path)
    key=sha(path.read_bytes());result=tmp_path/'results.jsonl'
    result.write_text('\n'.join(json.dumps({'sha256':key,'file':path.name,'instance':name,'ts':0}) for name in ['A','B']))
    root=tmp_path/'lake'
    import_results(result,root);import_results(result,root)
    ds=lance.dataset(str(root/IMAGES_URI))
    assert ds.count_rows()==1
    row=ds.to_table(columns=['concepts','sources','resolution']).to_pylist()[0]
    assert row['concepts']==['A','B'] and len(row['sources'])==2
    assert generation_origin(row)=='generated'
    assert row['resolution']['width']==7 and row['resolution']['height']==9


def test_invalid_bytes_are_rejected_without_publishing_partial_metadata(tmp_path):
    import pytest
    first = image_row(b'bytes', 'A')
    write_images(tmp_path, [first])
    original = Catalog(tmp_path).registered()[-1]
    with pytest.raises(ValueError, match='availability or size'):
        write_images(tmp_path, [{**image_row(b'bytes', 'B'), 'data':None}])
    assert Catalog(tmp_path).registered()[-1] == original
    assert original.open(tmp_path).to_table(columns=['concepts'])['concepts'][0].as_py() == ['A']
