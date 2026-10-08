import json,heapq
from pathlib import Path
from collections import Counter
import lance
root=Path('/yzp/zhaozy/yangzepeng/0905/datasets')
master=json.loads(Path('/tmp/taxonomy-design-cases-master.json').read_text()); wanted={r['name'] for r in master}
links={n:set() for n in wanted};usable={n:set() for n in wanted};low={n:set() for n in wanted};samples={n:[] for n in wanted};all_names=set();byth={3:Counter(),11:Counter(),101:Counter(),1001:Counter()}
d=lance.dataset(str(root/'images.lance'),version=16)
for b in d.scanner(columns=['sha256','image_uri','concepts','availability'],batch_size=2048,batch_readahead=1,fragment_readahead=1).to_batches():
 for r in b.to_pylist():
  cc=set(r['concepts'] or []);all_names.update(cc);nc=len(cc)
  for threshold,c in byth.items():
   if nc>=threshold:c['images']+=1;c['relations']+=nc
  for n in cc&wanted:
   sha=r['sha256'];links[n].add(sha)
   if r['image_uri'] and r['availability']=='available':usable[n].add(sha)
   if nc<=2 and r['image_uri'] and r['availability']=='available':
    low[n].add(sha)
    item=(sha,r['image_uri'],nc)
    samples[n].append(item)
    samples[n]=sorted(samples[n])[:3]
result={'image_source':{'uri':str(root/'images.lance'),'version':16},'master_source':{'uri':str(root/'master_concepts.lance'),'version':4},'counts_note':'per-row distinct names; available means stored metadata, not this run pixel validation','distinct_image_concept_names':len(all_names),'fanout_threshold_simulations':{str(k):dict(v) for k,v in byth.items()},'cases':{n:{'all_linked_sha':len(links[n]),'uri_and_available_sha':len(usable[n]),'uri_available_max2names_sha':len(low[n]),'low_fanout_examples':[{'sha256':s,'image_uri':u,'linked_names':c} for s,u,c in samples[n]]} for n in sorted(wanted)}}
# 验证仅在内存中的关系回放，不写表；名称含义是否相同仍由单独证据审核决定。
old=links['Mooncake']; current=links['月饼'];combined=old|current
result['rename_retrieval_replay']={'mapping_assumption':'Mooncake equivalent_to 月饼, conditional on semantic approval','original_Mooncake':len(old),'canonical_name_direct_only':len(current),'canonical_only_misses_original':len(old-current),'historical_name_union':len(combined),'shared_sha':len(old&current),'source_set_preservation_pass':old.issubset(combined) and current.issubset(combined),'same_sha_deduplicated_pass':len(combined)==len(old)+len(current)-len(old&current),'has_not_assigned_image_pass_status':True}
for group in ['Mooncake','月饼','普通翠鸟雄鸟','白虎','雄鸟','指名亚种']:
 print(group,json.dumps(result['cases'][group],ensure_ascii=False),flush=True)
print('REPLAY',json.dumps(result['rename_retrieval_replay'],ensure_ascii=False),flush=True)
print('FANOUT',json.dumps(result['fanout_threshold_simulations'],ensure_ascii=False),flush=True)
Path('/tmp/taxonomy-design-case-images.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
