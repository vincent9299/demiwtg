import copy,json,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from PIL import Image
from .contracts import digest
from .ops.multimodal import *
from .test_source_blocks import sample


def request():
    return {'case_id':'C','batch_id':'B','joint_prompt':{'passages':[{'source_id':'S','text':'仅在甲地使用。'}],'image_ids':['I']},'pixel_images':[],'pixel_roles':[]}

def fact(fid='F',basis='multimodal'):
    return {'fact_id':fid,'statement':'在甲地使用，图中呈红色。','conditions':['仅在甲地'],'exceptions':[],
        'basis':basis,'status':'candidate','reasons':[],
        'evidence':[{'source_id':'S','quote':'仅在甲地使用。'}] if basis!='image' else [],
        'image_evidence':[{'image_id':'I','region':'中心','supports':'呈红色','limitations':'不能证明普遍颜色'}] if basis!='text' else []}

def extracted(fs):
    r=request();r['prompt_result']={'facts':fs,'conflicts':[],'coverage_note':'test'}
    return ApplyJointExtraction()(r)

def test_joint_keeps_text_only_and_visual_knowledge_and_quarantines_bad_quote():
    fs=[fact('A','text'),fact('B','image'),fact('C')];fs[-1]['evidence'][0]['quote']='乙地'
    r=extracted(fs)
    assert len(r['joint_facts'])==2 and len(r['joint_deferred'])==1
    assert r['joint_deferred'][0]['fact']['evidence'][0]['quote']=='乙地'
    r['prompt_result']={'reviews':[{'fact_id':f['fact_id'],'status':'supported','reason':'source matches',
        'image_support':[{'image_id':'I','status':'partial','region':'center','supports':'red','limitations':'one object'}] if f['image_evidence'] else []} for f in r['joint_facts']]}
    v=ApplyJointVerification()(r)
    assert len(v['joint_facts'])==2 and len(v['joint_deferred'])==1
    del r['prompt_result']['reviews'][1]
    assert len(ApplyJointVerification()(r)['joint_deferred'])==2

def test_merge_unions_citations_but_never_promotes_deferred_or_drops_conditions():
    fs=extracted([fact('A','text'),fact('B','text')])['joint_facts'];fs[1]['evidence']=[{'source_id':'S2','quote':'仅在甲地使用。'}]
    row={'joint_facts':fs,'joint_deferred':[],'joint_calls':[{}],'prompt_result':{'duplicate_groups':[[f['fact_id'] for f in fs]],'conflicts':[],'exclusions':[]}}
    out=ApplyJointMerge()(row);assert len(out['knowledge']['facts'])==1
    assert len(out['knowledge']['facts'][0]['evidence'])==2
    assert len(out['knowledge']['facts'][0]['merged_candidates'])==2
    fs[1]['conditions']=['仅在乙地']
    assert len(ApplyJointMerge()(row)['knowledge']['facts'])==2
    row['prompt_result']['conflicts']=[{'fact_ids':[f['fact_id'] for f in fs],'issue':'disputed','needed_evidence':'source'}]
    out=ApplyJointMerge()(row);assert not out['knowledge']['facts'] and len(out['knowledge']['deferred_facts'])==2

def test_images_ignore_metadata_rejection_and_hash_duplicates_keep_links(tmp_path):
    p=tmp_path/'a.png';Image.new('RGB',(32,32),'red').save(p);sha=digest(p.read_bytes())
    m={'kind':'legacy_images','record':{'sha256':sha,'path':str(p),'preannotation':{'description':None}},'provenance':{},'bytes':{'status':'verified_bytes','path':str(p)}}
    row={'cleaned_materials':[m,copy.deepcopy(m)],'identity':{'accepted_material_ids':[]}}
    out=SelectAvailableImages()(row);assert len(out['available_images'])==1 and len(out['image_material_scope']['exact_duplicates'])==1
    p.write_bytes(b'changed')
    import pytest
    with pytest.raises(ValueError,match='Image changed'):pixels(out['available_images'])

