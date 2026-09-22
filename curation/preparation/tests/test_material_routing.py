from curation.preparation.ops.material_routing import route_materials,PrepareRoutingMaterials


def materials():
    return {'case_id':'C','concept':'concept','passages':[{'source_id':'A','text':'first'},{'source_id':'B','text':'second'}],
            'images':[{'image_id':'I'},{'image_id':'J'}],'native_links':[]}


def test_sparse_routing_preserves_unmatched_text_images_and_no_text_cartesian():
    m=materials();v={'text_windows':[{'source_id':'A','text':'a','embedding':[1,0]},{'source_id':'B','text':'b','embedding':[0,1]}],
                   'image_vectors':[{'image_id':'I','embedding':[1,0]},{'image_id':'J','embedding':[-1,0]}]}
    emb={'A':{'embedding_title_body':[1,0]},'B':{'embedding_title_body':[0,1]}}
    r=route_materials(m,v,emb,threshold=.5)
    assert r['metrics']['calls']==3 and r['metrics']['text_only']==1 and r['metrics']['image_only']==1
    assert [x['image_id'] for q in r['requests'] for x in q['images']]==['I','J']
    assert sum(len(q['passages']) for q in r['requests'])==2
    m['native_links']=[{'source_id':'B','image_id':'J'}]
    r=route_materials(m,v,emb,threshold=.5)
    assert r['metrics']['calls']==2 and r['metrics']['image_only']==0
    assert next(q for q in r['requests'] if q['passages'][0]['source_id']=='B')['images'][0]['image_id']=='J'


def test_overflow_does_not_drop_images_or_repeat_text():
    m=materials();m['images']=[{'image_id':str(i)} for i in range(5)]
    v={'text_windows':[{'source_id':'A','text':'a','embedding':[1,0]},{'source_id':'B','text':'b','embedding':[0,1]}],
       'image_vectors':[{'image_id':str(i),'embedding':[1,0]} for i in range(5)]}
    emb={'A':{'embedding_title_body':[1,0]},'B':{'embedding_title_body':[0,1]}}
    r=route_materials(m,v,emb,threshold=.5,max_images=2)
    assert r['metrics']['image_presentations']==5 and len(r['overflow_edges'])==3
    assert sum(len(q['passages']) for q in r['requests'])==2


def test_similarity_ordered_packing_keeps_all_passages_without_hard_topic_split():
    from curation.preparation.ops.material_routing import packed_text_groups
    ps=[{'source_id':str(i),'text':'x'*n} for i,n in enumerate([10,10,10,10])]
    em={str(i):{'embedding_title_body':v} for i,v in enumerate([[1,0],[1,0],[0,1],[0,1]])}
    groups=packed_text_groups(ps,em,max_chars=20)
    assert [[p['source_id'] for p in g] for g in groups]==[['0','1'],['2','3']]
    assert sum(len(g) for g in groups)==4 and all(sum(len(p['text']) for p in g)<=20 for g in groups)


def test_native_link_requires_exact_url_and_nearby_original_span():
    from curation.preparation.ops.identity import material_id
    m={'kind':'legacy_docs','record':{'url':'https://example.org/page'},'provenance':{},'cleaning':{'blocks':[
        {'kind':'figure','section':['S'],'raw_start':110,'raw_end':120,'images':[{'target':'https://example.org/image.jpg','label':'Original caption'}]},
        {'kind':'figure','section':['S'],'raw_start':9000,'raw_end':9010,'images':[{'target':'https://example.org/image.jpg','label':'Far image'}]}]}}
    p={'source_id':'P','material_id':material_id(m),'sections':['S'],'text':'paragraph','source_blocks':[{'raw_start':10,'raw_end':100}]}
    r={'case_id':'C','identity':{'target_label':'concept'},'cleaned_materials':[m],'material_pack':{'passages':[p],'images':[{'image_id':'I','record':{'content_url':'https://example.org/image.jpg'}}]}}
    out=PrepareRoutingMaterials()(r)
    assert len(out['native_links'])==1 and out['native_links'][0]['original_caption']=='Original caption'
    assert len(out['unmatched_native_references'])==1
