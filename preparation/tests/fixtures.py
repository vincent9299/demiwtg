import gzip, hashlib, json
from pathlib import Path
import pytest
from PIL import Image

def write_rows(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'wt',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')


@pytest.fixture
def data(tmp_path):
    d=tmp_path/'datasets/demiwtg';meta=d/'meta';meta.mkdir(parents=True)
    (meta/'concepts.json').write_text(json.dumps({'concepts':[{'name':'同名概念','taxonomy':[]}]}))
    (meta/'taxonomy.json').write_text(json.dumps({'tree':{'children':[]}}))
    write_rows(meta/'qid_concepts.fat.jsonl.gz',[{'qid':'Q1','en':{'page_id':10,'title':'Example'}}])
    write_rows(d/'corpus/pages-en-part1.jsonl.gz',[
      {'qid':None,'lang':'en','page_id':10,'revision_id':7,'sections':[{'text':'source text'}]},
      {'qid':None,'lang':'zh','page_id':10,'revision_id':8,'sections':[{'text':'wrong language'}]}])
    write_rows(meta/'docs.jsonl',[{'concepts':['同名概念'],'path':'pages/a.md','url':'https://example.org/a'}])
    (d/'pages').mkdir();(d/'pages/a.md').write_text('原始材料正文')
    im=d/'img.png';Image.new('RGB',(8,8),'white').save(im);raw=im.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    path=f'blobs/{sha[:2]}/{sha}.png';(d/path).parent.mkdir(parents=True);im.rename(d/path)
    write_rows(meta/'qid_images.jsonl.gz',[{'qid':'Q1','path':path,'sha256':sha}, {'qid':'Q1','path':'blobs/missing.png','sha256':'0'*64}])
    write_rows(meta/'images.jsonl',[])
    return tmp_path,d


class FakeModel:
    def __init__(self,run,config):self.calls=0;self.reused=0
    async def aclose(self):pass
    async def json(self,stage,messages):
        self.calls+=1
        data=json.loads(messages[1]['content'].split('输入数据：\n')[1])
        if stage=='identity':
            ambiguous=data['request']['value']=='ambiguous'
            result={'status':'ambiguous' if ambiguous else 'resolved','target_label':'示例概念','reason':'test source identities',
                    'accepted_material_ids':[] if ambiguous else [m['material_id'] for m in data['materials']],
                    'rejected_materials':[{'material_id':m['material_id'],'reason':'ambiguous'} for m in data['materials']] if ambiguous else [],'identity_groups':[]}
            result['material_reviews']=[{'material_id':m['material_id'],'relation':'uncertain' if ambiguous else 'same_identity',
                'basis':'metadata' if 'images' in m['kind'] else 'text','quote':(m.get('text_preview') or m.get('title') or m.get('caption') or '')[:30],
                'reason':'fixture source association'} for m in data['materials']]
        else:
            source=data['passages'][0]
            result={'facts':[{'fact_id':'F1','statement':'A source-supported statement','conditions':[],'exceptions':[],
                             'evidence':[{'source_id':source['source_id'],'quote':source['text'][:40]}]}],
                    'unresolved_conflicts':[],'coverage_note':'fixture scope','changes':[]}
        return result,{'usage':{},'model':'fake'}
