"""Exercise the actual notebook plan without its historical StreamFlow dispatcher."""
import ast
import json
import linecache
from pathlib import Path
import pytest
from .test_flow import data, write_rows


def notebook_pipeline():
    notebook=json.loads((Path(__file__).parent/'knowledge_debug.ipynb').read_text())
    ns={}
    for cell in notebook['cells']:
        source=''.join(cell['source'])
        if cell['cell_type']=='code' and 'from curation.v4.ops.dataset_operators import' in source:
            exec(compile(source,'<notebook imports>','exec'),ns)
        if cell['cell_type']=='code' and source.startswith('def run_pipeline('):
            node=ast.parse(source).body[0]
            code='\n'.join(source.splitlines()[:node.end_lineno])+'\n'
            filename='<explicit notebook pipeline>'
            linecache.cache[filename]=(len(code),None,code.splitlines(keepends=True),filename)
            exec(compile(code,filename,'exec'),ns)
    return ns['run_pipeline']


def rows(run,name):
    return [json.loads(l) for l in (run/'datasets'/f'{name}.jsonl').open()]


def test_explicit_plan_matches_prior_upstream_and_resumes_without_flow(data,monkeypatch):
    import sqlite3
    from .stream_flow import StreamFlow
    from .sources import discover
    from . import dataset_operators as ops
    p,d=data
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念','other'],'path':'pages/a.md'}])
    (d/'meta/concepts.json').write_text('{"concepts":[{"name":"同名概念"},{"name":"other"},{"name":"empty"}]}')
    ids=['legacy:同名概念','legacy:other','legacy:empty','qid:Q1','missing']
    chosen={'legacy_concepts','qid_concepts','legacy_docs','legacy_images','wiki_pages'}
    prior=StreamFlow(p/'state/curation/prior',dataset=d,project=p,ids=ids,group_size=2,
                     sources=[s['path'] for s in discover(d,p) if s['kind'] in chosen])
    prior.prepare_sources();prior.select();prior.process_documents();prior.process_images();prior.summarize();prior.gather()
    def forbidden(*a,**kw):raise AssertionError('Forbidden implicit execution')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    monkeypatch.setattr(ops.ReadSource,'__call__',forbidden)
    monkeypatch.setattr(__import__('curation.v4.sources',fromlist=['discover']),'discover',forbidden)
    for method in ['__init__','prepare_sources','select','summarize','gather','knowledge']:
        monkeypatch.setattr(StreamFlow,method,forbidden)
    run=p/'state/curation/explicit';pipeline=notebook_pipeline()
    kwargs=dict(ids=ids,group_size=2,project=p)
    pipeline(run,d,**kwargs)
    names=['concepts','documents','images','documents_selected','images_selected','documents_unmatched',
           'images_unmatched','missing_concepts','documents_processed','images_processed','concepts_ready','knowledge_inputs']
    for name in names:
        canonical=lambda rs:sorted(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in rs)
        assert canonical(rows(run,name))==canonical(rows(prior.run,name)),name
    monkeypatch.setattr(ops,'clean_document',forbidden)
    monkeypatch.setattr(ops,'read_source_rows',forbidden)
    monkeypatch.setattr(__import__('demiflow.data.records',fromlist=['iter_file_records']),'iter_file_records',forbidden)
    for _ in range(3):pipeline(run,d,**kwargs)
    with pytest.raises(ValueError):pipeline(run,d,**{**kwargs,'group_size':3})


