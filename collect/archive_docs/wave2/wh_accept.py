import gzip, json, random, sys
random.seed(4242)
W2 = "/home/ubuntu/merge_wave2"
WH = "/home/ubuntu/wh_backfill"

def iter_gz(p):
    with gzip.open(p, "rt", errors="replace") as f:
        for l in f: yield l

# ---- 1) 从我发布的 v2(wave2 out,md5=canonical) 抽样:si2 400 / met 60 / wm404 全部 ----
want = {"si2": {}, "meta_refetch": {}, "wm404_recovered": {}}
metfix = {}
for l in open(f"{W2}/in/met_redo_fix.jsonl"):
    o = json.loads(l)
    if o.get("verdict") == "replaced": metfix[str(o["extid"])] = o
buf = {"si2": [], "meta_refetch": [], "wm404_recovered": []}
for l in iter_gz(f"{W2}/out/images.v2.wave2.jsonl.gz"):
    fs = None
    if '"fix_state": "si2"' in l: fs = "si2"
    elif '"fix_state": "meta_refetch"' in l: fs = "meta_refetch"
    elif '"fix_state": "wm404_recovered"' in l: fs = "wm404_recovered"
    if fs: buf[fs].append(l)
CAP = {"si2": 4000, "meta_refetch": 700, "wm404_recovered": 100}
for fs, rows in buf.items():
    sample = random.sample(rows, min(CAP[fs], len(rows)))
    for l in sample:
        o = json.loads(l)
        want[fs][o["sha256"]] = o
print("sampled:", {k: len(v) for k, v in want.items()}, flush=True)

# ---- 2) 死信增件 sha 集 ----
add_sha = set()
for l in open(f"{WH}/deadletter_additions.jsonl"):
    add_sha.add(json.loads(l)["sha256"])
print("additions shas:", len(add_sha), flush=True)

# ---- 3) 流 wh 层:覆盖率 + met 宽高一致 + 增件 sha 残留(应为 0) ----
st = {"si2_hit": 0, "si2_wh": 0, "met_hit": 0, "met_wh_eq": 0, "met_wh_ne": 0,
      "wm404_hit": 0, "wm404_wh": 0, "add_sha_residual": 0, "rows": 0}
met_expect = {}
for sha, o in want["meta_refetch"].items():
    for r in o.get("refs") or []:
        fx = metfix.get(r.get("external_id"))
        if fx: met_expect[sha] = (fx.get("w"), fx.get("h")); break
for l in iter_gz(f"{WH}/images.v2.wh.jsonl.gz"):
    o = json.loads(l); sha = o["sha256"]; st["rows"] += 1
    if sha in add_sha: st["add_sha_residual"] += 1
    if sha in want["si2"]:
        st["si2_hit"] += 1
        if o.get("width"): st["si2_wh"] += 1
    elif sha in want["meta_refetch"]:
        st["met_hit"] += 1
        e = met_expect.get(sha)
        if o.get("width") and e:
            if (o.get("width"), o.get("height")) == e: st["met_wh_eq"] += 1
            else: st["met_wh_ne"] += 1
    elif sha in want["wm404_recovered"]:
        st["wm404_hit"] += 1
        if o.get("width"): st["wm404_wh"] += 1
print(json.dumps(st, ensure_ascii=False), flush=True)
json.dump(st, open("/home/ubuntu/merge_wave2/work/wh_accept.json", "w"))
print("ACCEPT_SCAN_DONE")
