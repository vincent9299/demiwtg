from preparation.operaters.routing import PrepareRoutingMaterials










def test_native_link_requires_exact_url_and_nearby_original_span():
    from preparation.operaters.identity import material_id
    m={'kind':'legacy_docs','record':{'url':'https://example.org/page'},'provenance':{},'cleaning':{'blocks':[
        {'kind':'figure','section':['S'],'raw_start':110,'raw_end':120,'images':[{'target':'https://example.org/image.jpg','label':'Original caption'}]},
        {'kind':'figure','section':['S'],'raw_start':9000,'raw_end':9010,'images':[{'target':'https://example.org/image.jpg','label':'Far image'}]}]}}
    p={'source_id':'P','material_id':material_id(m),'sections':['S'],'text':'paragraph','source_blocks':[{'raw_start':10,'raw_end':100}]}
    r={'case_id':'C','identity':{'target_label':'concept'},'cleaned_materials':[m],'material_pack':{'passages':[p],'images':[{'image_id':'I','record':{'content_url':'https://example.org/image.jpg'}}]}}
    out=PrepareRoutingMaterials()(r)
    assert len(out['native_links'])==1 and out['native_links'][0]['original_caption']=='Original caption'
    assert len(out['unmatched_native_references'])==1
