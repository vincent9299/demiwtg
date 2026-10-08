"""Read-only title-link and image-file checks for the sizing audit.

Title/alias hits are acquisition leads, not accepted concept identity mappings.
Pixel checks use deterministic stratified samples; dHash only flags candidates.
"""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse
import csv
import hashlib
import json

import lance
from PIL import Image

ROOT=Path('/yzp/zhaozy/yangzepeng/0905')
OUT=Path(__file__).resolve().parent
rows=list(csv.DictReader((OUT/'candidate_coverage.csv').open()))
names={r['concept'] for r in rows}
images=json.loads((OUT/'image_metadata.json').read_text())
def norm(x): return x.strip().replace('_',' ').casefold()
def key(x): return hashlib.sha256(('benchmark-sizing-v1\0'+x).encode()).hexdigest()

direct=defaultdict(set)
expanded=defaultdict(set)
for name in names:
    direct[norm(name)].add(name)
    expanded[norm(name)].add(name)
for batch in lance.dataset(str(ROOT/'datasets/master_concepts.lance'),version=4).scanner(columns=['name','aliases'],batch_size=4096).to_batches():
    for row in batch.to_pylist():
        if row['name'] in names:
            for alias in row['aliases'] or []:
                if alias: expanded[norm(alias)].add(row['name'])
qid_links=[]
for batch in lance.dataset(str(ROOT/'demiwtg/subset/datasets/qid_sub_100k_bucket_v1_concepts.lance'),version=1).scanner(
        columns=['qid','en_title','zh_title','image_sha256s','eligible_images','selected_images'],batch_size=4096).to_batches():
    for row in batch.to_pylist():
        titles={norm(t) for t in [row['en_title'],row['zh_title']] if t}
        hits=set().union(*(expanded[t] for t in titles))
        for name in sorted(hits):
            qid_links.append({**row,'concept':name,'match_kind':'name_title' if any(name in direct[t] for t in titles) else 'alias_title'})
needed_shas={sha for r in qid_links for sha in r['image_sha256s']}
qid_images={}
for batch in lance.dataset(str(ROOT/'demiwtg/subset/datasets/qid_sub_100k_bucket_v1_images.lance'),version=5).scanner(
        columns=['sha256','image_uri','width','height','availability','source','refs_orig_url'],batch_size=4096).to_batches():
    for row in batch.to_pylist():
        if row['sha256'] in needed_shas: qid_images[row['sha256']]=row
existing=defaultdict(set)
for im in images:
    if im['available_metadata']:
        for name in im['candidate_concepts']: existing[name].add(im['sha256'])
for link in qid_links:
    local={s for s in link['image_sha256s'] if s in qid_images and qid_images[s]['availability']=='available' and qid_images[s]['image_uri']}
    link['local_images']=len(local)
    link['new_sha_candidates']=sorted(local-existing[link['concept']])
    link['local_short_side_512']=sum(min(qid_images[s]['width'] or 0,qid_images[s]['height'] or 0)>=512 for s in local)
(OUT/'qid_acquisition_leads.json').write_text(json.dumps(qid_links,ensure_ascii=False,indent=2)+'\n')

eligible=[im for im in images if im['available_metadata'] and im['fanout']<=2]
def path_of(im):
    uri=im['image_uri']; u=urlparse(uri)
    if u.scheme not in {'','file'}: raise ValueError('nonlocal URI')
    return Path(unquote(u.path) if u.scheme else uri)
def stat(im):
    try:
        p=path_of(im); s=p.stat()
        return {'sha256':im['sha256'],'exists':p.is_file(),'actual_bytes':s.st_size,
                'size_matches_metadata':s.st_size==im['byte_size'] if im['byte_size'] is not None else None}
    except Exception as exc: return {'sha256':im['sha256'],'exists':False,'error':str(exc)}
with ThreadPoolExecutor(max_workers=8) as pool:
    file_stats=list(pool.map(stat,eligible))

by_name=defaultdict(list)
for im in eligible:
    if (im['short_side'] or 0)>=512:
        for name in im['candidate_concepts']: by_name[name].append(im)
