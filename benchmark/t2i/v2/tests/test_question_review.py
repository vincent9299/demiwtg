"""审题真实离线链：同题冻结、改题身份、暂缓隔离、请求材料与恢复。"""
import json
from copy import deepcopy
from pathlib import Path
import pyarrow as pa
import pytest
import yaml
from demiflow import data
from demiflow.agent import load_agent_config
from demiflow.operator_llm.call_ref import read_call
from demiflow.operator_llm.sqlite_offline import submit_response
from project import resolve_root
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import config, run_pipeline, DATASETS, PROMPTS, QUESTIONS
from benchmark.t2i.v2.operators import question_review


def source_row(n=1):
    return {'task_id':f'q{n}', 'concept':f'对象{n}', 'taxonomy':[], 'concept_record':None,
        'status':'unreviewed', 'instruction':f'画对象{n}，采用写实风格。',
        'test_points':[{'point':'目标形态','basis':'题意及资料。','criterion':'形态正确。'}],
        'reasoning':None,'evidence_json':'[]','authoring_variant':'standard',
        'authoring_images_json':'[]','authoring_context_json':'[]'}


def result(row, changed=False):
    return {'decision':'ready','reason':'题面与判据一致，材料充分。','changes':['收敛题面'] if changed else [],
        'issues':[], 'question':{'instruction':row['instruction'].replace('，采用写实风格','') if changed else row['instruction'],
                                'test_points':row['test_points']},
        'requirements':[{'requirement':'目标形态成立。','dimension':'task_correctness','is_core':True,
                         'basis':'目标身份由此决定；资料支持。','criterion':'辨認目標形態，合法視角不限制。','applicability':None}]}


def make_config(tmp_path, *, count=2):
    root=resolve_root(); root.mkdir(parents=True,exist_ok=True)
    rows=[{**source_row(n), 'answer_pixels':'PRIVATE_ANSWER','old_score':'PRIVATE_SCORE'} for n in range(1,count+1)]
    schema=pa.schema([*QUESTIONS, ('answer_pixels',pa.string()),('old_score',pa.string())])
    uri=str(root/'original.lance')
    data.from_arrow(pa.Table.from_pylist(rows,schema=schema)).write_lance(uri,mode='overwrite',schema=schema)
    raw=yaml.safe_load((PROMPTS/'question_review.yaml').read_text());raw['runtime']='demiflow'
    raw['model']={'name':'fixture-review','transport':'openai_compatible','base_url':'http://127.0.0.1:4001/v1','api_key_env':'MODELHUB_API_KEY'}
    raw['budgets']={'max_requests':8,'max_turns':1,'max_context_chars':60000}
    raw['options']={'timeout_s':10,'request_options':{'max_tokens':8192}}
    p=tmp_path/'review.yaml';p.write_text(yaml.safe_dump(raw,allow_unicode=True))
    return config(run=root/DATASETS/'review_fixture',through='review',
        review_source={'uri':uri,'version':1},sample_size=count,agent_config=p,max_calls=8,mode='offline')


def test_native_review_resume_changes_identity_and_keeps_hold_out_of_release(tmp_path):
    cfg=make_config(tmp_path);assert config(**cfg)==cfg
    pending=run_pipeline(cfg)
    assert not pending['complete'] and pending['counts']=={'pending':2}
    rows=data.read_lance(**pending['question_review']['outputs']['question_reviews']).take(2)
    for row in rows:
        call=json.loads(row['review_call_json']);req=read_call(call['request_ref'],resolve_root())
        text=json.dumps(req,ensure_ascii=False)
        assert row['original_instruction'] in text and 'PRIVATE_ANSWER' not in text and 'PRIVATE_SCORE' not in text
        response=result(row,changed=True) if row['source_task_id']=='q1' else {
            'decision':'hold','reason':'缺少可核实的决定性知识。','changes':[],
            'issues':['事实尚不能核实。'],'question':None,'requirements':[]}
        submit_response(resolve_root(),call['request_ref'],json.dumps({'api_calls':[], 'response':{'result':response}}),
                        model=call['model'],metadata={'reviewer':'fixture','reviewer_kind':'human'})
    finished=run_pipeline(cfg)
    assert finished['complete'] and finished['counts']=={'ready':1,'hold':1}
    released=data.read_lance(**finished['candidates']).take(3)
    assert len(released)==1 and released[0]['requires_new_answer'] is True
    assert released[0]['task_id']!='q1' and released[0]['source_task_id']=='q1'
    assert released[0]['original_instruction']=='画对象1，采用写实风格。'
    assert json.loads(released[0]['requirements_json'])[0]['id']=='r001'
    assert data.read_lance(**cfg['review_source']).take(1)[0]['instruction']=='画对象1，采用写实风格。'
    again=run_pipeline(cfg)
    assert data.read_lance(**again['candidates']).take(1)[0]['question_revision']==released[0]['question_revision']


