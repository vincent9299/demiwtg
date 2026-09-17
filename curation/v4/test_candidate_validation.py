from curation.v4.ops.knowledge_stages import family
from curation.v4.ops.quality_policy import defer_unverifiable_facts,quarantine_conflicts


def material(url):return {'kind':'legacy_docs','record':{'url':url},'material_id':'M1'}
def fact(id,statement,quote):return {'fact_id':id,'statement':statement,'conditions':[],'exceptions':[],'evidence':[{'source_id':'S1','quote':quote}]}


def test_wiki_variants_share_page_family_without_merging_distinct_articles():
    urls=['https://zh.wikipedia.org/wiki/芦笙','https://zh.wikipedia.org/zh/芦笙',
          'http://zh.m.wikipedia.org/zh-hans/芦笙','https://zh.wikipedia.org/w/index.php?title=芦笙&oldid=123']
    assert len({family(material(u)) for u in urls})==1
    assert family(material(urls[0]))!=family(material('https://zh.wikipedia.org/wiki/笙'))
    assert family(material('https://example.org/?id=1'))!=family(material('https://example.org/?id=2'))


def test_bad_quote_isolates_whole_fact_but_not_independent_facts():
    r={'facts':[fact('F1','A fact','Original statement.'),fact('F2','Bad','Original ... statement.')],'unresolved_conflicts':[]}
    out=defer_unverifiable_facts(r,[{'source_id':'S1','text':'Original statement.'}])
    assert [f['fact_id'] for f in out['facts']]==['F1']
    assert out['deferred_facts'][0]['fact']['evidence'][0]['quote']=='Original ... statement.'
    assert len(r['facts'])==2


def test_uncertainty_scale_error_is_deferred_even_when_quote_is_exact():
    q='The age is 13.799 ± 0.021 billion years, as of 2015.'
    r={'facts':[fact('F1','137.99 ± 0.021亿年',q),fact('F2','13.799 ± 0.021 billion years',q)],'unresolved_conflicts':[]}
    out=defer_unverifiable_facts(r,[{'source_id':'S1','text':q}])
    assert [f['fact_id'] for f in out['facts']]==['F2']
    assert out['deferred_facts'][0]['fact']['fact_id']=='F1'
    final=quarantine_conflicts(out)
    assert final['deferred_facts']==out['deferred_facts']


def test_consolidation_cannot_silently_restore_deferred_fact():
    f=fact('F1','Claim','Original statement.')
    prior={'deferred_facts':[{'fact':f,'reasons':['invalid_original_quote'],'next_action':'verify'}]}
    out=defer_unverifiable_facts({'facts':[f],'unresolved_conflicts':[]},[{'source_id':'S1','text':'Original statement.'}],prior)
    assert not out['facts'] and out['deferred_facts']


def test_number_check_recognizes_numbers_inside_chinese_sentence():
    q='Length is 12 cm.'
    r={'facts':[fact('F1','长度为100 cm。',q)],'unresolved_conflicts':[]}
    out=defer_unverifiable_facts(r,[{'source_id':'S1','text':q}])
    assert not out['facts'] and out['deferred_facts']


def test_etymology_keeps_the_source_word_as_its_subject():
    q='The word universe derives from the Old French word univers.'
    r={'facts':[fact('F1','“宇宙”一词源自古法语。',q),fact('F2','英语 universe 一词源自古法语 univers。',q)],'unresolved_conflicts':[]}
    out=defer_unverifiable_facts(r,[{'source_id':'S1','text':q}])
    assert [f['fact_id'] for f in out['facts']]==['F2']


def test_model_cannot_close_unselected_topics_as_non_core():
    from curation.v4.ops.quality_policy import retain_extraction_scope
    r=retain_extraction_scope({'facts':[],'coverage_note':'Regional knowledge is non-core.'},[{'source_id':'S1','material_id':'M1','start':0,'end':50}])
    assert r['model_coverage_note']=='Regional knowledge is non-core.'
    assert r['remaining_knowledge_review'][0]['next_action']=='continue_knowledge_review_in_supplied_passage'
    assert 'Regional knowledge is non-core.' not in r['coverage_note']


