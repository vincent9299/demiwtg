#!/usr/bin/env python3
"""构建逐实体扩展类目映射 + 重采样投影(供方案确认, 不执行正式抽样).

产物:
  qid_cut_map_v4_5000_ext.tsv  账本全部 QID → main_class → cat_id(选定体系+多数票扩展)
  project_1m.json              投影: 框架类数/配额/实体/图量/体积/桶级分布(seed=42 试抽)

口径与 make_sample_short.py 完全一致: 语义折叠 ≥200 实体成框架, 水填到 1M, seed=42.
"""
import collections
import glob
import json
import random
import time

BASE = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect"
A = f"{BASE}/sample_1m_transfer/assets"
SHM = "/dev/shm/rerun_edges_clean"
OUT = f"{BASE}/rerun_v2_20260925"


def semantic(cid):
    parts = cid.split("|")
    while parts and parts[-1].startswith("shard"):
        parts.pop()
    if len(parts) == 3 and parts[2] == "noaux":
        parts = parts[:2]
    if len(parts) == 1 and (cid.endswith("#self") or cid.endswith("#other")):
        return parts[0].split("#")[0]
    if parts and parts[-1].startswith("@"):
        parts.pop()
        if not parts:
            return "ORPH"
    return "|".join(parts)


def main():
    t0 = time.time()
    # ---- 1. clean 账本每 QID 图计数 ----
    qc = {}
    for p in glob.glob(f"{SHM}/*"):
        with open(p) as f:
            for l in f:
                q, s, b = l.rstrip("\n").split("\t")
                e = qc.get(q)
                if e is None:
                    qc[q] = e = [0, 0, 0]
                e[0] += 1
                if s == "1":
                    e[1] += 1
                    e[2] += int(b)
    print(f"[1] 账本 QID {len(qc):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 2. 旧 cut map + 多数票扩展 ----
    qid_cat, qid_mc = {}, {}
    mc_cat = collections.defaultdict(collections.Counter)
    with open(f"{A}/qid_cut_map_v4_5000.tsv") as f:
        next(f)
        for l in f:
            q, mc, cat = l.rstrip("\n").split("\t")
            qid_cat[q], qid_mc[q] = cat, mc
            mc_cat[mc][cat] += 1
    mc2cat = {mc: c.most_common(1)[0][0] for mc, c in mc_cat.items()}
    newq = [q for q in qc if q not in qid_cat]
    new_mc = {}
    want = set(newq)
    with open(f"{A}/p31_all.tsv") as f:
        for l in f:
            q, _, rest = l.partition("\t")
            if q in want:
                new_mc[q] = "Q" + rest.split(",")[0].strip()
                want.discard(q)
                if not want:
                    break
    with open(f"{OUT}/qid_cut_map_v4_5000_ext.tsv", "w") as f:
        f.write("qid\tmain_class\tcat_id\tin_old_map\n")
        for q, cat in qid_cat.items():
            f.write(f"{q}\t{qid_mc[q]}\t{cat}\t1\n")
        for q in newq:
            mc = new_mc.get(q, "")
            cat = mc2cat.get(mc, "UNMAPPED") if mc else "UNMAPPED"
            f.write(f"{q}\t{mc}\t{cat}\t0\n")
    for q, mc in new_mc.items():
        qid_mc[q] = mc
        qid_cat[q] = mc2cat.get(mc, "UNMAPPED") if mc else "UNMAPPED"
    print(f"[2] 扩展映射落盘(旧 {len(qid_cat)-len(newq):,} + 新 {len(newq):,}) "
          f"{time.time()-t0:.0f}s", flush=True)

    # ---- 3. 框架 + 水填配额(同 make_sample_short) ----
    frame = collections.defaultdict(list)
    for q, (n_all, n_short, b) in qc.items():
        cat = qid_cat.get(q)
        if cat and cat != "UNMAPPED" and n_short > 0:
            frame[semantic(cat)].append(q)
    cats = {c: q for c, q in frame.items() if len(q) >= 200}
    sizes = sorted(len(v) for v in cats.values())
    lo, hi = 1, sizes[-1]
    for _ in range(60):
        mid = (lo + hi) // 2
        if sum(min(x, mid) for x in sizes) < 1_000_000:
            lo = mid
        else:
            hi = mid
    QUOTA = hi
    fill = sum(min(x, QUOTA) for x in sizes)
    print(f"[3] 框架 {len(cats):,} 类, 配额 {QUOTA}, 可填 {fill:,}", flush=True)

    # ---- 4. seed=42 试抽 → 实体/图量/体积投影 ----
    rng = random.Random(42)
    picked = set()
    for c, qids in cats.items():
        picked.update(rng.sample(qids, min(len(qids), QUOTA)))
    n_img = sum(qc[q][1] for q in picked)
    n_bytes = sum(qc[q][2] for q in picked)
    # 桶级投影
    cls_bucket = {}
    with open(f"{BASE}/p31/p31_class_bucket.tsv") as f:
        next(f)
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) >= 4:
                cls_bucket[p[0]] = p[3]
    bkt = collections.Counter()
    for q in picked:
        bkt[cls_bucket.get(qid_mc.get(q, ""), "未上卷/无P31")] += 1
    proj = {
        "ledger_rows": 18643608, "universe_qids": len(qc),
        "qids_short1024": sum(1 for v in qc.values() if v[1] > 0),
        "framework_classes": len(cats), "quota": QUOTA, "fill_entities": fill,
        "sampled_entities": len(picked),
        "sampled_images_short": n_img, "sampled_tb_edge_weighted": n_bytes / 1e12,
        "bucket_entities_top": dict(bkt.most_common(12)),
    }
    with open(f"{OUT}/project_1m.json", "w") as f:
        json.dump(proj, f, indent=1, ensure_ascii=False)
    print(json.dumps(proj, indent=1, ensure_ascii=False))
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
