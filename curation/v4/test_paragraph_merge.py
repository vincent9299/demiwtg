import pytest
from .ops.paragraph_merge import ApplyParagraphMerge,PrepareParagraphMerge


def fixture_row():
    return {'merge_payload':{'items':[{'item_id':'a','type':'text'},{'item_id':'i','type':'image','image_id':'I'}]},
            'joint_prompt':{'passages':[{'source_id':'S','text':'原始条件。'}],'image_ids':['I']},
            'image_observations':{'i':{'item_id':'i','type':'image','image_id':'I','caption':'已有观察','region':'全图','limitations':'身份不确定'}},
            'prompt_result':{'topics':[{'title':'主题','blocks':[
                {'block_id':'T','type':'text','text':'保留条件。','citations':[{'source_id':'S','quote':'原始条件。'}],'input_ids':['a'],'status':'candidate','reason':'整合'},
                {'block_id':'P','type':'image','input_id':'i','caption':'模型试图改写'}]}],
                'decisions':[{'input_id':'a','action':'used','output_block_ids':['T'],'reason':'整合'},
                             {'input_id':'i','action':'used','output_block_ids':['P'],'reason':'保留'}],'coverage_note':''}}


def test_merge_restores_image_observation_without_creating_support():
    out=ApplyParagraphMerge()(fixture_row())
    assert not out['merge_validation_issues']
    b=out['topics'][0]['blocks'][1]
    assert b['caption']=='已有观察' and b['limitations']=='身份不确定'
    assert b['related_block_ids']==[]
    assert all(b['status']=='candidate' for b in out['topics'][0]['blocks'])


def test_unaccounted_input_or_inconsistent_lineage_blocks_publication():
    r=fixture_row();r['prompt_result']['decisions'].pop();r['prompt_result']['topics'][0]['blocks'].pop()
    out=ApplyParagraphMerge()(r)
    assert 'incomplete_input_accounting' in out['merge_validation_issues']
    assert all(b['status']=='deferred' for b in out['topics'][0]['blocks'])
    r=fixture_row();r['prompt_result']['decisions'][0]['output_block_ids']=['P']
    assert 'inconsistent_ancestry_mapping' in ApplyParagraphMerge()(r)['merge_validation_issues']


def test_new_quote_is_checked_against_original_not_generated_prose():
    r=fixture_row();r['prompt_result']['topics'][0]['blocks'][0]['citations'][0]['quote']='保留条件。'
    out=ApplyParagraphMerge()(r)
    assert out['topics'][0]['blocks'][0]['status']=='deferred'
    assert out['topics'][0]['blocks'][1]['status']=='candidate'


def test_merge_budget_refuses_instead_of_truncating():
    r={'run':'r','batch_id':'b','joint_prompt':{'concept':'C','passages':[],'image_ids':[]},'pixel_images':[],'pixel_roles':[],'topics':[]}
    with pytest.raises(ValueError,match='budget'):
        PrepareParagraphMerge(max_payload_chars=1)({'concept':'C','rows':[r]})


def test_quote_repair_keeps_raw_evidence_and_respects_explicit_exclusion():
    block={'block_id':'t','type':'text','text':'候选','citations':[{'source_id':'S','quote':'错引'}],
           'status':'deferred','validation_issues':['quote_not_in_input'],'review':{'status':'supported'}}
    row={'run':'r','batch_id':'b','joint_prompt':{'concept':'C','passages':[{'source_id':'S','text':'准确原文'}],'image_ids':[]},
         'pixel_images':[],'pixel_roles':[],'topics':[{'title':'T','blocks':[block]}]}
    r=PrepareParagraphMerge()({'concept':'C','rows':[row]})
    assert r['merge_payload']['items'][0]['needs_reference_repair']
    assert r['merge_payload']['passages'][0]['text']=='准确原文'
    r=PrepareParagraphMerge([{'run':'r','batch_id':'b','block_id':'t','reason':'范围错误'}])({'concept':'C','rows':[row]})
    assert not r['merge_payload']['items']


def test_missing_inverse_mapping_is_reconstructed_without_rewriting_prose():
    r=fixture_row();r["prompt_result"]["decisions"].pop()
    out=ApplyParagraphMerge()(r)
    assert not out["merge_validation_issues"]
    assert out["accounting_repairs"][0]["input_id"]=="i"
    assert out["topics"][0]["blocks"][0]["text"]=="保留条件。"


def test_quote_alignment_only_restores_unique_literal_formatting():
    from .ops.paragraph_merge import align_source_quote
    source="| --- |\n| 科：  | 芍药科  |\n| 属： | 芍药属 |"
    quote="科： 芍药科 | 属： 芍药属"
    aligned=align_source_quote(quote,source)
    assert aligned in source and aligned!=quote
    assert align_source_quote("科：牡丹科",source)=="科：牡丹科"
    assert align_source_quote("A B","A  B / A B")=="A B"


def test_verify_input_disambiguates_old_and_new_block_ids():
    r=fixture_row();r['merge_payload']['items'][0]['block_id']='old_duplicate'
    out=ApplyParagraphMerge()(r)
    assert out['verify_payload']['expected_block_ids']==['T','P']
    assert all('block_id' not in b for b in out['verify_payload']['items'])


def test_recheck_requests_new_review_only_when_output_review_missing():
    from .recheck_paragraph_merge import RecheckMerge
    r=fixture_row();old=ApplyParagraphMerge()(r)
    old['raw_verification']={'reviews':[{'block_id':'T','status':'supported','reason':'Original evidence'}]}
    old['verification_call']=None;old['verification_error']=None
    assert RecheckMerge()({'saved':old,'request':r})['needs_new_review']
    old['raw_verification']['reviews'].append({'block_id':'P','status':'supported','reason':'Observation unchanged'})
    out=RecheckMerge()({'saved':old,'request':r})
    assert not out['needs_new_review']
    assert all(b['status']=='candidate' for b in out['topics'][0]['blocks'])
