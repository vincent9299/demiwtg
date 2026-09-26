#!/usr/bin/env python3
"""重跑选定分类体系 · Step2: 聚合到语义类/桶两级, 与旧跑数对比, 测算新版水填配额.

输入:
  /dev/shm/rerun_edges/*                 Step1 产出的 (qid, short, size_bytes) 边
  assets/qid_cut_map_v4_5000.tsv         选定体系(5千档) qid→main_class→cat_id
  assets/sample_1m_short_classes.tsv     旧框架 3,335 类的 available 口径(对照)
  assets/qid_res_short1024.tsv           旧短边≥1024 QID 集(对照)
  qid_edges/cut_categories_v4_5000.tsv   原始类目标签(带分段名, 供语义类命名)
  p31/p31_class_bucket.tsv               main_class→32 桶 上卷表
  p31/p31_bucket_counts.tsv              旧桶级 entities(对照)
  assets/p31_all.tsv                     全量 P31 主类(为账本新出现的 QID 补映射)

输出(本目录):
  class_counts_v2.tsv    语义类级: 实体/短边图量/字节/旧available/是否仍≥200
  bucket_counts_v2.tsv   32 桶级: 同口径 + 旧 entities 对照
  summary_v2.json        头部数字(口径对齐用)

口径: 实体=账本中出现≥1图的 QID; entities_short=有≥1张短边≥1024图的 QID;
      images_short=该类实体名下短边≥1024 图的 qid-图边数(一图挂多 QID 重复计).
"""
import collections
import glob
import json
import os
import sys
import time

BASE = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect"
A = f"{BASE}/sample_1m_transfer/assets"
SHM = sys.argv[1] if len(sys.argv) > 1 else "/dev/shm/rerun_edges"
TAG = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = f"{BASE}/rerun_v2_20260925"


