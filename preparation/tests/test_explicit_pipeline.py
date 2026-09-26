"""Exercise the actual Python pipeline without its historical StreamFlow dispatcher."""
import ast
import json
import linecache
from pathlib import Path
import pytest
from preparation.tests.fixtures import data, write_rows


from preparation.preparation_pipeline import run_pipeline


def rows(run, name):
    from preparation.operaters.runfiles import read_stage
    return read_stage(run, name).take_all()


def ingest_sources(dataset, monkeypatch):
    """Import test source files once; the actual graph consumes only fixed refs."""
    import gzip
    import pyarrow as pa
    from demiflow.lance.registry import write_registered_table
    from preparation.operaters import inputs as lake_inputs
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(dataset))
    from collect.material_schema import DOCUMENTS, IMAGES
    from collect.materials import sha, document_identity
    concept_schema = pa.schema([('raw_payload',pa.large_string()),('source_row',pa.int64())])
    values=json.loads((dataset/'meta/concepts.json').read_text())['concepts']
    records=[{'raw_payload':json.dumps(v,ensure_ascii=False),'source_row':i} for i,v in enumerate(values)]
    specs={'legacy_concepts':(concept_schema,records)}
    documents=[]
    for line in (dataset/'meta/docs.jsonl').read_text().splitlines():
        if not line:continue
        value=json.loads(line);path=dataset/value['path'];text=path.read_text() if path.exists() else None
        identity=value.get('url') or value['path'];content=sha(text) if text is not None else None
        documents.append({**value,'document_id':document_identity(identity,content),
            'document_type':'web','source_identity':identity,'text':text,'content_sha256':content,
            'content_status':'available' if text is not None else 'metadata_only','sections':[],'sources':[]})
    specs['legacy_docs']=(DOCUMENTS,[{k:v for k,v in r.items() if k in DOCUMENTS.names} for r in documents])
    specs['legacy_images']=(IMAGES,[])
    refs={}
    for kind,(schema,records) in specs.items():
        ref,_,_=write_registered_table(dataset,'raw/fixtures/'+kind+'.lance',schema_name='fixture',
              schema_version='v1',schema=schema,rows_factory=lambda records=records:iter(records),fingerprint=kind)
        refs[kind]=ref
    def resolve(dataset,kind,spec=None):return refs[kind],{'kind':kind,'dataset_ref':refs[kind].to_dict()}
    monkeypatch.setattr(lake_inputs,'resolve_source',resolve)


def test_explicit_plan_reads_lance_and_resumes_without_file_dispatch(data,monkeypatch):
    import sqlite3
    from preparation.operaters import inputs as ops
    p,d=data
    write_rows(d/'meta/docs.jsonl',[{'concepts':['同名概念','other'],'path':'pages/a.md'}])
    (d/'meta/concepts.json').write_text('{"concepts":[{"name":"同名概念"},{"name":"other"},{"name":"empty"}]}')
    ingest_sources(d,monkeypatch)
    def forbidden(*a,**kw):raise AssertionError('Forbidden file/SQLite execution')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    run=p/'preparation/runs/explicit';pipeline=run_pipeline
    kwargs=dict(ids=['legacy:同名概念','legacy:other','legacy:empty','missing'],group_size=2,
                project=p,source_scope='collected',model_config={"global_material_audit":True})
    result = pipeline(run,d,**kwargs).take_all()
    assert {r['concept_ref'] for r in result} == {'legacy:同名概念','legacy:other','legacy:empty'}
    docs = rows(run,'documents_processed')
    assert len(docs) == 1 and docs[0]['raw_text'] == '原始材料正文'
    monkeypatch.setattr(ops,'clean_document',forbidden)
    for _ in range(2):assert pipeline(run,d,**kwargs).take_all() == result
    with pytest.raises(ValueError):pipeline(run,d,**{**kwargs,'group_size':3})