def test_checklist_revision_without_prompt_change_reuses_answer_identity():
    definition=load_agent_config(PROMPTS/'question_review.yaml').prompt_pack.prompt_definitions['review_question']
    row=source_row(); prepared={**row,'source_task_id':'q1','original_instruction':row['instruction'],
        'review_status':'ready_to_review','review_prompt_version':definition.version,'question_review_result':result(row)}
    one=question_review.finish(prepared,result_schema=definition.response_schema['properties']['result'])
    assert one['task_id']=='q1' and one['requires_new_answer'] is False
    altered=deepcopy(prepared);altered['question_review_result']['requirements'][0]['criterion']='新增合法变化说明。'
    two=question_review.finish(altered,result_schema=definition.response_schema['properties']['result'])
    assert two['question_revision']!=one['question_revision'] and two['task_id']=='q1'


def test_empty_scope_is_completed_without_model_calls(tmp_path):
    cfg=make_config(tmp_path,count=0);state=run_pipeline(cfg)
    assert state['complete'] and state['candidate_count']==0
    assert data.read_lance(**state['candidates']).take(1)==[]


def test_review_pages_read_only_selected_requests_and_keep_frozen_version(tmp_path, monkeypatch):
    from benchmark.t2i.v2.operators import question_review_viewer as viewer
    cfg=make_config(tmp_path,count=2);run_pipeline(cfg);root=resolve_root()
    run_id=Path(cfg['run']).name;directory=root/'demiwtg/benchmark/t2i/v2/runs'/run_id
    directory.mkdir(parents=True);(directory/'config.json').write_text(json.dumps(cfg))
    snapshot=viewer._question_review_snapshot(root/'demiwtg',run_id)
    rows=data.read_lance(**snapshot['ref']).take(2)
    original_read=viewer.read_call;reads=[]
    def tracked_read(ref, root):
        reads.append(ref)
        return original_read(ref,root)
    monkeypatch.setattr(viewer,'read_call',tracked_read)
    first=viewer._question_review_payload(root/'demiwtg',run_id,page=1,page_size=1,snapshot=snapshot)
    assert first['meta']['recorded']==2 and first['meta']['shown']==1 and first['meta']['pages']==2
    assert len(reads)==1 and reads[0]==json.loads(rows[0]['review_call_json'])['request_ref']
    # 新表头不能改变已选浏览快照；翻页只取另一题，不读取全批请求。
    altered=[{**row,'original_instruction':'NEW_VERSION_ONLY'} for row in rows]
    data.from_items(altered).write_lance(snapshot['ref']['uri'],mode='overwrite')
    second=viewer._question_review_payload(root/'demiwtg',run_id,page=2,page_size=1,snapshot=snapshot)
    assert len(reads)==2 and reads[1]==json.loads(rows[1]['review_call_json'])['request_ref']
    assert second['cases'][0]['source_task_id']!=first['cases'][0]['source_task_id']
    assert second['cases'][0]['original_instruction']!='NEW_VERSION_ONLY'
    assert second['meta']['counts']=={'pending':2}
    with pytest.raises(ValueError,match='fixed snapshot'):
        viewer._question_review_payload(root/'demiwtg',run_id,page=3,page_size=1,snapshot=snapshot)


