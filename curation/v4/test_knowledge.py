"""Offline failure boundaries and resumable multi-stage composition."""
import asyncio
import json
from pathlib import Path
import pytest
from curation.v4.contracts import immutable,read
from curation.v4.pipeline import run,inspect,status
from curation.v4.ops.knowledge_checks import knowledge,evidence
from curation.v4.local_model import LocalModel,CallBudgetExceeded

class FakeModel:
    def __init__(self,run,config):self.calls=0;self.reused=0
    async def aclose(self):pass
    async def json(self,stage,messages):
        self.calls+=1
        data=json.loads(messages[1]['content'].split('输入数据：\n')[1])
        if stage=='identity':
            ambiguous=data['request']['value']=='ambiguous'
            result={'status':'ambiguous' if ambiguous else 'resolved','target_label':'示例概念','reason':'test source identities',
                    'accepted_material_ids':[] if ambiguous else [m['material_id'] for m in data['materials']],
                    'rejected_materials':[{'material_id':m['material_id'],'reason':'ambiguous'} for m in data['materials']] if ambiguous else [],'identity_groups':[]}
            result['material_reviews']=[{'material_id':m['material_id'],'relation':'uncertain' if ambiguous else 'same_identity',
                'basis':'metadata' if 'images' in m['kind'] else 'text','quote':(m.get('text_preview') or m.get('title') or m.get('caption') or '')[:30],
                'reason':'fixture source association'} for m in data['materials']]
        else:
            source=data['passages'][0]
            result={'facts':[{'fact_id':'F1','statement':'A source-supported statement','conditions':[],'exceptions':[],
                             'evidence':[{'source_id':source['source_id'],'quote':source['text'][:40]}]}],
                    'unresolved_conflicts':[],'coverage_note':'fixture scope','changes':[]}
        return result,{'usage':{},'model':'fake'}


def prepared(tmp_path):
    p=tmp_path/'state/curation/materials';p.mkdir(parents=True)
    immutable(p/'manifest.json',{'project':str(tmp_path),'dataset':str(tmp_path/'datasets/demiwtg')})
    entries=[]
    for value in ['clear','ambiguous']:
        bundle={'concept_id':'concept:'+value,'request':{'kind':'legacy','value':value},'materials':[
            {'kind':'legacy_docs','record':{'title':'Example','url':'https://example.org/page'},'document':{'text':'This source contains a clear statement with its conditions.'},'provenance':{'line':1}}]}
        f=p/'bundles'/f'{value}.json';immutable(f,bundle);entries.append({'path':str(f)})
    immutable(p/'report.json',{'bundles':entries});return p


def test_staged_run_blocks_ambiguity_and_resumes(tmp_path):
    src=prepared(tmp_path);out=tmp_path/'state/curation/knowledge'
    first=run(src,out,{},'organize',FakeModel)
    assert first['calls_executed_this_invocation']==2
    assert first['results'][1]['blocked']['stage']=='identity'
    second=run(src,out,{},'export',FakeModel)
    assert second['calls_executed_this_invocation']==2 # extract + consolidate, identity cached
    assert second['results'][0]['facts']==1
    third=run(src,out,{},'export',FakeModel)
    assert third['calls_executed_this_invocation']==0
    assert all(n==0 for n in third['stage_tasks_executed'].values())
    assert inspect(out,'consolidate','legacy:clear')[0]['result']['facts']
    with pytest.raises(ValueError,match='new run'):run(src,out,{'max_docs':3},'export',FakeModel)


def test_bad_quotes_and_missing_image_pairs_are_not_accepted():
    result={'facts':[{'fact_id':'F1','statement':'invented','conditions':[],'exceptions':[],
                      'evidence':[{'source_id':'S1','quote':'made up'}]}],'unresolved_conflicts':[],'coverage_note':''}
    with pytest.raises(ValueError,match='quote'):knowledge(result,[{'source_id':'S1','text':'actual source'}])
    with pytest.raises(ValueError,match='coverage'):evidence({'images':[{'image_id':'I1','caption':'visible'}],'support':[]},[{'image_id':'I1'}],[{'fact_id':'F1'}])


def test_budget_zero_never_calls_endpoint(tmp_path):
    async def check():
        m=LocalModel(tmp_path,{'base_url':'http://127.0.0.1:8000/v1','model':'qwen3.8-27b','max_output_tokens':50,'max_calls':0})
        try:
            with pytest.raises(CallBudgetExceeded):await m.json('test',[])
            assert not list(tmp_path.glob('calls/*.request.json'))
        finally:await m.aclose()
    asyncio.run(check())


def test_partial_response_not_retried(tmp_path):
    from curation.v4.local_model import UncertainPreviousCall
    from curation.v4.contracts import digest
    async def check():
        cfg={'base_url':'http://127.0.0.1:8000/v1','model':'qwen3.8-27b','max_output_tokens':50,'max_calls':1}
        payload={'model':cfg['model'],'messages':[],'temperature':0,'max_tokens':50,'response_format':{'type':'json_object'},'chat_template_kwargs':{'enable_thinking':False}}
        key=digest({'stage':'test','payload':payload,'endpoint':cfg['base_url']})
        immutable(tmp_path/'calls'/f'{key}.request.json',{})
        m=LocalModel(tmp_path,cfg)
        try:
            with pytest.raises(UncertainPreviousCall):await m.json('test',[])
        finally:await m.aclose()
    asyncio.run(check())


def test_historical_clean_docs_cannot_start_new_extraction(tmp_path):
    src=prepared(tmp_path)
    f=src/'bundles/clear.json'
    bundle=read(f);bundle['materials'][0]['kind']='clean_docs'
    f.write_text(json.dumps(bundle))
    out=tmp_path/'state/curation/new_knowledge'
    with pytest.raises(ValueError,match='Historical clean_docs'):
        run(src,out,{},'identity',FakeModel)
    assert not (out/'calls').exists()
    assert not (out/'pipeline_manifest.json').exists()


def test_identity_does_not_use_error_pages_or_truncated_snippets(tmp_path):
    from .ops.knowledge_stages import ResolveIdentity
    async def check():
        model=FakeModel(tmp_path,{})
        op=ResolveIdentity(tmp_path,{'identity_docs':12,'max_images':2},model)
        materials=[{'kind':'legacy_docs','record':{'title':'Error'},'provenance':{'line':1},
                    'cleaning':{'text':'','status':'unavailable','warnings':['access_error_page_no_body']}},
                   {'kind':'legacy_docs','record':{'title':'Snippet'},'provenance':{'line':2},
                    'cleaning':{'text':'Partial description...','status':'needs_review','warnings':['possible_truncated_snippet']}}]
        out=await op.process({'bundle':{'request':{'kind':'legacy','value':'example'}},'cleaned_materials':materials})
        assert out['blocked']['reason']=='no_materials_in_scanned_scope'
        assert len(out['identity_ineligible'])==2 and model.calls==0
    asyncio.run(check())
