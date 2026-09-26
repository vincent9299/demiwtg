import asyncio
import pytest
from preparation.operaters.documents import clean_document
from preparation.operaters.identity import material_disposition
from preparation.operaters.identity import ResolveIdentity
from preparation.operaters.identity import validate_identity

def test_pending_material_never_reaches_identity_model(tmp_path):
    class NoModel:
        async def json(self,*a):raise AssertionError('pending material sent to model')
    clean=clean_document('{{unknown physical quantity|12|u=cm}}')
    assert material_disposition(clean)['status']=='pending'
    row={'bundle':{},'cleaned_materials':[{'kind':'wiki_pages','record':{},'provenance':{},'cleaning':clean}]}
    out=asyncio.run(ResolveIdentity(tmp_path,{'identity_docs':12,'max_images':2}).prepare(row))
    assert out['blocked'] and out['identity_ineligible'][0]['disposition']['status']=='pending'

def test_mediawiki_shell_keeps_infobox_conditions_and_references():
    raw='Main menu\nView history\n85 languages\nFrom Wikipedia, the free encyclopedia\n| Limit | 12 cm |\nOnly when static.\nReferences\n[paper](https://example.org)\nRetrieved from old revision\nPrivacy policy\n'
    c=clean_document(raw)
    assert '85 languages' not in c['text'] and 'Privacy policy' not in c['text']
    assert '| Limit | 12 cm |' in c['text'] and 'Only when static.' in c['text'] and 'References' in c['text']
    for b in c['blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if 'clean_start' in b:assert c['text'][b['clean_start']:b['clean_end']]==b['text']

def test_mixed_language_goes_to_review_not_permanent_rejection():
    raw='木兰将军\n'+' '.join(['nifolu','caputu','vadis','wud','suwso','nelaze']*10)
    d=material_disposition(clean_document(raw))
    assert d['status']=='pending' and 'review_language_and_readability' in d['next_actions']
    assert raw.splitlines()[1] in clean_document(raw)['text']

def test_identity_rejects_unreadable_accept_and_pixel_claim():
    base={'status':'resolved','target_label':'x','reason':'x','accepted_material_ids':['M1'],'rejected_materials':[],
          'material_reviews':[{'material_id':'M1','relation':'unreadable','basis':'metadata','quote':'x','reason':'x'}]}
    materials=[{'material_id':'M1'}];previews=[{'material_id':'M1','kind':'legacy_images','title':'x'}]
    with pytest.raises(ValueError,match='unreadable'):validate_identity(base,materials,previews)
    base['material_reviews'][0].update(relation='same_identity',basis='text')
    with pytest.raises(ValueError,match='no image pixels'):validate_identity(base,materials,previews)

def test_bad_identity_quote_isolated_related_context_retained():
    from preparation.operaters.identity import defer_invalid_identity_quotes
    previews=[{'material_id':'M1','kind':'legacy_docs','text_preview':'The instrument accompanies the dance.'},
              {'material_id':'M2','kind':'legacy_docs','text_preview':'Long continuous source description.'}]
    r={'status':'resolved','target_label':'instrument','reason':'source evidence', 'accepted_material_ids':['M1','M2'],'rejected_materials':[],
       'material_reviews':[{'material_id':'M1','relation':'related_context','basis':'text','quote':'instrument accompanies the dance','reason':'usage relationship'},
                           {'material_id':'M2','relation':'same_identity','basis':'text','quote':'Long ... description.','reason':'not verbatim'}]}
    out=defer_invalid_identity_quotes(r,previews)
    validate_identity(out,[{'material_id':'M1'},{'material_id':'M2'}],previews)
    assert out['accepted_material_ids']==['M1'] and out['status']=='resolved'
    assert out['protocol_issues'][0]['original_review']['quote']=='Long ... description.'
    assert out['material_reviews'][1]['relation']=='uncertain'
    assert r['accepted_material_ids']==['M1','M2']
