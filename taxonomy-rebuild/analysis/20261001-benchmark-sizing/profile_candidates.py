"""Read-only sizing audit of the frozen 3,315-name pool; not a selection pipeline.

Reads fixed Lance versions and saves analysis evidence alongside this script.
Image/name association and table publication are not new semantic verification.
Fanout <= 2/10 are sensitivity scenarios, not approved filtering policies.
"""
from collections import Counter
from pathlib import Path
import csv
import hashlib
import json
import time

import lance
import pyarrow as pa
import pyarrow.compute as pc

ROOT = Path('/yzp/zhaozy/yangzepeng/0905')
OUT = Path(__file__).resolve().parent
DECISIONS = ROOT / 'demiwtg/taxonomy-rebuild/combined-decisions.json'
decisions = json.loads(DECISIONS.read_text())
names = {r['concept'] for r in decisions}
arrow_names = pa.array(sorted(names))
metrics = ['associated', 'available', 'available_fanout_le2',
           'available_fanout_le10', 'available_fanout_le2_512',
           'available_fanout_le2_1024', 'available_fanout_le2_dimensions_unknown',
           'published_available', 'published_available_fanout_le2',
           'articles', 'reviewed_articles']
counts = {n: Counter({m: 0 for m in metrics}) for n in names}
matched = []
global_counts = Counter()
start = time.time()
ds = lance.dataset(str(ROOT/'datasets/images.lance'), version=16)
columns = ['sha256', 'concepts', 'published_concepts', 'image_uri', 'availability',
           'width', 'height', 'byte_size', 'generation_origin']
for batch in ds.scanner(columns=columns, batch_size=4096,
                       batch_readahead=1, fragment_readahead=1).to_batches():
    flat = pc.list_flatten(batch['concepts'])
    owners = pc.list_parent_indices(batch['concepts'])
    indices = pc.unique(pc.filter(owners, pc.is_in(flat, value_set=arrow_names)))
    global_counts['scanned_image_rows'] += len(batch)
    for row in batch.take(indices).to_pylist():
        all_concepts = set(row.pop('concepts') or [])
        targets = sorted(all_concepts & names)
        published = set(row.pop('published_concepts') or []) & names
        row['candidate_concepts'] = targets
        row['published_candidate_concepts'] = sorted(published)
        row['fanout'] = len(all_concepts)
        available = row['availability'] == 'available' and bool(row['image_uri'])
        row['available_metadata'] = available
        short = min(row['width'], row['height']) if row['width'] and row['height'] else None
        row['short_side'] = short
        matched.append(row)
        global_counts['matched_unique_image_rows'] += 1
        global_counts['matched_available_image_rows'] += available
        global_counts['matched_available_fanout_le2_image_rows'] += available and row['fanout'] <= 2
        global_counts['known_generated_image_rows'] += row['generation_origin'] in {'generated','synthetic','ai_generated'}
        for n in targets:
            c = counts[n]
            c['associated'] += 1
            c['available'] += available
            c['available_fanout_le2'] += available and row['fanout'] <= 2
            c['available_fanout_le10'] += available and row['fanout'] <= 10
            c['available_fanout_le2_512'] += available and row['fanout'] <= 2 and short is not None and short >= 512
            c['available_fanout_le2_1024'] += available and row['fanout'] <= 2 and short is not None and short >= 1024
            c['available_fanout_le2_dimensions_unknown'] += available and row['fanout'] <= 2 and short is None
            c['published_available'] += available and n in published
            c['published_available_fanout_le2'] += available and n in published and row['fanout'] <= 2
    if global_counts['scanned_image_rows'] % 262144 < 4096:
        print('scanned', global_counts['scanned_image_rows'], 'matched', len(matched), flush=True)

articles = lance.dataset(str(ROOT/'datasets/articles.lance'), version=8)
for batch in articles.scanner(columns=['concept','review_status'], batch_size=4096).to_batches():
    for r in batch.filter(pc.is_in(batch['concept'], value_set=arrow_names)).to_pylist():
        counts[r['concept']]['articles'] += 1
        counts[r['concept']]['reviewed_articles'] += r['review_status'] == 'reviewed'

rows = []
for d in decisions:
    rows.append({'concept': d['concept'], 'id': d['id'], 'root': d['path'][0],
                 'path': ' / '.join(d['path']), 'placement_status': d['placement_status'],
                 'name_status': d['name_audit']['status'],
                 'source_groups': '/'.join(map(str,sorted({s['group'] for s in d['sources']}))),
                 **counts[d['concept']]})

thresholds = [1,3,5,8,10,12,15,20,25,30,50]
def summarize(items):
    return {'names': len(items),
            'l2_paths': len({tuple(r['path'].split(' / ')[:2]) for r in items}),
            'l3_paths': len({r['path'] for r in items}),
            'thresholds': {m: {str(k): sum(r[m] >= k for r in items) for k in thresholds}
                           for m in metrics},
            'totals': {m:sum(r[m] for r in items) for m in metrics},
            'histograms': {m:dict(sorted(Counter(r[m] for r in items).items())) for m in metrics}}

roots = sorted({r['root'] for r in rows})
summary = {'date_local': '2026-10-01 Asia/Shanghai',
           'sources': {'images': {'uri':str(ROOT/'datasets/images.lance'),'version':16},
                       'articles': {'uri':str(ROOT/'datasets/articles.lance'),'version':8},
                       'decisions': {'path':str(DECISIONS),'sha256':hashlib.sha256(DECISIONS.read_bytes()).hexdigest()}},
           'definitions': {'available':'table says available and image_uri nonempty; pixels not verified here',
                           'fanout':'number of unique nonempty/recorded associated names; <=2 and <=10 are sensitivity scenarios',
                           'published':'name occurs in published_concepts; no new review or freshness claim',
                           'associated':'exact original name matching; alias and QID candidate links not merged',
                           'dedup':'SHA-level only; near-duplicate and photographer/session clustering not done'},
           'global_counts':dict(global_counts), 'all':summarize(rows),
           'by_root':{root:summarize([r for r in rows if r['root']==root]) for root in roots},
           'navigation_ready':summarize([r for r in rows if r['placement_status']=='按当前导航约定归类']),
           'elapsed_seconds':round(time.time()-start,2)}
OUT.mkdir(parents=True,exist_ok=True)
with (OUT/'candidate_coverage.csv').open('w', newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
(OUT/'image_metadata.json').write_text(json.dumps(matched,ensure_ascii=False)+'\n')
print(json.dumps({'global':dict(global_counts),'thresholds':summary['all']['thresholds'],
                  'elapsed':summary['elapsed_seconds']},ensure_ascii=False,indent=2),flush=True)
