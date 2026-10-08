"""共享本地 Qwen 的并发请求隔离；只用模拟权重，不启动 GPU。"""
import base64
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from evaluation.t2i.case_annotation.tests.test_generation_models import service


def data_url(color):
    out=io.BytesIO()
    Image.new('RGB',(4,3),color).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


def test_parallel_text_and_reference_requests_share_model_without_crossing_images(service,monkeypatch):
    arrivals,observed=[],{}
    gate=threading.Lock()
    all_arrived=threading.Event()
    original=service.generate
    def admitted(body,**kwargs):
        with gate:
            arrivals.append(body['prompt'])
            if len(arrivals)==3: all_arrived.set()
        return original(body,**kwargs)
    monkeypatch.setattr(service,'generate',admitted)
    class Pipeline:
        def __call__(self,**kwargs):
            assert all_arrived.wait(5), 'Other arm must submit before first inference finishes'
            refs=kwargs.get('image',[])
            observed[kwargs['prompt']]=[im.getpixel((0,0)) for im in refs]
            assert kwargs['num_inference_steps']==40 and kwargs['output_resolution']==1024
            assert kwargs['generator'].initial_seed()==42
            return SimpleNamespace(images=[Image.new('RGB',(256,256))])
    service.STATE['pipe']=Pipeline()
    client=TestClient(service.app)  # No lifespan: no real weight load.
    requests=[('/v1/images/generations',{'prompt':'  text only\n'}),
              ('/v1/images/edits',{'prompt':'reference A','image':[data_url('red'),data_url('blue')]}),
              ('/v1/images/edits',{'prompt':'reference B','image':[data_url('green')]})]
    def send(item):
        path,body=item
        return client.post(path,json={'model':'Qwen-Image-2.1','size':'256x256',
            'num_inference_steps':40,'output_resolution':1024,'seed':42,**body})
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses=list(pool.map(send,requests))
    assert all(r.status_code==200 for r in responses)
    assert observed=={'  text only\n':[], 'reference A':[(255,0,0),(0,0,255)], 'reference B':[(0,128,0)]}
    assert len(responses[1].json()['usage']['source_sha256s'])==2
    assert len(responses[2].json()['usage']['source_sha256s'])==1


@pytest.mark.parametrize('images',[[],[data_url('red')]*9,['invalid-data-url']])
def test_bad_reference_list_never_falls_back_to_text_only(service,images):
    response=TestClient(service.app).post('/v1/images/edits',json={
        'model':'Qwen-Image-2.1','prompt':'prompt','size':'256x256','image':images})
    assert response.status_code==400 and not service.inference_calls


def test_existing_single_image_json_contract_remains_supported(service):
    response=TestClient(service.app).post('/v1/images/edits',json={
        'model':'Qwen-Image-2.1','prompt':'prompt','size':'256x256','image':data_url('red')})
    assert response.status_code==200
    usage=response.json()['usage']
    assert usage['source_sha256s']==[usage['source_sha256']]
