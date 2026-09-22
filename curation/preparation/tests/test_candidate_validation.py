from curation.preparation.ops.identity import family

def material(url):
    return {"kind":"legacy_docs", "record":{"url":url}, "material_id":"fixture"}


def test_wiki_variants_share_page_family_without_merging_distinct_articles():
    urls=['https://zh.wikipedia.org/wiki/芦笙','https://zh.wikipedia.org/zh/芦笙',
          'http://zh.m.wikipedia.org/zh-hans/芦笙','https://zh.wikipedia.org/w/index.php?title=芦笙&oldid=123']
    assert len({family(material(u)) for u in urls})==1
    assert family(material(urls[0]))!=family(material('https://zh.wikipedia.org/wiki/笙'))
    assert family(material('https://example.org/?id=1'))!=family(material('https://example.org/?id=2'))

def test_document_priority_uses_traceable_citations_not_all_navigation_links():
    from curation.preparation.ops.passage_selection import document_priority
    def doc(url):return {'cleaning':{'blocks':[{'decision':'keep','links':[{'target':url}]}]}}
    assert document_priority(doc('https://example.org/#cite_note-1'))['citation_band']==1
    assert document_priority(doc('https://example.org/login'))['citation_band']==0
    assert not document_priority(doc('https://example.org/#cite_note-1'),'related_context')['direct_subject']

def test_identity_fills_remaining_budget_with_page_variants(tmp_path):
    import asyncio,json
    from curation.preparation.ops.identity import ResolveIdentity
    from curation.preparation.ops.cleaning import clean_document
    class Model:
        async def json(self,stage,messages):
            data=json.loads(messages[1]['content'].split('输入数据：\n')[1]);ps=data['materials']
            assert len(ps)==3
            return {'status':'resolved','target_label':'X','reason':'test','identity_groups':[],
                'accepted_material_ids':[p['material_id'] for p in ps],'rejected_materials':[],
                'material_reviews':[{'material_id':p['material_id'],'relation':'same_identity','basis':'text',
                    'quote':p['text_preview'],'reason':'test'} for p in ps]},{}
    urls=['https://zh.wikipedia.org/wiki/X','https://zh.wikipedia.org/zh/X','https://en.wikipedia.org/wiki/X']
    ms=[{'kind':'legacy_docs','record':{'url':url},'provenance':{'line':i},'cleaning':clean_document('A complete definition of X.')} for i,url in enumerate(urls)]
    row={'bundle':{'request':{'kind':'legacy','value':'X'}},'cleaned_materials':ms}
    out=asyncio.run(ResolveIdentity(tmp_path,{'identity_docs':3,'identity_images':0,'max_images':0},Model()).process(row))
    assert not out['identity_unexamined']

def test_omitted_redundant_list_entry_uses_explicit_review_not_a_guess():
    from curation.preparation.ops.quality_policy import complete_identity_membership,defer_invalid_identity_quotes
    previews=[{'material_id':'M1','title':'X'},{'material_id':'M2','text_preview':'Related X'}]
    reviews=[{'material_id':'M1','relation':'same_identity','basis':'text','quote':'X','reason':'same'},
             {'material_id':'M2','relation':'related_context','basis':'text','quote':'Related X','reason':'related'}]
    raw={'status':'resolved','accepted_material_ids':['M1'],'rejected_materials':[],'material_reviews':reviews}
    out=defer_invalid_identity_quotes(complete_identity_membership(raw,previews),previews)
    assert out['accepted_material_ids']==['M1','M2'] and out['protocol_issues']
    assert raw['accepted_material_ids']==['M1']
    raw['material_reviews']=reviews[:1]
    assert complete_identity_membership(raw,previews)['accepted_material_ids']==['M1']
