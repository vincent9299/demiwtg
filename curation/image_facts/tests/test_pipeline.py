"""验证错图绑定、关系完整性，以及真实 Dataset/HTTP/日志的最小闭环。"""
import copy
import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import lance
import pyarrow as pa
import pytest
import yaml
from PIL import Image
from demiflow import data
from curation.image_facts import image_facts_pipeline as pipeline
from curation.image_facts.operators import rows


def annotation(number=1):
    return {'image_number':number,'composition':'单个主要对象','objects':[{'object_id':'a','name':'方块',
        'location':'中央','visible_features':'红色','count':1}], 'relations':[], 'framing':'完整主体为主',
        'view_tags':['无法确认'],'observability_issues':[],
        'text':{'presence':'未观察到','kind':None,'content':'','location':'','detail':''},
        'watermark':{'presence':'未观察到','location':'','detail':''},'caption':'中央是红色方块。'}


def test_duplicate_or_missing_image_numbers_fail_without_rebinding():
    row={'items':[{'sha256':str(i),'image_number':i,'status':'ready','error':''} for i in (1,2)],
         'model_key':'gpt61','model':'mock','batch_id':'gpt61:0','status':'ready',
         'result':{'annotations':[annotation(1),annotation(1)]}}
    result=rows.finish_batch(row)
    assert result['ok_count']==0
    assert [r['sha256'] for r in result['labels']]==['1','2']
    row['result']['annotations']=[annotation(1)]
    assert rows.finish_batch(row)['ok_count']==0


def test_unknown_relation_target_is_technical_failure():
    a=annotation();a['relations']=[{'subject_id':'a','object_id':'missing','type':'位于内部','evidence':'x'}]
    assert 'unknown object_id' in rows.check_annotation(a)


