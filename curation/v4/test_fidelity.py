import asyncio
from copy import deepcopy
import pytest
from .ops.fidelity import PrepareFidelity, ApplyFidelity, apply_review


def prepared():
    row={'knowledge':{'facts':[{'fact_id':'F1','statement':'春分禁止吹奏。','conditions':[], 'evidence':[]}],
                      'deferred_facts':[{'fact':{'fact_id':'F2','statement':'旧争议'},'reasons':['unresolved']}]},
         'material_pack':{'passages':[{'source_id':'S1','text':'Between the beginning of spring and autumn harvest',
                                     'source_family':'source','start':0,'end':56}]}}
    return asyncio.run(PrepareFidelity('.',{})(row))


def reviews():
    return {'reviews':[{'fact_id':'F1','verdict':'unsupported','reason':'季节开始被具体化为节气',
                        'source_conditions':[],'issues':[{'kind':'precision','claim':'春分','source_id':'S1',
                                   'source_quote':'beginning of spring','reason':'没有指定春分'}]},
                       {'fact_id':'F2','verdict':'faithful','reason':'语义支持但原有争议未裁决','source_conditions':[],'issues':[]}]}


def test_defers_error_preserves_original_and_prior_deferred():
    row=prepared();before=deepcopy(row)
    out=apply_review(row,reviews(),reviewer='test')
    assert row==before
    assert not out['knowledge']['facts']
    assert [x['fact']['fact_id'] for x in out['knowledge']['deferred_facts']]==['F2','F1']
    assert out['knowledge']['deferred_facts'][1]['fact']==row['knowledge']['facts'][0]
    assert out['fidelity_review']['input_hash']==row['fidelity_input_hash']


@pytest.mark.parametrize('fault',['missing','duplicate','quote','claim','changed','faithful_with_issue'])
def test_rejects_incomplete_or_unbound_reviews(fault):
    row=prepared();result=reviews()
    if fault=='missing':result['reviews'].pop()
    if fault=='duplicate':result['reviews'].append(result['reviews'][0])
    if fault=='quote':result['reviews'][0]['issues'][0]['source_quote']='立春'
    if fault=='claim':result['reviews'][0]['issues'][0]['claim']='不在陈述里'
    if fault=='changed':row['knowledge']['facts'][0]['statement']='改过了'
    if fault=='faithful_with_issue':result['reviews'][0]['verdict']='faithful'
    with pytest.raises(ValueError):apply_review(row,result,reviewer='test')


def test_uncertain_not_pass_and_missing_response_blocks():
    row=prepared();result=reviews();result['reviews'][0].update(verdict='uncertain',issues=[])
    assert not apply_review(row,result,reviewer='test')['knowledge']['facts']
    assert asyncio.run(ApplyFidelity('.',{})(row))['blocked']['stage']=='fidelity'


def test_bad_candidate_quote_is_not_model_source_but_remains_hash_bound():
    from .ops.fidelity import review_input
    row=prepared()
    row['knowledge']['facts'][0]['evidence']=[{'source_id':'S1','quote':'invented ... source words'}]
    before=deepcopy(row)
    payload=review_input(row)
    assert 'invented' not in str(payload)
    assert payload['review_facts'][0]['evidence']==[{'source_id':'S1','quoted_text_matches':False}]
    row['knowledge']['facts'][0]['evidence'][0]['quote']='another invented quote'
    assert review_input(row)['original_facts_sha256']!=payload['original_facts_sha256']
    assert before['knowledge']['facts'][0]['evidence'][0]['quote']=='invented ... source words'


def test_declared_missing_source_condition_cannot_pass():
    row=prepared();result=reviews()
    review=result['reviews'][0]
    review.update(verdict='faithful',issues=[],source_conditions=[{
        'source_id':'S1','quote':'beginning of spring','preserved':False,'reason':'missing condition'}])
    with pytest.raises(ValueError,match='Omitted source condition'):
        apply_review(row,result,reviewer='test')