selected=[]
for root in sorted({r['root'] for r in rows}-{'待明确主体','抽象与文化主题'}):
    root_rows=[r for r in rows if r['root']==root and r['placement_status']=='按当前导航约定归类']
    for lo,hi,label in [(5,19,'5_to_19'),(20,100000,'20_plus')]:
        options=[r for r in root_rows if lo<=len(by_name[r['concept']])<=hi]
        if options:
            chosen=min(options,key=lambda r:key(r['concept']))
            selected.append({'concept':chosen['concept'],'root':root,'stratum':label,
                             'pool_size':len(by_name[chosen['concept']])})
sample={}
for s in selected:
    group=sorted(by_name[s['concept']],key=lambda im:key(im['sha256']))[:60]
    s['sample_shas']=[im['sha256'] for im in group]
    for im in group: sample[im['sha256']]=im
def pixels(im):
    try:
        raw=path_of(im).read_bytes()
        with Image.open(path_of(im)) as opened:
            opened.load(); size=opened.size
            small=opened.convert('RGB').convert('L').resize((9,8),Image.Resampling.LANCZOS)
            vals=list(small.getdata())
            h=sum((vals[y*9+x]>vals[y*9+x+1])<<(y*8+x) for y in range(8) for x in range(8))
        return {'sha256':im['sha256'],'sha_matches':hashlib.sha256(raw).hexdigest()==im['sha256'],
                'decoded':True,'size':size,'metadata_dimensions_match':list(size)==[im['width'],im['height']],
                'dhash':f'{h:016x}'}
    except Exception as exc: return {'sha256':im['sha256'],'decoded':False,'error':str(exc)}
with ThreadPoolExecutor(max_workers=4) as pool:
    pixel_results={r['sha256']:r for r in pool.map(pixels,sample.values())}
for s in selected:
    good=[pixel_results[sha] for sha in s['sample_shas'] if pixel_results[sha]['decoded'] and pixel_results[sha]['sha_matches']]
    parent=list(range(len(good)))
    def find(i):
        while parent[i]!=i: parent[i]=parent[parent[i]]; i=parent[i]
        return i
    pairs=[]
    for i,a in enumerate(good):
        for j in range(i):
            b=good[j]
            if (int(a['dhash'],16)^int(b['dhash'],16)).bit_count()<=4:
                parent[find(i)]=find(j)
                pairs.append([a['sha256'],b['sha256']])
    s['decoded_verified']=len(good)
    s['dhash_candidate_groups']=len({find(i) for i in range(len(good))})
    s['possible_duplicate_pairs']=pairs
summary={'qid_versions':{'concepts':1,'images':5,'master':4},
         'qid_leads':{kind:{'names':len({l['concept'] for l in qid_links if l['match_kind']==kind}),
                            'qids':len({l['qid'] for l in qid_links if l['match_kind']==kind}),
                            'new_unique_sha':len({s for l in qid_links if l['match_kind']==kind for s in l['new_sha_candidates']})}
                      for kind in ['name_title','alias_title']},
         'local_files':{'checked':len(file_stats),'exists':sum(r['exists'] for r in file_stats),
                        'size_mismatch':sum(r.get('size_matches_metadata') is False for r in file_stats)},
         'pixel_sample':{'method':'per root one 5-19 and one >=20 short-side>=512 pool, fixed hash selection, <=60 images/name',
                         'images':len(pixel_results),'decoded':sum(r['decoded'] for r in pixel_results.values()),
                         'sha_matches':sum(r.get('sha_matches',False) for r in pixel_results.values()),
                         'dimension_mismatches':sum(r.get('metadata_dimensions_match') is False for r in pixel_results.values()),
                         'concepts':selected},
         'limitations':['No new semantic image review.','dHash threshold <=4 flags potential near duplicates; not identity proof or full dedup.',
                        'QID name/alias title matches are unreviewed leads, not usable-image claims.','Stratified sample is not a population error-rate estimate.']}
(OUT/'supply_checks.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
(OUT/'pixel_checks.json').write_text(json.dumps(pixel_results,ensure_ascii=False,indent=2)+'\n')
(OUT/'file_checks.json').write_text(json.dumps(file_stats,ensure_ascii=False)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='pixel_sample'},ensure_ascii=False,indent=2),flush=True)
print('pixel_sample',summary['pixel_sample']['images'],'concepts',len(selected),flush=True)
for s in selected: print(s['root'],s['concept'],s['pool_size'],s['decoded_verified'],s['dhash_candidate_groups'],flush=True)
