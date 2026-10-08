"""以隔离CLI替身覆盖标准map_prompt_async、附图、图片交付和回放。"""
import base64
import json
from pathlib import Path
import sys
import pytest
from demiflow import data
from demiflow.objects import LocalObjectStore
from project import resolve_root
from evaluation.t2i.v2.t2i_v2_eval_pipeline import DATASETS, config, run_paired_arm
from evaluation.t2i.v2.operators.request_view import answer_view
from evaluation.t2i.v2.tests.test_pipeline import picture
from evaluation.t2i.v2.tests.test_paired import question
from evaluation.t2i.v2.tests.test_reanswer import store


@pytest.mark.parametrize('mode',['text_only','positive_images'])
def test_codex_generates_durable_image_and_displays_actual_request(tmp_path,mode):
    root=resolve_root(); capture=tmp_path/'actual.json';cli=tmp_path/'codex'
    # 返回与参考图不同的合法PNG，避免把原图复制当作生图。
    import io
    from PIL import Image
    buf=io.BytesIO();Image.new('RGB',(17,18),'blue').save(buf,format='PNG')
    encoded=base64.b64encode(buf.getvalue()).decode()
    cli.write_text(f'''#!{sys.executable}
import sys,json,base64
from pathlib import Path
text=sys.stdin.read()
ctx=json.loads(text.split('CODEX_EXEC_FILE_CONTEXT\\n',1)[1])
Path({str(capture)!r}).write_text(json.dumps({{'text':text,'images':ctx['input_images'],'args':sys.argv}}))
Path(ctx['artifact_directory'],'answer.png').write_bytes(base64.b64decode({encoded!r}))
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({{'result':{{'status':'generated','artifact':'answer.png','reason':''}}}}))
print(json.dumps({{'type':'item.completed','item':{{'type':'image_generation','status':'completed'}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':1,'output_tokens':1}}}}))
''');cli.chmod(0o755)
    model={'backend':'codex_exec','model':'Codex原生生图','answer_mode':mode,
        'concurrency':2,'queue_depth':2,'timeout_s':30,'max_context_chars':60000,
        'codex_exec':{'bin':str(cli),'reasoning_effort':'xhigh','web_search':'disabled','image_generation':True,
            'artifact_store':{'directory':str(root/'objects')},'max_artifact_files':1,'max_artifact_bytes':32*1024*1024}}
    cfg=config(answer_models=[model],parallel_answers=True,max_questions=1)
    q=question();q['test_points'][0]['point']='PRIVATE_TEST_POINT'
    if mode=='positive_images':
        ref=LocalObjectStore(root/'objects').put(picture()).to_dict()
        q['authoring_images_json']=json.dumps([{'kind':'image','number':1,'object_ref':ref}])
    manifest={'config':cfg,'source':store('questions',[q]),'expected_questions':1}
    run=root/DATASETS/'codex'
    state=run_paired_arm(run,manifest,0)
    row=data.read_lance(**state['outputs']['answers']).take(1)[0]
    assert state['complete'],row['reason']
    actual=json.loads(capture.read_text())
    assert len(actual['images'])==(1 if mode=='positive_images' else 0)
    assert 'PRIVATE_TEST_POINT' not in actual['text']
    assert '--image' in actual['args'] if mode=='positive_images' else '--image' not in actual['args']
    view=answer_view(root,'codex',0,cfg['answers'][0],q,row,lambda ref,side: ref['sha256'])
    assert view['available'] and q['instruction'] in view['text']
    assert len(view['images'])==(1 if mode=='positive_images' else 0)
    assert view['parameters']['file_context']['artifact_directory']
    cli.unlink()
    # 不需要CLI可执行文件即可复用已完成的业务答案。
    replay=run_paired_arm(run,manifest,0)
    assert replay['complete']