def test_native_joint_graph_sends_pixels_and_replays_without_calls(tmp_path,monkeypatch):
    from .multimodal_trial import run_trial
    from .pipeline import DEFAULT
    from .ops import prompt_config
    monkeypatch.setattr(prompt_config,'validate_local_endpoint',lambda *a:None)
    p=tmp_path/'a.png';Image.new('RGB',(32,32),'red').save(p)
    row=sample();m=row['identity_materials'][0];m['kind']='legacy_docs'
    row.update(concept_ref='legacy:芦笙',bundle={'concept_id':'test','request':{'kind':'legacy','value':'芦笙'}},cleaned_materials=[m])
    row['cleaned_materials'].append({'kind':'legacy_images','record':{'sha256':digest(p.read_bytes()),'path':str(p)},'provenance':{},'bytes':{'status':'verified_bytes','path':str(p)}})
    source=tmp_path/'input.jsonl';source.write_text(json.dumps(row)+'\n');calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'data':[{'id':'qwen3.8-27b'}]}).encode())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body)
            content=body['messages'][1]['content']
            text=content if isinstance(content,str) else '\n'.join(x['text'] for x in content if x['type']=='text')
            payload=json.JSONDecoder().raw_decode(text.split('输入数据：\n')[1])[0]
            if 'units' in payload:
                result={'decisions':[{'unit_id':u['unit_id'],'relation':'direct','decision':'selected','reason':'related'} for u in payload['units']]}
            elif 'metadata' in payload:
                result={'images':[{'image_id':i,'relation':'background','concept_relation':'related_activity','decision':'keep','observability':'usable','reason':'related','visible_information':'red','limitations':'one object'} for i in payload['image_ids']]}
            elif 'facts' in payload and 'image_ids' not in payload:
                result={'duplicate_groups':[],'conflicts':[],'exclusions':[]}
            elif 'facts' in payload:
                result={'reviews':[{'fact_id':f['fact_id'],'status':'supported','reason':'matches','image_support':[{'image_id':e['image_id'],'status':'partial','region':'center','supports':'red','limitations':'only object'} for e in f['image_evidence']]} for f in payload['facts']]}
            else:
                f=fact();f['evidence']=[{'source_id':payload['passages'][0]['source_id'],'quote':payload['passages'][0]['text']}];f['image_evidence'][0]['image_id']=payload['image_ids'][0]
                result={'facts':[f],'conflicts':[],'coverage_note':'all'}
            if 'image_ids' in payload:
                assert sum(x['type']=='image_url' for x in content)==len(payload['image_ids'])
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'model':'qwen3.8-27b','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'result':result})}}],'usage':{'prompt_tokens':10,'completion_tokens':10}}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    config={**DEFAULT,'text_mode':'multimodal','base_url':f'http://127.0.0.1:{server.server_port}/v1'}
    try:
        out=list(run_trial(source,tmp_path/'run',config).iter_rows());assert out[0]['knowledge']['facts']
        assert out[0]['image_selection']['supporting_images']
        n=len(calls);assert n==5
        run_trial(source,tmp_path/'run',config);assert len(calls)==n
    finally:server.shutdown();server.server_close();t.join()


def test_failed_image_link_does_not_discard_supported_text():
    row=extracted([fact()]);fid=row['joint_facts'][0]['fact_id']
    row['prompt_result']={'reviews':[{'fact_id':fid,'status':'supported','reason':'Text supports statement; image is unrelated',
        'image_support':[{'image_id':'I','status':'none','region':'whole','supports':'none','limitations':'unrelated'}]}]}
    out=ApplyJointVerification()(row)
    assert len(out['joint_facts'])==1 and out['joint_facts'][0]['basis']=='text'
    assert not out['joint_facts'][0]['image_evidence'] and out['joint_facts'][0]['unsupported_image_links']


def test_scoped_review_withdraws_image_without_losing_text_or_machine_audit():
    import pytest
    from .ops.support_review import ApplySupportReview
    f=fact();pair={'fact_id':'F','image_id':'I','status':'partial','region':'center','supports':'red','limitations':'one object'}
    row={'knowledge':{'facts':[f],'image_evidence':{'status':'machine_reviewed','result':{'images':[{'image_id':'I'}],'support':[pair]}}},
         'images':[{'image_id':'I','material_id':'M'}],'audit':{}}
    review={'reviewer':'Codex','decisions':[{'fact_id':'F','image_id':'I','status':'unobservable','reason':'Cannot establish object identity'}]}
    out=ApplySupportReview(review)(row)
    assert out['knowledge']['facts'][0]['basis']=='text'
    assert out['knowledge']['facts'][0]['evidence']==f['evidence']
    assert out['knowledge']['facts'][0]['disputed_image_evidence']==f['image_evidence']
    assert out['knowledge']['image_evidence']['result']['support'][0]['machine_judgment']==pair
    assert not out['image_selection']['supporting_images']
    assert row['knowledge']['facts'][0]['basis']=='multimodal'
    review['decisions'][0]['fact_id']='unknown'
    with pytest.raises(ValueError,match='unknown support'):ApplySupportReview(review)(row)


