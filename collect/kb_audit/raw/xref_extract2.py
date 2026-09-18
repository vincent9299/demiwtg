#!/usr/bin/env python3
# 按属性子集抽取（grep 已预滤）
import sys, gzip, time, os, re
PROPS = set(sys.argv[1].split(","))
pat = re.compile(r'^<http://www\.wikidata\.org/entity/(Q\d+)> <http://www\.wikidata\.org/prop/direct/(P\d+)> (.*) \.$')
out = gzip.open(os.path.expanduser("~/xref_part.tsv.gz"), "wt", compresslevel=1)
n = 0; t0 = time.time()
for line in sys.stdin:
    m = pat.match(line)
    if not m or m.group(2) not in PROPS: continue
    val = m.group(3)
    if val.startswith('"'):
        val = val[1:val.index('"', 1)]
    else:
        val = val.strip("<>")
    out.write(f"{m.group(1)}\t{m.group(2)}\t{val}\n"); n += 1
    if n % 2000000 == 0:
        out.flush(); print(f"[{time.strftime('%H:%M:%S')}] {n/1e6:.0f}M rows", flush=True)
out.close()
print(f"DONE rows={n}", flush=True)
