#!/usr/bin/env python3
# wave2_verify.py — Wave2 产物的三道验证门
# G1 守恒: 行数/refs/qids 前后对账 + 死键闭包
# G2 抽查: 新增/变更 sha 的 blob HEAD 存在性 + 抽样下载魔数/解码
# G3 恒等: 未触碰行 byte-equal 抽样 + 死信行数恒等式
import gzip, json, os, random, sys, time, pickle, hashlib
from collections import Counter, defaultdict

HOME = "/home/ubuntu"
W2 = f"{HOME}/merge_wave2"
IN, WK, OUT = f"{W2}/in", f"{W2}/work", f"{W2}/out"

def iter_gz(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", errors="replace") as f:
        for l in f: yield l

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

def g1():
    """守恒: 输入 vs 输出的行/refs/qids 精确对账 + 死键闭包"""
    rep = json.load(open(f"{OUT}/wave2_report.json"))
    m = rep["merge"]
    def scan(path):
        rows = refs = qids = 0; fix = Counter()
        for l in iter_gz(path):
            o = json.loads(l); rows += 1
            refs += len(o.get("refs") or []); qids += len(o.get("qids") or [])
            fix[o.get("fix_state")] += 1
        return rows, refs, qids, fix
    log("scan baseline...")
    b_rows, b_refs, b_qids, _ = scan(f"{IN}/images.v2.jsonl.gz")
    log("scan wave2...")
    w_rows, w_refs, w_qids, w_fix = scan(f"{OUT}/images.v2.wave2.jsonl.gz")
    # 输出中 si2 行的 qids 总和(用于 qids 恒等式)
    si2_qn = 0
    for l in iter_gz(f"{OUT}/images.v2.wave2.jsonl.gz"):
        if '"fix_state": "si2"' in l:
            si2_qn += len(json.loads(l).get("qids") or [])
    # 被移除行的 refs/qids(从死信 moved_row 取)
    dropped_refs = dropped_qids = 0
    for l in iter_gz(f"{OUT}/deadletter.v2.wave2.jsonl.gz"):
        if '"moved_row"' not in l: continue
        o = json.loads(l)
        if o.get("moved_row"):
            dropped_refs += len(o["moved_row"].get("refs") or [])
            dropped_qids += len(o["moved_row"].get("qids") or [])
    exp_rows = b_rows - m["wm404_dead_moved_to_deadletter"] + m["si2_new"] \
               + m["wm404_add_q"] - rep["bucket_merge"]["absorbed"]
    got = {"baseline_rows": b_rows, "wave2_rows": w_rows, "expected_rows": exp_rows,
           "rows_match": w_rows == exp_rows,
           "baseline_refs": b_refs, "wave2_refs": w_refs,
           "expected_refs": b_refs + m["si2_new"] + m["wm404_add_q"] - dropped_refs,
           "refs_match": w_refs == b_refs + m["si2_new"] + m["wm404_add_q"] - dropped_refs,
           "dropped_refs": dropped_refs,
           "baseline_qids": b_qids, "wave2_qids": w_qids,
           "expected_qids": b_qids + si2_qn + m["wm404_add_q"] - dropped_qids,
           "qids_match": w_qids == b_qids + si2_qn + m["wm404_add_q"] - dropped_qids,
           "si2_qids_sum": si2_qn, "dropped_qids": dropped_qids,
           "fix_state_hist": dict(w_fix)}
    # 死键闭包: 每个 dead key 恰好落位一次
    tasks = [json.loads(l) for l in iter_gz(f"{IN}/wm404_tasks.jsonl.gz")]
    cand = pickle.load(open(f"{WK}/wm404_cand_all.pkl", "rb"))
    dead_keys = {(t["qid"], t["commons_file"]) for t in tasks} - set(cand.keys())
    seen = Counter()
    for l in iter_gz(f"{OUT}/deadletter.v2.wave2.jsonl.gz"):
        o = json.loads(l)
        if o.get("dead_class") == "dead_404_final" and not o.get("moved_row"):
            seen[(o.get("qid"), o.get("external_id"))] += 1
    for l in open(f"{OUT}/wm404_pid_dead_final.jsonl"):
        o = json.loads(l)
        seen[(o.get("qid"), o.get("external_id"))] += 1
    moved = set()
    for l in iter_gz(f"{OUT}/deadletter.v2.wave2.jsonl.gz"):
        if '"moved_row"' not in l: continue
        o = json.loads(l)
        if o.get("moved_row"):
            for q in o["moved_row"].get("qids") or []:
                for r in o["moved_row"].get("refs") or []:
                    moved.add((q, r.get("external_id")))
    dup = {k: v for k, v in seen.items() if v > 1}
    covered = set(seen) | (dead_keys & moved)
    got["dead_keys_total"] = len(dead_keys)
    got["dead_covered"] = len(covered & dead_keys)
    got["dead_uncovered"] = len(dead_keys - covered)
    got["dead_dup_written"] = len(dup)
    got["moved_rows_keys"] = len(moved)
    # 主表残留检查: drop shas(blob 实存=false 的移除行)不得残留于输出
    bc = json.load(open(f"{WK}/wm404_main_blobcheck.json"))
    drop = {s for s, d in bc["dead_main"].items() if not d["exists"]}
    resid = 0
    if drop:
        for l in iter_gz(f"{OUT}/images.v2.wave2.jsonl.gz"):
            o = json.loads(l)
            if o["sha256"] in drop: resid += 1
    got["drop_sha_residual"] = resid
    got["drop_sha_n"] = len(drop)
    json.dump(got, open(f"{WK}/g1.json", "w"), indent=1, default=str)
    log("G1", json.dumps(got)[:900])

def g2(n_head=400, n_dl=24):
    """抽查: 新增/换 sha 的 blob 存在性(COS HEAD) + 部分下载魔数校验"""
    sys.path.insert(0, f"{HOME}/demiflow_collect")
    from cosio import COSCreds, COSIO, build_host
    io = COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
               build_host("lhcos-368f6-1256345599", "ap-singapore"))
    from concurrent.futures import ThreadPoolExecutor
    random.seed(20260924)
    new_rows = []
    for l in iter_gz(f"{OUT}/images.v2.wave2.jsonl.gz"):
        if '"fix_state": "si2"' in l or '"fix_state": "wm404_recovered"' in l or '"fix_state": "meta_refetch"' in l:
            new_rows.append(l)
            if len(new_rows) >= 60000: break
    sample = random.sample(new_rows, min(n_head, len(new_rows)))
    objs = [(json.loads(l)["blob_path"], json.loads(l)["fix_state"]) for l in sample]
    st = Counter()
    def head(bp):
        try: return bp, bool(io.head("lhcos-data/demiwtg-data/datasets/demiwtg/kb/" + bp))
        except Exception: return bp, False
    with ThreadPoolExecutor(8) as ex:
        for bp, ok in ex.map(head, [o[0] for o in objs]):
            st["head_ok" if ok else "head_MISSING"] += 1
    # 下载魔数: si2 抽 12, met 抽 8, wm404 抽 4
    dl_n = 0; magic_ok = 0; MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"\x00\x00\x00\x0c", b"\x00\x00\x00 ftyp")
    picked = ([o for o in objs if o[1] == "si2"][:12] + [o for o in objs if o[1] == "meta_refetch"][:8]
              + [o for o in objs if o[1] == "wm404_recovered"][:4])
    for bp, _ in picked:
        try:
            b = io.get_bytes("lhcos-data/demiwtg-data/datasets/demiwtg/kb/" + bp)[:16]
            dl_n += 1
            if any(b.startswith(m) for m in MAGIC): magic_ok += 1
        except Exception:
            pass
    got = {"head_sample": len(objs), **dict(st), "downloaded": dl_n, "magic_ok": magic_ok}
    json.dump(got, open(f"{WK}/g2.json", "w"), indent=1)
    log("G2", json.dumps(got))

