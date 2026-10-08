from pathlib import Path
from collections import Counter
import heapq,json,time
import lance
import pyarrow.compute as pc
root=Path('/yzp/zhaozy/yangzepeng/0905/datasets')
out={}
def save():
 Path('/tmp/taxonomy-design-profile-20260930.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
t=time.time()
d=lance.dataset(str(root/'master_concepts.lance'),version=4)
names=set();alias_owners=Counter();roots=Counter();paths=Counter();depths=Counter();pcounts=Counter();counts=Counter();maxpaths=[]
for b in d.scanner(columns=['name','aliases','carriers','taxonomy'],batch_size=4096,batch_readahead=1,fragment_readahead=1).to_batches():
 for r in b.to_pylist():
  n=r['name']; counts['rows']+=1
  if n in names: counts['duplicate_names']+=1
  names.add(n)
  counts['name_has_edge_whitespace']+=int(n!=n.strip())
  counts['empty_names']+=int(not n.strip())
  a=r['aliases'] or [];counts['alias_entries']+=len(a);counts['rows_with_aliases']+=int(bool(a))
  alias_owners.update(set(x for x in a if x))
  counts['self_alias_rows']+=int(n in a)
  counts['carrier_entries']+=len(r['carriers'] or [])
  counts['rows_with_carriers']+=int(bool(r['carriers']))
  try: pp=json.loads(r['taxonomy'])
  except Exception: counts['invalid_taxonomy']+=1;continue
  if not isinstance(pp,list) or any(not isinstance(p,str) for p in pp): counts['invalid_taxonomy']+=1;continue
  pcounts[len(pp)]+=1; rr=set()
  for p in pp:
   parts=[s.strip() for s in p.split('/')]
   paths[p]+=1;depths[len(parts)]+=1;rr.add(parts[0])
   counts['paths_with_empty_segment']+=int(any(not s for s in parts))
  roots.update(rr)
  counts['cross_root_rows']+=int(len(rr)>1)
  item=(len(pp),n)
  if len(maxpaths)<6:heapq.heappush(maxpaths,item)
  elif item>maxpaths[0]:heapq.heapreplace(maxpaths,item)
out['master']={'uri':str(root/'master_concepts.lance'),'version':4,'counts':dict(counts),'distinct_path_strings':len(paths),'path_depth_by_occurrence':dict(sorted(depths.items())),'path_count_per_concept':dict(sorted(pcounts.items())),'roots_per_concept_deduplicated':roots.most_common(),'distinct_alias_strings':len(alias_owners),'alias_strings_used_by_multiple_rows':sum(v>1 for v in alias_owners.values()),'alias_strings_equal_to_a_master_name':sum(a in names for a in alias_owners),'largest_path_counts':sorted(maxpaths,reverse=True),'elapsed_seconds':round(time.time()-t,2)}
save();print('MASTER',json.dumps(out['master'],ensure_ascii=False),flush=True)

t=time.time();d=lance.dataset(str(root/'images.lance'),version=16)
st=Counter();hist=Counter();avail=Counter();largest=[]
for b in d.scanner(columns=['sha256','concepts','published_concepts','image_uri','availability'],batch_size=2048,batch_readahead=1,fragment_readahead=1).to_batches():
 lens=pc.fill_null(pc.list_value_length(b['concepts']),0).to_pylist()
 published=pc.fill_null(pc.list_value_length(b['published_concepts']),0).to_pylist()
 shas=b['sha256'].to_pylist();uris=b['image_uri'].to_pylist();av=b['availability'].to_pylist()
 for sha,c,p,uri,a in zip(shas,lens,published,uris,av):
  st['rows']+=1;st['concept_array_entries']+=c;st['published_concept_array_entries']+=p
  st['rows_with_uri']+=int(bool(uri and uri.strip()));st['rows_with_published_concepts']+=int(p>0)
  avail[str(a)]+=1
  bucket='0' if c==0 else '1' if c==1 else '2' if c==2 else '3-10' if c<=10 else '11-100' if c<=100 else '101-1000' if c<=1000 else '1001+'
  hist[bucket]+=1
  if c>=1000:st['entries_from_rows_ge1000']+=c;st['rows_ge1000']+=1
  if len(largest)<5:heapq.heappush(largest,(c,sha))
  elif (c,sha)>largest[0]:heapq.heapreplace(largest,(c,sha))
out['images']={'uri':str(root/'images.lance'),'version':16,'counts':dict(st),'concept_array_length_histogram':dict(hist),'availability':dict(avail),'largest_concept_arrays':sorted(largest,reverse=True),'count_note':'array element counts, not uniqueness-checked relations; no pixels read','elapsed_seconds':round(time.time()-t,2)}
save();print('IMAGES',json.dumps(out['images'],ensure_ascii=False),flush=True)

t=time.time();d=lance.dataset(str(root/'articles.lance'),version=8)
c=Counter();names_article=set();rs=Counter();kinds=Counter()
for b in d.scanner(columns=['concept','review_status','article_kind'],batch_size=4096,batch_readahead=1,fragment_readahead=1).to_batches():
 for r in b.to_pylist():
  c['rows']+=1; names_article.add(r['concept']);rs[str(r['review_status'])]+=1;kinds[str(r['article_kind'])]+=1
out['articles']={'version':8,'rows':c['rows'],'distinct_concept_names':len(names_article),'names_in_master':len(names_article & names),'review_status':dict(rs),'article_kind':dict(kinds),'elapsed_seconds':round(time.time()-t,2)}
save();print('ARTICLES',json.dumps(out['articles'],ensure_ascii=False),flush=True)
