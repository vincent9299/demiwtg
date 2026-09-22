import copy
from PIL import Image
from curation.preparation.contracts import digest
from curation.preparation.ops.image_selection import (SelectAvailableImages, ApplyImageSelection, SelectRelatedMaterials, pixels)

def test_images_ignore_metadata_rejection_and_hash_duplicates_keep_links(tmp_path):
    p=tmp_path/'a.png';Image.new('RGB',(32,32),'red').save(p);sha=digest(p.read_bytes())
    m={'kind':'legacy_images','record':{'sha256':sha,'path':str(p),'preannotation':{'description':None}},'provenance':{},'bytes':{'status':'verified_bytes','path':str(p)}}
    row={'cleaned_materials':[m,copy.deepcopy(m)],'identity':{'accepted_material_ids':[]}}
    out=SelectAvailableImages()(row);assert len(out['available_images'])==1 and len(out['image_material_scope']['exact_duplicates'])==1
    p.write_bytes(b'changed')
    import pytest
    assert pixels(out['available_images'])

def test_image_filter_excludes_namesake_and_holds_uncertain():
    row={'case_id':'C','pixel_roles':[],'image_prompt':{'image_ids':['poster','target','maybe'],'selection_protocol':'image-relevance-v2'}}
    def item(i,cr,r,d):return dict(image_id=i,concept_relation=cr,relation=r,decision=d,observability='usable',reason='pixels',visible_information='visible',limitations='scope')
    row['prompt_result']={'images':[item('poster','namesake','unrelated','exclude'),item('target','target','direct','keep'),item('maybe','uncertain','uncertain','pending')]}
    decisions=ApplyImageSelection()(row)['image_decisions']
    out=SelectRelatedMaterials()({'image_decisions':decisions,'available_images':[{'image_id':i} for i in ['poster','target','maybe']]})
    assert [i['image_id'] for i in out['material_pack']['images']]==['target']
    assert len(out['material_pack']['pending_images'])==len(out['material_pack']['excluded_images'])==1
    row['prompt_result']['images'][0]['decision']='keep'
    assert not ApplyImageSelection()(row)['image_decisions'][0]['protocol_valid']