def test_explicit_notebook_executes_named_knowledge_operators(data,monkeypatch):
    import sqlite3
    from .stream_flow import StreamFlow
    from .sources import discover
    import asyncio, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from .test_knowledge import FakeModel
    from .local_model import LocalModel
    from . import prompt_config
    p,d=data;pipeline=notebook_pipeline()
    def forbidden(*a,**kw):raise AssertionError('No dispatcher or database')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    monkeypatch.setattr(StreamFlow,'knowledge',forbidden)
    run=p/'state/curation/explicit'
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id':'qwen3.8-27b'}]}).encode())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            content=body['messages'][1]['content']
            text=content if isinstance(content,str) else '\n'.join(x.get('text','') for x in content)
            payload=json.JSONDecoder().raw_decode(text.split('输入数据：\n')[1])[0]
            stage='identity' if 'request' in payload else 'consolidate' if 'candidates' in payload else 'extract'
            if 'review_facts' in payload:
                result={'reviews':[{'fact_id':f['fact_id'],'verdict':'faithful','reason':'Matches supplied text','source_conditions':[],'issues':[]} for f in payload['review_facts']]}
            else:
                result,_=asyncio.run(FakeModel(None,None).json(stage,[{}, {'content':'输入数据：\n'+json.dumps(payload)}]))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'model':'qwen3.8-27b','choices':[{'finish_reason':'stop',
                'message':{'content':json.dumps({'result':result})}}],
                'usage':{'prompt_tokens':10,'completion_tokens':10}}).encode())
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    monkeypatch.setattr(prompt_config,'validate_local_endpoint',lambda *a:None)
    monkeypatch.setattr(LocalModel,'json',forbidden)
    kwargs=dict(ids=['legacy:同名概念'],project=p,through='export',
                model_config={'base_url':f'http://127.0.0.1:{server.server_port}/v1','max_calls':4})
    try:
        result=pipeline(run,d,**kwargs)
        assert len(calls)==4
        pipeline(run,d,**kwargs)
        assert len(calls)==4
        assert len(list((run/'knowledge/calls').glob('*.request.json')))==4
        assert len(list((run/'knowledge/calls').glob('*.response.json')))==4
        # Crash after per-row stage commit but before Dataset checkpoint commit:
        # durable prompt replay must not invalidate the saved business result.
        original_rows=list(result.iter_rows())
        for stage in ['consolidate','fidelity','evidence','export']:
            (run/'datasets'/f'knowledge_{stage}.jsonl').unlink()
        (run/'knowledge_base.jsonl').unlink()
        recovered=pipeline(run,d,**kwargs)
        def semantic(value):
            if isinstance(value,dict):return {k:semantic(v) for k,v in value.items() if k!='reused'}
            if isinstance(value,list):return [semantic(v) for v in value]
            return value
        assert semantic(list(recovered.iter_rows()))==semantic(original_rows)
        assert len(calls)==4

    finally:
        server.shutdown();server.server_close();worker.join()

    final=list(result.iter_rows())
    assert final[0]['knowledge']['status']=='machine_candidates_ready_for_review'
    assert len(final[0]['knowledge']['facts'])==1
    assert all((run/'datasets'/f'knowledge_{s}.jsonl').exists() for s in
               ['identity','organize','extract','consolidate','fidelity','evidence','export'])


def test_code_fingerprint_ignores_runtime_string_interning_but_detects_edits():
    from .notebook_io import _code_record
    def plan():return {'words': ('source', 'source'), 'set': frozenset({1,2})}
    before=_code_record(plan.__code__)
    for _ in range(20):
        plan()
        assert _code_record(plan.__code__)==before
    def changed():return {'words': ('source', 'changed')}
    assert _code_record(changed.__code__)!=before


def test_image_contract_requires_explicit_image_ids():
    from demiflow.schema import validate_instance, SchemaValidationError
    from .ops.prompt_config import knowledge_prompt_pack
    from .pipeline import DEFAULT
    pack,_=knowledge_prompt_pack(DEFAULT)
    schema=next(p for p in pack.prompts if p.name=='evidence').response_schema
    with pytest.raises(SchemaValidationError):
        validate_instance({'result':{'images':[{'caption':'visible object'}],'support':[]}},schema,label='evidence')


def test_candidate_export_does_not_conflict_when_image_call_is_replayed(tmp_path):
    import asyncio
    from .ops.prompt_operators import BuildCandidateRecords
    row={'case_id':'case','bundle':{'concept_id':'concept','request':{'kind':'legacy','value':'sample'}},
         'image_evidence':{'status':'machine_reviewed','result':{'images':[],'support':[]},'call':{'reused':False}}}
    op=BuildCandidateRecords(tmp_path,{})
    first=asyncio.run(op(row))
    row['image_evidence']['call']['reused']=True
    replay=asyncio.run(op(row))
    assert not first.get('blocked') and not replay.get('blocked')
    assert replay['export']['image_evidence']['call']['reused']
    assert not (tmp_path/'candidates').exists()  # final Dataset checkpoint owns persistence
