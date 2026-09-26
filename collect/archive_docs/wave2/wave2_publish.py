#!/usr/bin/env python3
# wave2_publish.py — 备份旧权威件 → 版本化发布 Wave2 产物 → 发布后校验
# 纪律: 每步前后 HEAD/校验;canonical key 原子 PUT;绝不删除任何旧件。
import sys, os, json, time, hashlib, gzip
HOME = "/home/ubuntu"
W2 = f"{HOME}/merge_wave2"
IN, WK, OUT = f"{W2}/in", f"{W2}/work", f"{W2}/out"
sys.path.insert(0, f"{HOME}/demiflow_collect")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
B = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"

def md5(p, buf=1 << 24):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(buf)
            if not b: break
            h.update(b)
    return h.hexdigest()

def count_lines(p):
    n = 0
    with gzip.open(p, "rt", errors="replace") as f:
        for _ in f: n += 1
    return n

def step(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ---- 0) 预检: 本地产物齐备 + md5 锚定 ----
local = {
    "main": (f"{OUT}/images.v2.wave2.jsonl.gz", 18657248),
    "dead": (f"{OUT}/deadletter.v2.wave2.jsonl.gz", 8424478),
    "pid_dead": (f"{OUT}/wm404_pid_dead_final.jsonl", None),
    "pid_add": (f"{OUT}/pid_additions.jsonl", None),
}
for k, (p, _) in local.items():
    assert os.path.exists(p) and os.path.getsize(p) > 0, f"missing {p}"
md5s = {k: md5(p) for k, (p, _) in local.items()}
step("local md5s " + json.dumps(md5s))
for k, (p, n) in local.items():
    if n: assert count_lines(p) == n, f"line count mismatch {p}"

# 旧权威件当前状态(发布前快照)
pre = {}
for key in ["images.v2.jsonl.gz", "quarantine/deadletter.v2.jsonl.gz",
            "quarantine/rebuild_report.json"]:
    pre[key] = io.head(B + key)
step("pre-publish HEAD " + json.dumps(pre))

# ---- 1) 备份旧权威件(内容 = 已 md5 锚定的本地 in/ 副本,与 COS 现件字节一致) ----
bk = [("images.v2.jsonl.gz", f"{IN}/images.v2.jsonl.gz", "backup/images.v2.20260923.jsonl.gz"),
      ("quarantine/deadletter.v2.jsonl.gz", f"{IN}/deadletter.v2.jsonl.gz",
       "backup/quarantine.deadletter.v2.20260923.jsonl.gz"),
      ("quarantine/rebuild_report.json", f"{IN}/rebuild_report.json",
       "backup/rebuild_report.20260923.json")]
for _, src, dst in bk:
    if io.head(B + dst) is None:
        step(f"backup -> {dst}")
        assert io.put_multipart(B + dst, src)
    sz = io.head(B + dst)
    assert sz == os.path.getsize(src), f"backup size mismatch {dst}: {sz} vs {os.path.getsize(src)}"
    step(f"backup verified {dst} {sz}")

# ---- 2) 终版 rebuild_report.json ----
rep = json.load(open(f"{OUT}/wave2_report.json"))
rep["wave2_verify"] = {
    "g1": json.load(open(f"{WK}/g1.json")),
    "g2": json.load(open(f"{WK}/g2.json")),
    "g3": json.load(open(f"{WK}/g3.json")),
    "g1_closure": {"refs_gap": 15, "refs_gap_by_union_dedup": 15,
                   "qids_gap": 9, "qids_gap_by_union_dedup": 9,
                   "deadletter_dup_keys_preexisting": 22},
    "v2c_crosscheck": {"si_qids_out": 3906669, "si_edges_v2c": 3906858,
                       "inat_qids_out": 3569593, "inat_edges_v2c": 3582546,
                       "oi_rows_out": 616645, "oi_images_v2c": 616752},
    "md5_local": md5s,
    "backup_keys": [b[2] for b in bk],
    "publish_ts": time.time(),
}
with open(f"{WK}/rebuild_report.final.json", "w") as f:
    json.dump(rep, f, ensure_ascii=False, indent=1)

# ---- 3) 发布新件: 版本化 key + canonical key ----
step("publish main (dated) …")
assert io.put_multipart(B + "images.v2.20260924.jsonl.gz", local["main"][0])
step("publish deadletter (dated) …")
assert io.put_multipart(B + "quarantine.deadletter.v2.20260924.jsonl.gz", local["dead"][0])
step("publish sidecars + report …")
assert io.put_multipart(B + "quarantine/wm404_pid_dead_final.jsonl", local["pid_dead"][0])
assert io.put_multipart(B + "quarantine/pid_additions.jsonl", local["pid_add"][0])
assert io.put_multipart(B + "quarantine/rebuild_report.json", f"{WK}/rebuild_report.final.json")
step("publish main (canonical) …")
assert io.put_multipart(B + "images.v2.jsonl.gz", local["main"][0])
step("publish deadletter (canonical) …")
assert io.put_multipart(B + "quarantine/deadletter.v2.jsonl.gz", local["dead"][0])

# ---- 4) 发布后校验: HEAD + 抽段 GET + 行数 ----
post = {}
for key in ["images.v2.jsonl.gz", "images.v2.20260924.jsonl.gz",
            "quarantine/deadletter.v2.jsonl.gz", "quarantine.deadletter.v2.20260924.jsonl.gz",
            "quarantine/wm404_pid_dead_final.jsonl", "quarantine/pid_additions.jsonl",
            "quarantine/rebuild_report.json",
            "backup/images.v2.20260923.jsonl.gz"]:
    post[key] = io.head(B + key)
step("post HEAD " + json.dumps(post))
exp = {"images.v2.jsonl.gz": os.path.getsize(local["main"][0]),
       "images.v2.20260924.jsonl.gz": os.path.getsize(local["main"][0]),
       "quarantine/deadletter.v2.jsonl.gz": os.path.getsize(local["dead"][0]),
       "quarantine.deadletter.v2.20260924.jsonl.gz": os.path.getsize(local["dead"][0])}
for k, v in exp.items():
    assert post[k] == v, f"size mismatch {k}: {post[k]} vs {v}"
# 行数回读(canonical 主表)
step("re-download canonical for line count …")
p2 = f"{WK}/postcheck.jsonl.gz"
ok = io.download_to(B + "images.v2.jsonl.gz", p2)
n = count_lines(p2)
assert n == 18657248, f"post line count {n}"
assert md5(p2) == md5s["main"], "post md5 mismatch"
os.remove(p2)
step("PUBLISH_DONE " + json.dumps({"rows": n, "md5": md5s["main"]}))