def semantic(cid):
    """与 make_sample_short.py 完全一致的语义类折叠: 去分段/noaux/末位@桶, 本级#self/#other 归本体."""
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

    # ---- 1. 边聚合: qid → [n_all, n_short, bytes_short] ----
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

    # ---- 2. 旧口径载入(对照) ----
    old_avail = {}                       # 语义类 → 旧 available
    with open(f"{A}/sample_1m_short_classes.tsv") as f:
        next(f)
        for l in f:
            c, a, _q = l.rstrip("\n").split("\t")
            old_avail[c] = int(a)
    old_short = set()                    # 旧短边≥1024 QID 集
    for l in open(f"{A}/qid_res_short1024.tsv"):
        old_short.add(l.split("\t", 1)[0])
    old_bucket = {}                      # 桶 → 旧 entities
    with open(f"{BASE}/p31/p31_bucket_counts.tsv") as f:
        next(f)
        for l in f:
            qid, nm, ent, _cls = l.rstrip("\n").split("\t")
            old_bucket[qid] = (nm, int(ent))

    # 原始类目标签(命名语义类用)
    raw_lab = {}
    with open(f"{BASE}/qid_edges/cut_categories_v4_5000.tsv", encoding="utf-8") as f:
        next(f)
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) >= 2:
                raw_lab[p[0]] = p[1]

    # main_class → 桶
    cls_bucket = {}
    with open(f"{BASE}/p31/p31_class_bucket.tsv", encoding="utf-8") as f:
        next(f)
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) >= 4:
                cls_bucket[p[0]] = p[2]

    # ---- 3. cut map 连接 ----
    qid_main = {}                        # qid → main_class(选定体系内)
    qid_cat = {}
    with open(f"{A}/qid_cut_map_v4_5000.tsv") as f:
        next(f)
        for l in f:
            q, mc, cat = l.rstrip("\n").split("\t")
            qid_main[q] = mc
            qid_cat[q] = cat
    print(f"[2] cut map QID {len(qid_main):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 4. 语义类级聚合 ----
    cls = {}                             # sem → [ent_all, ent_short, img_short, bytes_short]
    sem_raw = collections.defaultdict(collections.Counter)   # sem → raw cat 计数(命名用)
    unmapped = [0, 0, 0, 0]              # ent_all, ent_short, img_short, bytes_short
    for q, (n_all, n_short, b_short) in qc.items():
        cat = qid_cat.get(q)
        if cat is None:
            continue                     # 账本新 QID, Step5 单独处理
        s = 1 if n_short > 0 else 0
        if cat == "UNMAPPED":
            unmapped[0] += 1
            unmapped[1] += s
            unmapped[2] += n_short
            unmapped[3] += b_short
            continue
        sem = semantic(cat)
        sem_raw[sem][cat] += 1
        e = cls.get(sem)
        if e is None:
            cls[sem] = e = [0, 0, 0, 0]
        e[0] += 1
        e[1] += s
        e[2] += n_short
        e[3] += b_short
    print(f"[3] 语义类 {len(cls):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 5. 账本新 QID(不在旧 haveimg/cut map 内): 扫 p31_all 补主类 ----
    newq = [q for q in qc if q not in qid_cat]
    newq_main = {}
    if newq:
        newset = set(newq)
        for l in open(f"{A}/p31_all.tsv"):
            q, _, rest = l.partition("\t")
            if q in newset:
                newq_main[q] = "Q" + rest.split(",")[0].strip()
                newset.discard(q)
                if not newset:
                    break
    print(f"[4] 新 QID {len(newq):,}(有P31 {len(newq_main):,}) {time.time()-t0:.0f}s", flush=True)

    # ---- 5b. main_class→cat 多数票还原: 同一主类的 cat 归属本应唯一(树上溯确定),
    #          用旧 cut map 统计每个 main_class 的 cat 分布, 把新 QID 映射进选定体系 ----
    mc_cat = collections.defaultdict(collections.Counter)
    for q, mc in qid_main.items():
        mc_cat[mc][qid_cat[q]] += 1
    tot_mc = sum(len(c) for c in mc_cat.values())
    unan_mc = sum(1 for c in mc_cat.values() if len(c) == 1)
    mc2cat = {mc: c.most_common(1)[0][0] for mc, c in mc_cat.items()}
    newq_cat = {q: mc2cat.get(mc) for q, mc in newq_main.items()}
    ext = sum(1 for v in newq_cat.values() if v)
    print(f"[4b] main_class {len(mc2cat):,} 一致率 {unan_mc/len(mc2cat):.4f}, "
          f"新 QID 可映射 {ext:,}/{len(newq_cat):,}", flush=True)
    # 新 QID 计入语义类聚合(cat=UNMAPPED 或无映射的进 unmapped 统计)
    for q, cat in newq_cat.items():
        n_all, n_short, b_short = qc[q]
        s = 1 if n_short > 0 else 0
        if cat is None or cat == "UNMAPPED":
            unmapped[0] += 1
            unmapped[1] += s
            unmapped[2] += n_short
            unmapped[3] += b_short
            continue
        sem = semantic(cat)
        sem_raw[sem][cat] += 1
        e = cls.get(sem)
        if e is None:
            cls[sem] = e = [0, 0, 0, 0]
        e[0] += 1
        e[1] += s
        e[2] += n_short
        e[3] += b_short

    # ---- 6. 桶级聚合(全部账本 QID: 旧 map 主类 + 新 QID 补充主类) ----
    bkt = collections.defaultdict(lambda: [0, 0, 0, 0])   # bucket_qid → 同四列
    def badd(mc, q):
        b = cls_bucket.get(mc)
        key = b if b else "NO_ROLLUP"
        e = bkt[key]
        n_all, n_short, b_short = qc[q]
        e[0] += 1
        if n_short > 0:
            e[1] += 1
        e[2] += n_short
        e[3] += b_short
    for q, mc in qid_main.items():
        if q in qc:
            badd(mc, q)
    for q, mc in newq_main.items():
        badd(mc, q)
    nop31 = sum(1 for q in qc if q not in qid_main and q not in newq_main)
    print(f"[5] 桶 {len(bkt):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 7. 水填配额测算(同 make_sample_short 逻辑, 类阈值≥200, 目标 1M) ----
    sizes = sorted(v[1] for v in cls.values() if v[1] >= 200)
    lo, hi = 1, sizes[-1]
    for _ in range(60):
        mid = (lo + hi) // 2
        if sum(min(x, mid) for x in sizes) < 1_000_000:
            lo = mid
        else:
            hi = mid
    quota = hi
    fill = sum(min(x, quota) for x in sizes)
    n200 = len(sizes)

    # ---- 8. 落盘 ----
    def sem_label(sem):
        """取该语义类下最常见原始类目的标签, 去掉分段后缀."""
        raw, _ = sem_raw[sem].most_common(1)[0]
        lab = raw_lab.get(raw, raw)
        return lab.split("(")[0].strip() if "(" in lab else lab

    with open(f"{OUT}/class_counts_v2{TAG}.tsv", "w", encoding="utf-8") as f:
        f.write("semantic_cat\tlabel\tentities_all\tentities_short\timages_short\t"
                "bytes_short\told_available\tdelta_short\tpass200\n")
        rows = sorted(cls.items(), key=lambda x: -x[1][1])
        for sem, e in rows:
            oa = old_avail.get(sem, 0)
            f.write(f"{sem}\t{sem_label(sem)}\t{e[0]}\t{e[1]}\t{e[2]}\t{e[3]}\t"
                    f"{oa}\t{e[1]-oa}\t{1 if e[1] >= 200 else 0}\n")
    bname = {v[0]: v[0] for v in old_bucket.values()}
    with open(f"{OUT}/bucket_counts_v2{TAG}.tsv", "w", encoding="utf-8") as f:
        f.write("bucket_qid\tbucket_name\tentities_all\tentities_short\timages_short\t"
                "bytes_short\told_entities\tnew_qids_short\n")
        for bq, e in sorted(bkt.items(),
                            key=lambda x: -x[1][1]):
            nm = old_bucket.get(bq, ("", 0))[0] or ("未上卷" if bq == "NO_ROLLUP" else bq)
            oe = old_bucket.get(bq, ("", 0))[1]
            nq = sum(1 for q, mc in newq_main.items()
                     if cls_bucket.get(mc) == bq and qc[q][1] > 0)
            f.write(f"{bq}\t{nm}\t{e[0]}\t{e[1]}\t{e[2]}\t{e[3]}\t{oe}\t{nq}\n")

    # ---- 9. 汇总 ----
    tot_short = sum(1 for v in qc.values() if v[1] > 0)
    new_short = sum(1 for q in qc if q not in qid_cat and qc[q][1] > 0)
    lost_short = sum(1 for q in old_short if q not in qc or qc[q][1] == 0)
    covered = sum(v[1] for v in cls.values())
    summary = {
        "ledger_rows": 18643608, "ledger_qids": len(qc),
        "qids_short1024": tot_short, "old_qids_short1024": len(old_short),
        "new_qids_in_ledger": len(newq), "new_qids_short1024": new_short,
        "old_short_qids_lost": lost_short,
        "cls_total": len(cls), "cls_pass200": n200, "cls_in_old_framework": len(old_avail),
        "cls_entities_short_covered": covered,
        "framework_coverage_of_short": round(covered / tot_short, 4),
        "quota_new": quota, "fill_new": fill,
        "unmapped_ent_all": unmapped[0], "unmapped_ent_short": unmapped[1],
        "unmapped_images_short": unmapped[2],
        "no_p31_entities": nop31,
        "mc_unanimous_rate": round(unan_mc / len(mc2cat), 4),
        "new_qids_mapped_to_framework": ext,
    }
    with open(f"{OUT}/summary_v2{TAG}.json", "w") as f:
        json.dump(summary, f, indent=1, ensure_ascii=False)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