def g3(n=1500):
    """恒等: 未触碰行 byte-equal 抽样 + 输出完整 gzip 校验 + 死信行数恒等"""
    random.seed(97)
    # 从 v2 随机取行(排除已知被改的),在输出中查找 byte-equal
    import subprocess
    # 输出 gzip 完整性
    r = subprocess.run(["python3", "-c",
        "import gzip;f=gzip.open('" + OUT + "/images.v2.wave2.jsonl.gz','rb');n=0\n"
        "while True:\n b=f.read(1<<24)\n if not b: break\n n+=len(b)\nprint('bytes',n)"],
        capture_output=True, text=True)
    out_bytes = r.stdout.strip()
    # 抽样: v2 行 sha 是否都在输出中且内容一致(用 sha 抽样索引)
    v2_rows = {}
    for i, l in enumerate(iter_gz(f"{IN}/images.v2.jsonl.gz")):
        if i % 9973 == 0:
            v2_rows[json.loads(l)["sha256"]] = l
        if len(v2_rows) >= n: break
    st = Counter()
    for l in iter_gz(f"{OUT}/images.v2.wave2.jsonl.gz"):
        o = json.loads(l)
        if o["sha256"] in v2_rows:
            st["byte_equal" if l == v2_rows[o["sha256"]] else "CHANGED"] += 1
    dl_in = sum(1 for _ in iter_gz(f"{IN}/deadletter.v2.jsonl.gz"))
    dl_out = sum(1 for _ in iter_gz(f"{OUT}/deadletter.v2.wave2.jsonl.gz"))
    rep = json.load(open(f"{OUT}/wave2_report.json"))
    d = rep["deadletter"]
    got = {"sample_n": len(v2_rows), **dict(st), "out_stream": out_bytes,
           "dl_in": dl_in, "dl_out": dl_out,
           "dl_expected": dl_in + d["appended_moved_rows"] + d["appended_from_v1"],
           "dl_match": dl_out == dl_in + d["appended_moved_rows"] + d["appended_from_v1"]}
    json.dump(got, open(f"{WK}/g3.json", "w"), indent=1)
    log("G3", json.dumps(got))

if __name__ == "__main__":
    {"g1": g1, "g2": g2, "g3": g3, "all": lambda: (g1(), g2(), g3())}[sys.argv[1]]()