def test_document_priority_uses_traceable_citations_not_all_navigation_links():
    from curation.v4.ops.passage_selection import document_priority
    def doc(url):return {'cleaning':{'blocks':[{'decision':'keep','links':[{'target':url}]}]}}
    assert document_priority(doc('https://example.org/#cite_note-1'))['citation_band']==1
    assert document_priority(doc('https://example.org/login'))['citation_band']==0
    assert not document_priority(doc('https://example.org/#cite_note-1'),'related_context')['direct_subject']


def test_identity_fills_remaining_budget_with_page_variants(tmp_path):
    import asyncio,json
    from curation.v4.ops.knowledge_stages import ResolveIdentity
    from curation.v4.ops.cleaning import clean_document
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


def test_unchecked_images_are_preserved_as_budget_followups(tmp_path):
    import asyncio
    from curation.v4.ops.knowledge_stages import OrganizeMaterials
    images=[{'material_id':'M'+str(i),'kind':'legacy_images','record':{'sha256':str(i)*64},'provenance':{}} for i in [1,2]]
    row={'identity':{'accepted_material_ids':['M1','M2']},'identity_materials':images}
    config={'max_docs':2,'max_input_chars':1000,'max_chars_per_doc':1000,'max_images':0}
    r=asyncio.run(OrganizeMaterials(tmp_path,config,None).process(row))
    assert len(r['material_pack']['omissions'])==2
    assert all(x['reason']=='image_support_budget' for x in r['material_pack']['omissions'])


def test_omitted_redundant_list_entry_uses_explicit_review_not_a_guess():
    from curation.v4.ops.quality_policy import complete_identity_membership,defer_invalid_identity_quotes
    previews=[{'material_id':'M1','title':'X'},{'material_id':'M2','text_preview':'Related X'}]
    reviews=[{'material_id':'M1','relation':'same_identity','basis':'text','quote':'X','reason':'same'},
             {'material_id':'M2','relation':'related_context','basis':'text','quote':'Related X','reason':'related'}]
    raw={'status':'resolved','accepted_material_ids':['M1'],'rejected_materials':[],'material_reviews':reviews}
    out=defer_invalid_identity_quotes(complete_identity_membership(raw,previews),previews)
    assert out['accepted_material_ids']==['M1','M2'] and out['protocol_issues']
    assert raw['accepted_material_ids']==['M1']
    raw['material_reviews']=reviews[:1]
    assert complete_identity_membership(raw,previews)['accepted_material_ids']==['M1']


def test_full_matching_calendar_date_translation_without_licensing_other_numbers():
    q='On May 20, 2006, the technique was listed.'
    r={'facts':[fact('F1','2006年5月20日列入。',q),fact('F2','2006年6月20日列入。',q),fact('F3','2006年5月20日列入5项。',q)]}
    out=defer_unverifiable_facts(r,[{'source_id':'S1','text':q}])
    assert [f['fact_id'] for f in out['facts']]==['F1']


def test_added_conditional_consequence_is_withheld():
    q='Playing is prohibited between spring and harvest.'
    r={'facts':[fact('F1','禁止演奏，否则会冒犯神灵。',q),fact('F2','春季至收获期间禁止演奏。',q)]}
    out=defer_unverifiable_facts(r,[{'source_id':'S1','text':q}])
    assert [f['fact_id'] for f in out['facts']]==['F2']
    assert 'conditional_consequence_not_in_quoted_evidence' in out['deferred_facts'][0]['reasons']


def test_repeated_defer_keeps_one_fact_and_all_reasons():
    f=fact('F1','X','Missing quote')
    prior={'deferred_facts':[{'fact':f,'reasons':['first_check'],'next_action':'verify_citation_and_quantity_against_source'}]}
    out=defer_unverifiable_facts({'facts':[f]},[{'source_id':'S1','text':'Original text'}],prior)
    assert len(out['deferred_facts'])==1
    assert set(out['deferred_facts'][0]['reasons'])=={'first_check','previous_deferred_fact_requires_review','quote_not_in_supplied_source'}
