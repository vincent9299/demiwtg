import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import httpx
from PIL import Image
from curation import image_preannotate as m

DESCRIPTION={'caption':'白色背景上的红色方块。','representation':'illustration','view_tags':['front'],'objects':[{'name':'方块','location':'中央','visible_features':'红色'}],'text_regions':[],'observability_issues':[],'uncertainties':[]}

class PreannotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.dataset=self.root/'dataset';self.dataset.mkdir()
        p=self.dataset/'image.png';Image.new('RGB',(10,10),'red').save(p);self.sha=hashlib.sha256(p.read_bytes()).hexdigest()
        self.input=self.root/'images.jsonl'
        self.input.write_text('\n'.join(json.dumps({'sha256':self.sha,'path':'image.png','instances':[n]}) for n in ['红色方块','红色方块','蓝色方块']))
        self.args=argparse.Namespace(run=self.root/'run',input=self.input,dataset=self.dataset,model='qwen3.8-27b',base_url='http://127.0.0.1:8000/v1',max_edge=1536,max_tokens=2000,concurrency=2,limit=0,max_attempts=3,timeout=2)
        with mock.patch('builtins.print'):m.prepare(self.args)

    def test_dedup_and_restart_preserve_completed_stages(self):
        requests=[]
        def handler(req):
            body=json.loads(req.content);requests.append(body)
            text=json.loads(body['messages'][1]['content'][1]['text'])
            if 'concepts' not in text:
                self.assertNotIn('红色方块',json.dumps(body,ensure_ascii=False))
                result=DESCRIPTION
            else:result={'matches':[{'name':n,'status':'uncertain','reason':'仅凭画面不作身份认证'} for n in text['concepts']]}
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})
        factory=httpx.AsyncClient
        with mock.patch.object(httpx,'AsyncClient',side_effect=lambda **kw:factory(transport=httpx.MockTransport(handler),**kw)),mock.patch('builtins.print'):
            asyncio.run(m.run_async(self.args));asyncio.run(m.run_async(self.args))
        self.assertEqual(len(requests),2)
        st=m.stats(self.args.run);self.assertEqual(st['images'],{'done':1});self.assertEqual(st['concept_pairs_done'],2)

    def test_bad_match_retains_description_for_retry(self):
        calls=[]
        def handler(req):
            body=json.loads(req.content);payload=json.loads(body['messages'][1]['content'][1]['text']);calls.append(payload)
            result={'matches':[]} if 'concepts' in payload else DESCRIPTION
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})
        factory=httpx.AsyncClient
        with mock.patch.object(httpx,'AsyncClient',side_effect=lambda **kw:factory(transport=httpx.MockTransport(handler),**kw)),mock.patch('builtins.print'):
            asyncio.run(m.run_async(self.args))
        self.assertEqual(sum('concepts' not in p for p in calls),1)
        self.assertEqual(m.stats(self.args.run)['images'],{'error':1})
        self.assertEqual(m.stats(self.args.run)['concept_pairs_done'],0)

    def test_paid_endpoint_and_protocol_change_blocked(self):
        self.args.base_url='http://127.0.0.1:4001/v1'
        with self.assertRaises(ValueError):m.configure(self.args)
        self.args.base_url='http://127.0.0.1:8000/v1';self.args.max_edge=768
        with self.assertRaises(ValueError):m.configure(self.args)

    def test_modified_image_rejected_before_inference(self):
        (self.dataset/'image.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):m.encode(self.dataset,'image.png',self.sha,100)

    def test_reuse_does_not_approve_unseen_concepts(self):
        import copy
        source=m.connect(self.args.run)
        source.execute("UPDATE images SET description=?,status='done'",(json.dumps(DESCRIPTION),))
        source.execute("DELETE FROM concepts WHERE name='蓝色方块'")
        match={'name':'红色方块','status':'consistent','reason':'可见红色方块'}
        source.execute('UPDATE concepts SET result=?',(json.dumps(match),));source.commit();source.close()
        target=copy.copy(self.args);target.run=self.root/'target';target.from_run=self.args.run
        with mock.patch('builtins.print'):
            m.prepare(target);m.reuse(target)
        self.assertEqual(m.stats(target.run)['images'],{'pending':1})
        self.assertEqual(m.stats(target.run)['concept_pairs_done'],1)
        self.assertEqual(m.stats(target.run)['descriptions'],1)

    def test_missing_and_invalid_images_do_not_call_model(self):
        (self.dataset/'image.png').unlink()
        with mock.patch('builtins.print'):asyncio.run(m.run_async(self.args))
        self.assertEqual(m.stats(self.args.run)['images'],{'missing':1})
        self.assertEqual(m.stats(self.args.run)['api_calls'],0)

    def test_stop_file_dispatches_nothing(self):
        (self.args.run/'STOP').touch()
        with mock.patch('builtins.print'):asyncio.run(m.run_async(self.args))
        self.assertEqual(m.stats(self.args.run)['api_calls'],0)

if __name__=='__main__':unittest.main()
