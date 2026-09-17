"""Compare sparse routing with the frozen Cartesian baseline, without LLM calls."""
import argparse,json,html,base64,mimetypes
from pathlib import Path
from collections import defaultdict
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code
from .ops.material_routing import route_materials,BuildRoutedJointRequest


def rows(p):return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--packing',choices=['complete_link','nearest_fit'],default='complete_link');a=p.parse_args()
    materials=rows(a.run/'materials.jsonl');vectors={r['case_id']:r for r in rows(a.run/'image_text_embeddings.jsonl')};embeddings={r['source_id']:r for b in rows(a.run/'text_embeddings.jsonl') for r in b['items']}
    baseline=defaultdict(list)
    for r in rows(a.baseline):baseline[r['case_id']].append(r)
    manifest={'source_code':source_code(),'inputs':{str(p):digest(p.read_bytes()) for p in [a.run/'materials.jsonl',a.run/'image_text_embeddings.jsonl',a.run/'text_embeddings.jsonl',a.baseline]},'config':{'packing':a.packing,'text_threshold':.7,'text_chars':2500,'image_thresholds':[.05,.1,.15,.2,.25],'top_groups':[1,2],'default':{'image_threshold':.1,'top_groups':1},'image_target_per_group':4}}
    immutable(a.out/'manifest.json',manifest);version=digest(manifest)
    comparisons=[];defaults=[]
    for m in materials:
        old=baseline[m['case_id']]
        assert {p['source_id'] for r in old for p in r['joint_prompt']['passages']}=={p['source_id'] for p in m['passages']}
        assert {i for r in old for i in r['joint_prompt']['image_ids']}=={i['image_id'] for i in m['images']}
        base={'calls':len(old),'image_presentations':sum(len(r['joint_prompt']['image_ids']) for r in old),'text_chars':sum(len(p['text']) for r in old for p in r['joint_prompt']['passages'])}
        for threshold in [.05,.1,.15,.2,.25]:
            for top in [1,2]:
                r=route_materials(m,vectors[m['case_id']],embeddings,threshold=threshold,top_groups=top,packing=a.packing)
                comparisons.append({'concept':m['concept'],'threshold':threshold,'top_groups':top,'baseline':base,'routed':r['metrics']})
                if threshold==.1 and top==1:defaults.append(r)
    immutable(a.out/'comparison.json',{'scope':'Same full pre-extraction materials. Planned extraction calls, not measured end-to-end speedup; verification/merge costs separate.','comparisons':comparisons})
    immutable(a.out/'routing.json',{'concepts':defaults})
    data=local_data();allrequests=[r for c in defaults for r in c['requests']]
    data.from_iter(lambda:iter(allrequests)).map(BuildRoutedJointRequest()).checkpoint(a.out/'all_requests.jsonl',version=version)
    selected=[]
    for c in defaults:
        eligible=[r for r in c['requests'] if r['kind']=='joint'];selected.append((eligible or c['requests'])[0]['batch_id'])
    fallback=next((r for r in allrequests if r['kind']=='image_only' and r['batch_id'] not in selected),None)
    if fallback is None:fallback=next((r for r in allrequests if r['kind']=='text_only' and r['batch_id'] not in selected),None)
    if fallback:selected.append(fallback['batch_id'])
    data.read_json(str(a.out/'all_requests.jsonl')).filter(lambda r:r['batch_id'] in selected).checkpoint(a.out/'sample_requests.jsonl',version=version)
    immutable(a.out/'sample_scope.json',{'selected_batch_ids':selected,'planned':len(allrequests),'selection':'First joint group per concept plus first image-only (otherwise text-only) fallback; directed engineering check, not random or full extraction.'})
    e=lambda x:html.escape(str(x));out=['<h1>联合提炼前的图文匹配实验</h1><p>同一批原材料：文本按内容分组，SigLIP 2补充图文关联。原文与入选图暂无URL精确匹配，不能据此验收原生配图恢复。所有56段正文与23张入选图片均保留入口。</p><table><tr><th>概念</th><th>原计划调用</th><th>新计划调用</th><th>原/新图像送入次数</th><th>纯文字组/独立图片组</th></tr>']
    for c in comparisons:
        if c['threshold']==.1 and c['top_groups']==1:
            old=c['baseline'];new=c['routed'];out.append('<tr>'+''.join('<td>'+e(v)+'</td>' for v in [c['concept'],old['calls'],new['calls'],str(old['image_presentations'])+' / '+str(new['image_presentations']),str(new['text_only'])+' / '+str(new['image_only'])])+'</tr>')
    out.append('</table><p>上表为计划提炼次数，不是实测整体加速。图片相似度不认证对象身份或知识支持；低匹配与容量溢出的图片另行处理。</p>')
    for m,c in zip(materials,defaults):
        out.append('<h2>'+e(m['concept'])+'</h2>')
        actual={r['batch_id']:r for r in c['requests']}
        for im in m['images']:
            iid=im['image_id'];ranks=sorted([x for x in c['edges'] if x['image_id']==iid],key=lambda x:-x['score']);best=ranks[0]
            raw=Path(im['bytes']['path']).read_bytes();mime=mimetypes.guess_type(im['bytes']['path'])[0] or 'image/jpeg';uri='data:'+mime+';base64,'+base64.b64encode(raw).decode()
            out.append('<section><img src="'+uri+'"><div><h3>'+e(iid)+'</h3><p>最高余弦相似度 '+f"{best['score']:.4f}"+'</p><p>匹配的原文窗口：'+e(best['matched_window'])+'</p>')
            places=[r for r in c['requests'] if any(x['image_id']==iid for x in r['images'])]
            out.append('<p>实际送入：'+e([(r['batch_id'],r['kind']) for r in places])+'</p><details><summary>前三候选</summary>'+e(ranks[:3])+'</details></div></section>')
        out.append('<details><summary>完整正文分组</summary>')
        for r in c['requests']:
            out.append('<h4>'+e(r['batch_id'])+' '+e(r['kind'])+'</h4>')
            for p in r['passages']:out.append('<p>'+e(p['source_id'])+' '+e(p['text'])+'</p>')
        out.append('</details>')
    (a.out/'preview.html').write_text('<!doctype html><meta charset="utf-8"><style>body{max-width:1100px;margin:30px auto;font:16px/1.7 sans-serif}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}section{display:flex;gap:20px;border-bottom:1px solid #ddd;padding:18px 0}section img{max-width:220px;max-height:230px;object-fit:contain}section div{flex:1;min-width:0}details{overflow-wrap:anywhere}</style>'+''.join(out))
    print(json.dumps([c for c in comparisons if c['threshold']==.1 and c['top_groups']==1],ensure_ascii=False,indent=2));print('selected',selected)

if __name__=='__main__':main()
