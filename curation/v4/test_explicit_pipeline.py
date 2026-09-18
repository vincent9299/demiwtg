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
    kwargs=dict(ids=ids,group_size=2,project=p,model_config={"global_material_audit":True})
    pipeline(run,d,**kwargs)
    names=['concepts','documents','images','documents_selected','images_selected','documents_unmatched',
           'images_unmatched','missing_concepts','documents_processed','images_processed','concepts_ready','knowledge_inputs']
    for name in names:
        def normalize(value):
            # The formal graph now includes FilterDocumentBlocks after cleaning.
            if isinstance(value,dict):return {k:normalize(v) for k,v in value.items() if k!='clean_filter'}
            if isinstance(value,list):return [normalize(v) for v in value]
            if isinstance(value,str):return value.removesuffix('+block-filter/1')
            return value
        canonical=lambda rs:sorted(json.dumps(normalize(r),sort_keys=True,ensure_ascii=False) for r in rs)
        assert canonical(rows(run,name))==canonical(rows(prior.run,name)),name
    monkeypatch.setattr(ops,'clean_document',forbidden)
    monkeypatch.setattr(ops,'read_source_rows',forbidden)
    monkeypatch.setattr(__import__('demiflow.data.records',fromlist=['iter_file_records']),'iter_file_records',forbidden)
    for _ in range(3):pipeline(run,d,**kwargs)
    with pytest.raises(ValueError):pipeline(run,d,**{**kwargs,'group_size':3})