def test_preparation_excludes_answers_and_retains_material_failures(tmp_path):
    row = {**source_row(), 'answer_pixels': 'PRIVATE_ANSWER', 'old_score': 'PRIVATE_SCORE'}
    prepare = lambda row: question_review.prepare(row, source={'uri': 'questions.lance', 'version': 7}, prompt_version='test')
    prepared = prepare(row)
    assert prepared['review_status'] == 'ready_to_review'
    assert prepared['review_payload']['instruction'] == row['instruction']
    assert 'PRIVATE_' not in json.dumps(prepared['review_payload'])
    row['authoring_images_json'] = json.dumps([{'number': 1, 'object_ref': {'uri': 'file:///missing-image', 'sha256': 'a'*64}}])
    assert prepare(row)['review_status'] == 'invalid_materials'
    row['authoring_images_json'] = json.dumps([{'number': i} for i in range(9)])
    assert '8张' in prepare(row)['review_reason']
    row['evidence_json'] = 'x' * question_review.MAX_ROW_BYTES
    assert prepare(row)['review_status'] == 'invalid_materials'


@pytest.mark.parametrize('fail_after_read', [False, True])
def test_native_codex_callback_reads_only_fixed_documents_and_receives_actual_images(tmp_path, monkeypatch, fail_after_read):
    import io, sys
    from PIL import Image
    from demiflow.collect.documents import store_document
    from demiflow.objects import LocalObjectStore
    cfg=make_config(tmp_path,count=1);root=resolve_root()
    ref=store_document(tmp_path/'docs',b'Independent evidence for this question.',url='https://fixture.invalid/fact',
        final_url='https://fixture.invalid/fact',content_type='text/plain',retrieved_at='2026-01-01T00:00:00Z')
    row=source_row();block=ref['sha256']+':b000000'
    row['concept_record']={'source_record_id':'s1','concept_id':'c1','original_name':row['concept'],
        'assessment_id':'a1','adopted_source':{'uri':'fixture.lance','version':1},'canonical_name':row['concept'],
        'definition':'固定概念定义。','concept_kind':'类别','qualifiers':[],
        'core_facts':[], 'task_sketch':None,'identity_evidence_ids':[block],'gaps':[],
        'evidence':[{'evidence_id':block,'document_ref':ref,'block_id':'b000000'}]}
    buffer=io.BytesIO();Image.new('RGB',(12,12),'red').save(buffer,format='PNG')
    image=LocalObjectStore(root/'objects').put(buffer.getvalue()).to_dict()
    row['authoring_images_json']=json.dumps([{'number':1,'kind':'image','object_ref':image}])
    data.from_arrow(pa.Table.from_pylist([row],schema=QUESTIONS)).write_lance(cfg['review_source']['uri'],mode='overwrite',schema=QUESTIONS)
    cfg['review_source']['version']=2
    # 后续版本含不同题面；Dataset固定读取原版本，再绑定到实际请求。
    later={**row,'instruction':'DO_NOT_READ_LATEST'}
    data.from_arrow(pa.Table.from_pylist([later],schema=QUESTIONS)).write_lance(cfg['review_source']['uri'],mode='overwrite',schema=QUESTIONS)
    marker=tmp_path/'native_sessions'
    fake=tmp_path/'fake_codex'
    fake.write_text('#!'+sys.executable+'\nMARKER = '+repr(str(marker))+'\nRESULT = '+repr(result(row))+
                   '\nFAIL_AFTER_READ = '+repr(fail_after_read)+'\n'+NATIVE_FIXTURE)
    fake.chmod(0o755)
    raw=yaml.safe_load((PROMPTS/'question_review.yaml').read_text())
    raw['model']={'name':'fixture-native-review'}
    raw['options']['codex_agent']['bin']=str(fake);raw['options']['timeout_s']=20
    path=Path(cfg['agent_config']);path.write_text(yaml.safe_dump(raw,allow_unicode=True))
    cfg['mode']='codex'
    from benchmark.t2i.v2.operators import concept_context
    monkeypatch.setattr(concept_context, 'read_document', lambda *a, **k: pytest.fail('must not preload document bodies'))
    state=run_pipeline(config(**cfg))
    assert state['complete'] is (not fail_after_read),state
    assert state['counts']=={'failed' if fail_after_read else 'ready':1},state
    output=data.read_lance(**state['question_review']['outputs']['question_reviews']).take(1)[0]
    call=json.loads(output['review_call_json']);request=read_call(call['request_ref'],root)
    from benchmark.t2i.v2.operators.question_review_viewer import recorded_codex_response
    response=recorded_codex_response(call,root)
    text=json.dumps(request,ensure_ascii=False)
    assert row['instruction'] in text and '固定概念定义' in text
    assert 'DO_NOT_READ_LATEST' not in text
    assert 'Independent evidence for this question.' not in text and 'image_url' in text
    assert 'Independent evidence for this question.' in json.dumps(response)
    if not fail_after_read:
        assert run_pipeline(cfg)['complete'] and marker.read_text()=='session\n'
    from benchmark.t2i.v2.operators.question_review_viewer import build_question_review_browser
    run_dir=root/'demiwtg/benchmark/t2i/v2/runs'/Path(cfg['run']).name
    run_dir.mkdir(parents=True);(run_dir/'config.json').write_text(json.dumps(cfg))
    document,meta=build_question_review_browser(root/'demiwtg',Path(cfg['run']).name)
    payload=json.loads(document.split('<script id="review-data" type="application/json">',1)[1].split('</script>',1)[0])
    case=payload['cases'][0]
    assert meta['recorded']==1 and len(case['actual_images'])==1
    assert 'error' not in case['actual_images'][0]
    assert case['native_tool_events']==[]
    observations=case['operator_observations']
    assert [item['result']['status'] for item in observations]==['error','ok']
    assert 'Independent evidence for this question.' in json.dumps(observations)
    assert case['actual_parameters']['operators']==['read_documents']
    assert case['available_references']==[{'number':1,'kind':'image','object_ref':image}]
    assert 'PRIVATE_THOUGHT' not in document
    # 已结束的回调任务：巡检应记录读取成功，不要求原生终端事件。
    import os, hashlib
    from benchmark.t2i.v2.operators.question_review_monitor import monitor
    (run_dir/'process.json').write_text(json.dumps({'pid':os.getpid(),'create_time':0,
        'agent_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
    monitor(root/'demiwtg',Path(cfg['run']).name)
    watched=json.loads((run_dir/'monitor_status.json').read_text())
    assert watched['callback_document_read_cases']==1 and watched['native_read_cases']==0
    if fail_after_read:
        assert {item['kind'] for item in watched['alerts']}=={'technical_failures','process_exited_before_complete'}
    else:
        assert watched['alerts']==[]


NATIVE_FIXTURE = r'''
import base64, io, json, pathlib, sys, time
from PIL import Image
assert 'features.shell_tool=false' in sys.argv and 'features.view_image=false' in sys.argv
with pathlib.Path(MARKER).open('a') as f: f.write('session\n')
def receive(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
def reply(req, result): send({'id':req['id'], 'result':result})
def note(method, params): send({'method':method,'params':{'threadId':'t',**params}})
def call(arguments):
    send({'id': 'callback-' + str(time.monotonic()), 'method': 'item/tool/call',
          'params': {'threadId':'t', 'turnId':'u', 'callId':'read', 'tool':'read_documents', 'arguments':arguments}})
    return receive()['result']
req=receive(); assert req['method']=='initialize';reply(req,{})
assert receive()['method']=='initialized'
req=receive();assert req['method']=='thread/start'
assert req['params']['sandbox']=='read-only'
assert [tool['name'] for tool in req['params']['dynamicTools']]==['read_documents']
assert set(req['params']['dynamicTools'][0]['inputSchema']['properties'])=={'request'}
reply(req,{'thread':{'id':'t'},'model':req['params']['model']})
req=receive();assert req['method']=='turn/start'
assert req['params']['effort']=='xhigh'
text='\n'.join(p.get('text','') for p in req['params']['input'])
assert RESULT['question']['instruction'] in text and '固定概念定义' in text
assert 'Independent evidence for this question.' not in text
images=[p for p in req['params']['input'] if p['type']=='image']
assert len(images)==1
with Image.open(io.BytesIO(base64.b64decode(images[0]['url'].split(',',1)[1]))) as picture:
    assert picture.size==(12,12) and picture.getpixel((0,0))==(255,0,0)
state=json.JSONDecoder().raw_decode(text.rsplit('当前执行环境：\n',1)[1])[0]
reply(req,{'turn':{'id':'u','status':'inProgress','items':[]}})
note('turn/started',{'turn':{'id':'u','status':'inProgress','items':[]}})
args={'request':{'documents':list(state['resources'].values()),
    'questions':[{'id':'q','text':'Independent evidence'}], 'new_chars':6000,'total_chars':6000}}
bad=json.loads(json.dumps(args));bad['request']['documents'][0]['document_ref']['uri']='file:///outside-this-row'
response=call(bad)
assert response['success'] is False
assert 'outside this row' in response['contentItems'][0]['text']
response=call(args)
assert response['success'] is True
assert 'Independent evidence for this question.' in response['contentItems'][0]['text']
if FAIL_AFTER_READ:
    sys.exit(0)
note('item/completed',{'item':{'id':'thought','type':'reasoning','text':'PRIVATE_THOUGHT'}})
note('item/completed',{'item':{'id':'final','type':'agentMessage','phase':'final_answer','text':json.dumps({'result':RESULT})}})
note('turn/completed',{'turn':{'id':'u','status':'completed','items':[]}})
time.sleep(20)
'''


def test_post_author_review_defers_partial_scope_and_resumes_frozen_handoff(tmp_path):
    from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import append_question_review
    from benchmark.t2i.v2.operators.run_tables import RunTables
    cfg=make_config(tmp_path,count=1)
    parent={'run':cfg['run'], 'question_review':{k:cfg[k] for k in
        ('agent_config','max_calls','concurrency','queue_depth','mode')}}
    records=RunTables(resolve_root(),str(DATASETS/'records__parent_fixture.lance'))
    waiting=append_question_review(parent,{'complete':False,'counts':{'pending':1},
        'candidate_count':0},records)
    assert waiting['question_review']=={'status':'awaiting_authoring'}
    # 探测未完成不影响已经完成出题的独立审核。
    authored={'complete':False,'counts':{'candidate':1},'candidate_count':1,
              'candidates':cfg['review_source'],'designs':None}
    pending=append_question_review(parent,authored,records)
    first=pending['question_review']
    assert first['counts']=={'pending':1} and not pending['complete']
    schema=QUESTIONS
    data.from_arrow(pa.Table.from_pylist([source_row()],schema=schema)).write_lance(
        cfg['review_source']['uri'],mode='overwrite',schema=schema)
    replay={**authored,'candidates':{**cfg['review_source'],'version':2}}
    resumed=append_question_review(parent,replay,records)
    assert resumed['question_review']['sources']==first['sources']
    assert resumed['question_review']['sources']['candidates']['version']==1
    changed={**source_row(),'task_id':'changed-task'}
    data.from_arrow(pa.Table.from_pylist([changed],schema=schema)).write_lance(
        cfg['review_source']['uri'],mode='overwrite',schema=schema)
    with pytest.raises(ValueError,match='handoff changed'):
        append_question_review(parent,{**authored,'candidates':{**cfg['review_source'],'version':3}},records)


def test_explicit_review_subset_dispatches_only_selected_task_ids(tmp_path):
    cfg=make_config(tmp_path,count=3)
    cfg=config(**{**cfg,'sample_size':1,'max_calls':1,'review_task_ids':['q2']})
    state=run_pipeline(cfg)
    assert state['counts']=={'pending':1} and state['question_review']['expected']==1
    rows=data.read_lance(**state['question_review']['outputs']['question_reviews']).take(3)
    assert [r['source_task_id'] for r in rows]==['q2']
    assert state['question_review']['miss']=={}
    call=json.loads(rows[0]['review_call_json'])
    submit_response(resolve_root(),call['request_ref'],json.dumps({'api_calls':[],
        'response':{'result':result(rows[0])}}),model=call['model'],
        metadata={'reviewer':'fixture','reviewer_kind':'human'})
    finished=run_pipeline(cfg)
    assert finished['complete'] and finished['counts']=={'ready':1}
    bad=config(**{**cfg,'run':str(Path(cfg['run']).with_name('unknown_scope')),'review_task_ids':['not-in-source']})
    with pytest.raises(ValueError,match='declared unique question scope'):
        run_pipeline(bad)
    with pytest.raises(ValueError,match='unique nonempty'):
        config(**{**cfg,'sample_size':2,'review_task_ids':['q2','q2']})


def test_codex_output_schema_declares_enum_and_constant_types():
    raw=yaml.safe_load((PROMPTS/'question_review.yaml').read_text())
    def check(node):
        if isinstance(node,dict):
            if 'enum' in node or 'const' in node:
                assert node.get('type')=='string'
            for value in node.values():check(value)
        elif isinstance(node,list):
            for value in node:check(value)
    check(raw['options']['codex_agent']['output_schema'])
