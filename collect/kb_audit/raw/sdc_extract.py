#!/usr/bin/env python3
# 流式抽取 SDC P180 depicts：M-id -> QID（含 qualifier 属性记录，供后续判 prominent）
import sys, json, gzip, time, collections
out = gzip.open("/home/ubuntu/demi/raw/sdc_depicts.tsv.gz", "wt")
n_in = n_hit = 0
qual_freq = collections.Counter()
t0 = time.time()
for line in sys.stdin:
    n_in += 1
    if '"P180"' not in line:
        continue
    t = line.rstrip()
    if t.endswith(","):
        t = t[:-1]
    if not t.startswith("{"):
        continue
    try:
        d = json.loads(t)
    except Exception:
        continue
    mid = d.get("id", "")
    sts = (d.get("statements") or {}).get("P180") or []
    rows = []
    for st in sts:
        try:
            v = st["mainsnak"]["datavalue"]["value"]
            qid = v.get("id") if isinstance(v, dict) else None
        except Exception:
            qid = None
        if not qid or not qid.startswith("Q"):
            continue
        quals = st.get("qualifiers") or {}
        keys = ",".join(sorted(quals.keys()))
        for k in quals:
            qual_freq[k] += 1
        rank = st.get("rank", "normal")
        rows.append(f"{mid}\t{qid}\t{rank}\t{keys}")
    for r in rows:
        out.write(r + "\n")
        n_hit += 1
    if n_in % 200000 == 0:
        out.flush()
        print(f"[{time.strftime('%H:%M:%S')}] in={n_in/1e6:.0f}M hit={n_hit/1e6:.2f}M "
              f"rate={n_in/max(time.time()-t0,1)/1e3:.0f}k/s quals={dict(qual_freq.most_common(5))}", flush=True)
out.close()
print(f"DONE in={n_in} hit={n_hit} quals={dict(qual_freq.most_common(10))}", flush=True)