@pytest.mark.parametrize('invalid_final',[False,True])
def test_explicit_notebook_article_batches_and_durable_replay(data,monkeypatch,invalid_final):
    """257 raw documents cross the 256-row boundary; one final review sees both groups."""
    import asyncio,re,threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from preparation.tests.fixtures import FakeModel
    from preparation import prompts as prompt_config
    from preparation.operaters.routing import EmbedParagraphBatch
    from preparation.operaters.routing import EncodeImageTextMaterials
    p,d=data;pipeline=run_pipeline;calls=[]
    docs=[]
    for i in range(257):
        path=f'pages/source-{i}.md'
        (d/path).write_text(f'同名概念在条件{i}下呈现独立的结构特征。这些结构的形成需要对应的环境条件，原文同时记录了适用范围和例外。')
        docs.append({'concepts':['同名概念'],'path':path,'title':f'来源{i}','url':f'https://example.test/{i}'})
    write_rows(d/'meta/docs.jsonl',docs)
    ingest_sources(d,monkeypatch)
    monkeypatch.setattr(prompt_config,'validate_local_endpoint',lambda *a:None)
    monkeypatch.setattr(EmbedParagraphBatch,'__call__',lambda self,r:{**r,'items':[{**i,'embedding_title_body':[1.,0.]} for i in r['items']]})
    monkeypatch.setattr(EncodeImageTextMaterials,'__call__',lambda self,r:{'case_id':r['case_id'],'text_windows':[],'image_vectors':[]})
    monkeypatch.setattr('preparation.preparation_pipeline.ArticleTokenBudget', lambda *a, **k: lambda r: 100)
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
    run=p/'preparation/runs/articles'
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
        # Lose derived output tables: the durable Lance call journal replays responses.
        import shutil
        from preparation.operaters.runfiles import stage_uri
        for name in ['final_review_requests','final_review','knowledge_base']:
            uri = stage_uri(run,name)
            shutil.rmtree(uri)
        recovered=pipeline(run,d,**kwargs).take_all()
        assert recovered[0]['knowledge']==result[0]['knowledge']
        assert recovered[0]['status']==result[0]['status'] and len(calls)==7
        from preparation.operaters.runfiles import prompt_store
        assert len(__import__('demiflow.lance.records',fromlist=['LanceRecordStore']).LanceRecordStore(**prompt_store(run,'calls')).keys(prefix='response/')) == 7
    finally:server.shutdown();server.server_close();worker.join()


def test_code_fingerprint_ignores_runtime_string_interning_but_detects_edits():
    from demiflow.execution.artifacts import code_record as _code_record
    def plan():return {'words': ('source', 'source'), 'set': frozenset({1,2})}
    before=_code_record(plan.__code__)
    for _ in range(20):
        plan()
        assert _code_record(plan.__code__)==before
    def changed():return {'words': ('source', 'changed')}
    assert _code_record(changed.__code__)!=before






def test_empty_concept_reaches_final_output_without_a_model_call(data,monkeypatch):
    from preparation.operaters.routing import EncodeImageTextMaterials
    from preparation import prompts as prompt_config
    p,d=data
    write_rows(d/'meta/docs.jsonl',[])
    ingest_sources(d,monkeypatch)
    pipeline=run_pipeline
    monkeypatch.setattr('preparation.preparation_pipeline.ArticleTokenBudget', lambda *a, **k: lambda r: 100)
    monkeypatch.setattr(EncodeImageTextMaterials,'__call__',lambda self,r:{'case_id':r['case_id'],'text_windows':[],'image_vectors':[]})
    monkeypatch.setattr(prompt_config,'validate_local_endpoint',lambda *a:None)
    run=p/'preparation/runs/empty'
    result=pipeline(run,d,ids=['legacy:同名概念'],project=p,through='export',source_scope='collected',
                    model_config={'text_mode':'multimodal','article_mode':True,'max_calls':0,
                                  'text_embedding_model':'fake','image_embedding_model':'fake'}).take_all()
    assert len(result)==1 and result[0]['concept']=='同名概念'
    assert result[0]['status']=='insufficient_materials' and result[0]['knowledge']==[]
    assert result[0]['status_reason']=='no_materials_in_scanned_scope'
    assert not list((run/'preparation/calls').glob('*.request.json'))
