"""Revalidate saved, complete responses without repeating model calls."""
import json
from pathlib import Path
from curation.v4.contracts import ROOT


def saved_response(call,expected):
    request_path=Path(call['request_path']);response_path=Path(call['response_path'])
    if not request_path.is_absolute():request_path=ROOT/request_path
    if not response_path.is_absolute():response_path=ROOT/response_path
    request=json.loads(request_path.read_text());response=json.loads(response_path.read_text())
    content=request['payload']['messages'][-1]['content']
    text=content if isinstance(content,str) else '\n'.join(x['text'] for x in content if x['type']=='text')
    actual=json.JSONDecoder().raw_decode(text.split('输入数据：\n',1)[1])[0]
    if expected is not None and actual!=expected:raise ValueError('Saved model input changed')
    choice=response['body']['choices'][0]
    if choice['finish_reason']!='stop':raise ValueError('Cannot normalize incomplete output')
    payload=json.loads(choice['message']['content'])
    if not isinstance(payload,dict) or not isinstance(payload.get('result'),dict):raise ValueError('Missing structured result')
    return actual,payload['result'],{k:v for k,v in payload.items() if k!='result'}


class RestoreJointVerification:
    def __call__(self,row):
        audit=row['saved_verification_calls'][0]
        call=audit.get('call') or (audit.get('error') or {}).get('call')
        _,result,extras=saved_response(call,row['verify_prompt'])
        return {**row,'prompt_result':result,'prompt_call':{**call,'reused':True,'normalization':'Outer metadata ignored; result revalidated'},
                'prompt_error':None,'ignored_outer_metadata':extras}


class RestoreJointMerge:
    def __call__(self,row):
        call=row['saved_merge_review']['call'];actual,result,extras=saved_response(call,None)
        keys=['fact_id','statement','conditions','exceptions','basis','evidence','image_evidence','status']
        def core(facts):return sorted([{k:f.get(k) for k in keys} for f in facts],key=lambda f:f['fact_id'])
        if actual['concept']!=row['merge_prompt']['concept'] or core(actual['facts'])!=core(row['merge_prompt']['facts']):
            raise ValueError('Semantic merge inputs changed; fresh merge required')
        return {**row,'prompt_result':result,'prompt_call':{**call,'reused':True,'normalization':'Only derived verification metadata differs'},
                'prompt_error':None}


class RestoreExactJointMerge:
    """Reapply a complete merge only against its identical frozen input."""
    def __call__(self,row):
        call=row['joint_merge_review']['call']
        _,result,_=saved_response(call,row['merge_prompt'])
        if result!=row['joint_merge_review']['result']:
            raise ValueError('Saved merge result differs from original response')
        return {**row,'prompt_result':result,'prompt_error':None,
                'prompt_call':{**call,'reused':True,'normalization':'Known singleton groups are no-ops; original input unchanged'}}