def test_image_filter_excludes_namesake_and_holds_uncertain():
    row={'case_id':'C','pixel_roles':[],'image_prompt':{'image_ids':['poster','target','maybe'],'selection_protocol':'image-relevance-v2'}}
    def item(i,cr,r,d):return dict(image_id=i,concept_relation=cr,relation=r,decision=d,observability='usable',reason='pixels',visible_information='visible',limitations='scope')
    row['prompt_result']={'images':[item('poster','namesake','unrelated','exclude'),item('target','target','direct','keep'),item('maybe','uncertain','uncertain','pending')]}
    decisions=ApplyImageSelection()(row)['image_decisions']
    out=SelectRelatedMaterials()({'image_decisions':decisions,'available_images':[{'image_id':i} for i in ['poster','target','maybe']]})
    assert [i['image_id'] for i in out['material_pack']['images']]==['target']
    assert len(out['material_pack']['pending_images'])==len(out['material_pack']['excluded_images'])==1
    row['prompt_result']['images'][0]['decision']='keep'
    assert not ApplyImageSelection()(row)['image_decisions'][0]['protocol_valid']


def test_preprocessing_reuse_requires_same_business_and_complete_checkpoint(tmp_path,monkeypatch):
    import pytest
    from . import notebook_io
    parent=tmp_path/'parent';(parent/'datasets').mkdir(parents=True)
    old={'config':{'ids':['x']},'sources':[],'source_code':{'ops/cleaning.py':'unchanged'}}
    (parent/'dataset_manifest.json').write_text(json.dumps(old))
    source=parent/'datasets/documents_processed.jsonl';source.write_text('{"x":1}\n')
    source.with_suffix('.jsonl.meta.json').write_text(json.dumps({'version':digest(old)}))
    (parent/'datasets/images_processed.jsonl.partial').write_text('incomplete')
    monkeypatch.setattr(notebook_io,'source_code',lambda:{'ops/cleaning.py':'unchanged'})
    run=tmp_path/'new';notebook_io.reuse_preprocessing(parent,run,'v2',{'ids':['x']})
    assert (run/'datasets/documents_processed.jsonl').read_text()==source.read_text()
    assert not (run/'datasets/images_processed.jsonl').exists()
    record=json.loads((run/'preprocessing_reuse.json').read_text())
    assert record['files'][0]['sha256']==digest(source.read_bytes())
    monkeypatch.setattr(notebook_io,'source_code',lambda:{'ops/cleaning.py':'changed'})
    with pytest.raises(ValueError,match='Business code changed'):
        notebook_io.reuse_preprocessing(parent,tmp_path/'bad','v3',{'ids':['x']})


def test_known_singleton_does_not_block_valid_merge_but_overlap_still_does():
    fs=extracted([fact('A','text'),fact('B','text'),fact('C','text')])['joint_facts']
    ids=[f['fact_id'] for f in fs]
    row={'joint_facts':fs,'joint_deferred':[],'joint_calls':[{}],
         'prompt_result':{'duplicate_groups':[[ids[0],ids[1]],[ids[0]],[ids[2]]],'conflicts':[],'exclusions':[]}}
    out=ApplyJointMerge()(row)
    assert len(out['knowledge']['facts'])==2
    assert out['joint_merge_review']['ignored_singleton_groups']==[[ids[0]],[ids[2]]]
    row['prompt_result']['duplicate_groups']=[[ids[0],ids[1]],[ids[0],ids[2]]]
    out=ApplyJointMerge()(row)
    assert not out['joint_merge_review']['protocol_valid']
    assert len(out['knowledge']['deferred_facts'])==3
