#!/usr/bin/env python3
# truthy NT 流式抽桥属性 -> concept_xref.tsv（仅保留概念集内 QID）
import sys, re, gzip, time
WANT = {"P225","P3151","P846","P685","P662","P235","P245","P1014","P1667","P244",
        "P646","P8814","P594","P352","P1256","P2581","P18","P935"}
concepts = set()
import subprocess
p = subprocess.Popen(["zcat","/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1<<22)
for line in p.stdout:
    try: concepts.add(line.decode().split('"')[3])
    except Exception: pass
p.wait()
print(f"concepts: {len(concepts):,}", flush=True)
pat = re.compile(r'<http://www\.wikidata\.org/prop/direct/(P\d+)> <([^>]+)>')
out = gzip.open("/tmp/concept_xref.tsv.gz","wt")
n=hit=kept=0; t0=time.time()
for line in sys.stdin:
    n+=1
    if "prop/direct" not in line: continue
    m = pat.search(line)
    if not m or m.group(1) not in WANT: continue
    hit+=1
    qid = line.split('> <http://www.wikidata.org/prop/direct/',1)[0].lstrip('<')
    out.write(f"{qid}\t{m.group(1)}\t{m.group(2)}\n"); kept+=1
    if n % 50000000 == 0:
        out.flush(); print(f"[{time.strftime('%H:%M:%S')}] {n/1e6:.0f}M lines, kept {kept/1e6:.2f}M, {n/max(time.time()-t0,1)/1e3:.0f}k/s", flush=True)
out.close()
print(f"DONE lines={n} hit={hit} kept={kept}", flush=True)