def test_dataset_http_roundtrip_preserves_scope_and_independence(tmp_path,monkeypatch):
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT',str(tmp_path))
    monkeypatch.setenv('IMAGE_FACTS_TEST_KEY','placeholder')
    from dataclasses import replace
    from demiflow.execution import native_resources
    original=native_resources.dataset_session
    def limited(executor,directory,**kwargs):
        session=original(executor,directory,**kwargs)
        session.options=replace(session.options,memory_bytes=128*1024**2,max_rss_bytes=2*1024**3,threads=1,partitions=1,admission_timeout_s=60)
        session.resources=tmp_path/'native-resources';session.max_concurrent=1
        return session
    monkeypatch.setattr(native_resources,'dataset_session',limited)
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            images=[p for message in body['messages'] for p in message['content'] if isinstance(p,dict) and p.get('type')=='image_url']
            answer={'result':{'annotations':[annotation(i+1) for i in range(len(images))]}}
            payload=json.dumps({'id':'test','model':'mock','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(answer)}}],
                                'usage':{'prompt_tokens':5,'completion_tokens':7}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        pack=yaml.safe_load((pipeline.SOURCE/'prompts/tasks.yaml').read_text())
        for prompt in pack['prompts'].values():
            prompt['model'].update(base_url=f'http://127.0.0.1:{server.server_port}/v1',api_key_env='IMAGE_FACTS_TEST_KEY')
        prompt_path=tmp_path/'tasks.yaml';prompt_path.write_text(yaml.safe_dump(pack,allow_unicode=True))
        pictures=[]
        for n in range(2):
            buffer=io.BytesIO();Image.new('RGB',(20+n,30),'red').save(buffer,format='PNG');raw=buffer.getvalue()
            path=tmp_path/f'{n}.png';path.write_bytes(raw)
            pictures.append({'image_number':n+1,'positive_image':{'uri':path.as_uri(),'sha256':hashlib.sha256(raw).hexdigest()},'review_sources':[]})
        schema=pa.schema([('concept',pa.string()),('concept_id',pa.string()),('selection_rank',pa.int64()),('positive_images',pa.list_(pa.struct([
            ('image_number',pa.int64()),('positive_image',pa.struct([('uri',pa.string()),('sha256',pa.string())])),
            ('review_sources',pa.list_(pa.struct([('uri',pa.string()),('version',pa.int64())])))])))])
        path=tmp_path/'input.lance'
        data.from_arrow(pa.Table.from_pylist([{'concept':'禁止泄露的概念名','concept_id':'test','selection_rank':1,'positive_images':pictures}],schema=schema)).write_lance(str(path),schema=schema)
        options=pipeline.config(run='mock',source={'uri':str(path),'version':1},models=['gpt61','luna'],prompt_config=str(prompt_path),
            prompt_options={k:{'stream':False,'timeout_s':10} for k in ('gpt61','luna')})
        prepared=pipeline.run_pipeline(options,prepare_only=True)
        assert prepared['image_count']==2 and len(requests)==0
        first=pipeline.run_pipeline(options,execute_models=['gpt61'])
        assert not first['complete'] and len(requests)==1
        with pytest.raises(ValueError,match='stage not committed'):
            pipeline.run_pipeline(options,finalize_only=True)
        second=pipeline.run_pipeline(options,execute_models=['luna'])
        assert not second['complete'] and len(requests)==2
        outcome=pipeline.run_pipeline(options,finalize_only=True)
        assert outcome['complete']
        summary=lance.dataset(**outcome['summary']).to_table().to_pylist()[0]
        outputs=json.loads(summary['outputs_json'])
        labels=lance.dataset(**outputs['labels_gpt61']).to_table().to_pylist()
        assert len(labels)==2 and all(r['status']=='ok' for r in labels)
        assert len(requests)==2 and all('禁止泄露的概念名' not in json.dumps(r,ensure_ascii=False) for r in requests)
        assert json.loads(summary['models_json'])[0]['input_tokens']==5
        comparison_rows=lance.dataset(**outputs['comparisons']).to_table().to_pylist()
        assert len(comparison_rows)==82
        assert all(row['tag'] for row in comparison_rows)
        expanded=lance.dataset(**outputs['label_fields_gpt61']).to_table().to_pylist()
        assert expanded[0]['objects'][0]['name']=='方块'
        assert expanded[0]['watermark_location']==''
        assert len(requests)==2
        # 原调用复用不会再次请求 HTTP。
        assert pipeline.run_pipeline(options)['complete']
        assert len(requests)==2
    finally:
        server.shutdown();server.server_close();thread.join()


def test_one_bad_annotation_keeps_other_images_without_another_model_call(monkeypatch):
    from demiflow.operator_llm import load_prompt_pack
    pack=load_prompt_pack(pipeline.SOURCE/'prompts/tasks.yaml')
    schema=dict(pack.prompt_definitions['gpt61'].response_schema['properties']['result']['properties']['annotations']['items'])
    good=annotation(1);bad=annotation(2);bad['observability_issues']=[{'type':'噪点','affected_area':'全图','detail':'细节无法确认'}]
    monkeypatch.setattr(rows,'read_call',lambda ref:{'status_code':200,'body':{'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'result':{'annotations':[good,bad]}})}}]}})
    row={'items':[{'sha256':str(i),'image_number':i,'status':'ready','error':''} for i in (1,2)],
         'model_key':'gpt61','model':'mock','batch_id':'gpt61:0','status':'ready',
         'error':{'category':'invalid_response','detail':'invalid enum','call':{'response_ref':{'kind':'response'}}}}
    result=rows.finish_batch(row,annotation_schema=schema)
    assert result['ok_count']==1
    assert [r['status'] for r in result['labels']]==['ok','failed']
    assert json.loads(result['labels'][1]['annotation_json'])==bad
    assert 'invalid_response' in result['error']


def test_tag_comparison_does_not_bundle_views_or_score_missing_as_negative():
    reference=annotation();reference['view_tags']=['正面','侧面']
    candidate=annotation();candidate['view_tags']=['正面','俯视']
    grouped={'sha256':'image','models':{
        'gpt61':{'status':'ok','annotation_json':json.dumps(reference)},
        'qwen4b':{'status':'ok','annotation_json':json.dumps(candidate)},
        'luna':{'status':'failed','annotation_json':'null'}}}
    compared=list(rows.compare(grouped,reference_key='gpt61',tags={'view_tags':['正面','侧面','俯视','背面']}))
    q={r['tag']:r for r in compared if r['model_key']=='qwen4b'}
    assert q['正面']['equal'] and q['正面']['model_selected']
    assert q['侧面']['reference_selected'] and not q['侧面']['model_selected']
    assert q['俯视']['model_selected'] and not q['俯视']['reference_selected']
    assert all(r['model_selected'] is None and r['equal'] is None for r in compared if r['model_key']=='luna')