@pytest.mark.parametrize('invalid_final',[False,True])
def test_explicit_notebook_article_batches_and_durable_replay(data,monkeypatch,invalid_final):
    """257 raw documents cross the 256-row boundary; one final review sees both groups."""
    import asyncio,re,threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from .test_knowledge import FakeModel
    from .ops import prompt_config
    from .ops.paragraph_similarity import EmbedParagraphBatch
    from .ops.material_routing import EncodeImageTextMaterials
    p,d=data;pipeline=notebook_pipeline();calls=[]
    docs=[]
    for i in range(257):
        path=f'pages/source-{i}.md'
        (d/path).write_text(f'同名概念在条件{i}下呈现独立的结构特征。这些结构的形成需要对应的环境条件，原文同时记录了适用范围和例外。')
        docs.append({'concepts':['同名概念'],'path':path,'title':f'来源{i}','url':f'https://example.test/{i}'})
    write_rows(d/'meta/docs.jsonl',docs)
    monkeypatch.setattr(prompt_config,'validate_local_endpoint',lambda *a:None)
    monkeypatch.setattr(EmbedParagraphBatch,'__call__',lambda self,r:{**r,'items':[{**i,'embedding_title_body':[1.,0.]} for i in r['items']]})
    monkeypatch.setattr(EncodeImageTextMaterials,'__call__',lambda self,r:{'case_id':r['case_id'],'text_windows':[],'image_vectors':[]})
    pipeline.__globals__['ArticleTokenBudget']=lambda *a,**k:lambda r:100
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'data':[{'id':'qwen3.8-27b'}]}).encode())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body)
            content=body['messages'][1]['content'];text=content if isinstance(content,str) else '\n'.join(x.get('text','') for x in content)
            if '输入数据：\n' in text:
                payload=json.JSONDecoder().raw_decode(text.split('输入数据：\n')[1])[0]
                if 'request' in payload:
                    result,_=asyncio.run(FakeModel(None,None).json('identity',[{}, {'content':'输入数据：\n'+json.dumps(payload)}]))
                    result['target_label']='模型给另一批起的别名' if len(payload['materials'])==1 else '同名概念'
                else:
                    assert payload['identity_scope']=='同名概念'
                    result={'decisions':[{'unit_id':u['unit_id'],'decision':'selected','relation':'direct','reason':'原文条件'} for u in payload['units']]}
                answer=json.dumps({'result':result})
            else:
                assert 'response_format' not in body
                assert all(x not in text for x in ['image_selection','primary_review','visible_information'])
                payload=text.split('本次材料：\n')[1]
                count=max(map(int,re.findall(r'【资料([0-9]+)】',payload)))
                final='待检查的提取稿' in payload
                if final:assert count==257
                answer='## 结构与条件\n\n'+'\n\n'.join(f'条件{i}下的结构特征。【资料{i}】' for i in range(1,count+1))+'\n【完成】'
                if final and invalid_final:answer=answer.replace('【资料257】','【资料9999】')
                if final:
                    assert body['chat_template_kwargs']['enable_thinking'] is True
                    assert body['chat_template_kwargs']['reasoning_effort'] in {'low','medium'}
                    assert body['max_tokens']==(24576 if body['chat_template_kwargs']['reasoning_effort']=='medium' else 32768)
                    answer='<think>fixture reasoning</think>'+answer
                else:
                    assert body['chat_template_kwargs']=={'enable_thinking':True,'reasoning_effort':'low'}
                    assert body['max_tokens']==8192
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'model':'qwen3.8-27b','choices':[{'finish_reason':'stop','message':{'content':answer}}],
                                        'usage':{'prompt_tokens':10,'completion_tokens':10}}).encode())
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    run=p/'state/curation/articles'
    kwargs=dict(ids=['legacy:同名概念'],project=p,through='export',source_scope='collected',group_size=256,
                model_config={'text_mode':'multimodal','article_mode':True,'base_url':f'http://127.0.0.1:{server.server_port}/v1',
                              'max_calls':None,'max_output_tokens':8192,'identity_docs':256,'identity_images':0,'block_batch_chars':1000000,'text_embedding_model':'fake','image_embedding_model':'fake'})
    try:
        result=pipeline(run,d,**kwargs).take_all()
        assert len(calls)==7
        assert sorted(len(r['materials']) for r in rows(run,'knowledge_inputs'))==[1,256]
        assert len(rows(run,'final_review_requests'))==1
        assert len(rows(run,'final_review_requests')[0]['source_catalog'])==257
        assert len(result)==1 and len(result[0]['audit']['parent_batches'])==2
        if invalid_final:
            assert result[0]['status']=='failed' and result[0]['knowledge']==[]
            assert 'unknown_资料_9999' in result[0]['audit']['validation_issues']
        else:
            assert result[0]['status']=='reviewed'
            topic=result[0]['knowledge'][0]
            assert len(topic['content']['paragraphs'])==257 and len(topic['references'])==257
            assert all(r['paragraph_indices'] for r in topic['references'])
        assert pipeline(run,d,**kwargs).take_all()==result
        # Lose final stage files and business cache: prompt journal must replay exactly.
        import shutil
        for name in ['final_review_requests','final_review']:(run/'datasets'/f'{name}.jsonl').unlink()
        shutil.rmtree(run/'cache/final_review')
        (run/'knowledge_base.jsonl').unlink()
        recovered=pipeline(run,d,**kwargs).take_all()
        assert recovered[0]['knowledge']==result[0]['knowledge']
        assert recovered[0]['status']==result[0]['status'] and len(calls)==7
        assert len(list((run/'knowledge/calls').glob('*.response.json')))==7
        # Change only final-review strategy: the real notebook must reuse drafts.
        child=p/'state/curation/final-only'
        revised={**kwargs,'model_config':{**kwargs['model_config'],'final_review_effort':'medium','final_max_output_tokens':24576}}
        again=pipeline(child,d,reuse_extraction=run,**revised).take_all()
        assert len(calls)==8 and again[0]['knowledge']==result[0]['knowledge']
        assert (child/'datasets/paragraph_extract.jsonl').read_bytes()==(run/'datasets/paragraph_extract.jsonl').read_bytes()
        with pytest.raises(ValueError,match='scope'):
            pipeline(p/'state/curation/wrong-scope',d,reuse_extraction=run,**{**revised,'ids':['legacy:other']})
        if not invalid_final:
            identity_parent=p/'state/curation/identity-only'
            pipeline(identity_parent,d,**{**kwargs,'through':'identity'}).take_all()
            assert len(calls)==10 and not (identity_parent/'datasets/multimodal_materials.jsonl').exists()
            def no_raw_scan(*a,**k):raise AssertionError('Identity reuse must not rescan raw data')
            monkeypatch.setattr(__import__('demiflow.data.records',fromlist=['iter_file_records']),'iter_file_records',no_raw_scan)
            organized=pipeline(p/'state/curation/from-identity',d,reuse_filter_inputs=identity_parent,
                               **{**kwargs,'through':'organize'}).take_all()
            assert len(calls)==12 and len(organized)==2
    finally:server.shutdown();server.server_close();worker.join()


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


def test_empty_concept_reaches_final_output_without_a_model_call(data,monkeypatch):
    from .ops.material_routing import EncodeImageTextMaterials
    from .ops import prompt_config
    p,d=data
    write_rows(d/'meta/docs.jsonl',[])
    pipeline=notebook_pipeline()
    pipeline.__globals__['ArticleTokenBudget']=lambda *a,**k:lambda r:100
    monkeypatch.setattr(EncodeImageTextMaterials,'__call__',lambda self,r:{'case_id':r['case_id'],'text_windows':[],'image_vectors':[]})
    monkeypatch.setattr(prompt_config,'validate_local_endpoint',lambda *a:None)
    run=p/'state/curation/empty'
    result=pipeline(run,d,ids=['legacy:同名概念'],project=p,through='export',source_scope='collected',
                    model_config={'text_mode':'multimodal','article_mode':True,'max_calls':0,
                                  'text_embedding_model':'fake','image_embedding_model':'fake'}).take_all()
    assert len(result)==1 and result[0]['concept']=='同名概念'
    assert result[0]['status']=='insufficient_materials' and result[0]['knowledge']==[]
    assert result[0]['status_reason']=='no_materials_in_scanned_scope'
    assert not list((run/'knowledge/calls').glob('*.request.json'))
