#!/usr/bin/env python3
# SDC depicts 边 × 概念集 → 第一批挂载统计
import gzip, subprocess, json
concepts = set()
p = subprocess.Popen(["zcat","/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_concepts.jsonl.gz"],stdout=subprocess.PIPE,bufsize=1<<22)
for line in p.stdout:
    try: concepts.add(line.decode().split('"')[3])
    except Exception: pass
p.wait()
n_edges = n_hit = 0
mids = set(); mids_hit = set(); qids = set(); qids_hit = set()
prominent_quals = {}
with gzip.open("/home/ubuntu/demi/raw/sdc_depicts.tsv.gz","rt") as f:
    for line in f:
        mid, qid, rank, quals = line.rstrip("\n").split("\t")
        n_edges += 1; mids.add(mid); qids.add(qid)
        if qid in concepts:
            n_hit += 1; mids_hit.add(mid); qids_hit.add(qid)
        if quals:
            for q in quals.split(","): prominent_quals[q] = prominent_quals.get(q,0)+1
print(f"总边: {n_edges:,} | 命中概念集边: {n_hit:,} ({n_hit*100//n_edges}%)")
print(f"带P180的文件(M-id): {len(mids):,} | 命中文件: {len(mids_hit):,} ({len(mids_hit)*100//len(mids)}%)")
print(f"被depict的QID: {len(qids):,} | 落在概念集: {len(qids_hit):,} ({len(qids_hit)*100//len(qids)}%)")
print(f"概念覆盖率提升: {len(qids_hit):,} / 7,826,266 = {len(qids_hit)*1000//7826266/10}%")
top = sorted(prominent_quals.items(), key=lambda x:-x[1])[:8]
print("qualifier 分布(找prominent):", top)
