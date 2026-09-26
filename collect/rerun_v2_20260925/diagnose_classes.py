#!/usr/bin/env python3
"""分类优化诊断 · 5千档语义类在新宇宙(合并版 20260926b, 不限短边)上的体检.

输出(本目录):
  cls_diag.tsv            语义类全量: 实体/图量/原始cat构成(shard折叠?@other?noaux?)/标记
  cls_diag_summary.json   头部数字 + 三类问题清单(过大/过小/没意思候选)

问题类定义:
  过大: 实体 > 5000(5千档本应 ≤5000; 语义折叠会重新合并 shard, 需要定位成因)
  过小: 200-500 边缘类
  没意思候选: 纯QID标签 / "其他|无值"聚集桶 / 属地细分微类 / 已知噪声残遗
"""
import collections
import glob
import json

BASE = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect"
SHM = "/dev/shm/rerun_edges_mrg"
OUT = f"{BASE}/rerun_v2_20260925"
MAXC = 5000
MINC = 200


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
    qc = {}
    for p in glob.glob(f"{SHM}/*"):
        with open(p) as f:
            for l in f:
                q, s, b = l.rstrip("\n").split("\t")
                e = qc.get(q)
                if e is None:
                    qc[q] = e = [0, 0]
                e[0] += 1
                e[1] += (s == "1")

    raw_lab = {}
    with open(f"{BASE}/qid_edges/cut_categories_v4_5000.tsv", encoding="utf-8") as f:
        next(f)
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) >= 2:
                raw_lab[p[0]] = p[1]

    cls = {}                                 # sem → [ent, img, rawcat 计数, shard数]
    for l in open(f"{OUT}/qid_cut_map_v4_5000_ext.tsv"):
        p = l.rstrip("\n").split("\t")
        if p[0] == "qid":
            continue
        q, mc, cat = p[0], p[1], p[2]
        if q not in qc or cat == "UNMAPPED":
            continue
        sem = semantic(cat)
        e = cls.get(sem)
        if e is None:
            cls[sem] = e = [0, 0, collections.Counter(), 0]
        e[0] += 1
        e[1] += qc[q][0]
        e[2][cat] += 1
        if "shard" in cat or "|fs" in cat:
            e[3] += 1

    def label(sem):
        raw, _ = cls[sem][2].most_common(1)[0]
        lab = raw_lab.get(raw, raw)
        return lab.split("(")[0].strip() if "(" in lab else lab

    rows = []
    for sem, e in cls.items():
        ent, img, cats, shard_n = e
        flags = []
        if ent > MAXC:
            flags.append("OVERSIZE")
        if MINC <= ent <= 500:
            flags.append("SMALL")
        if shard_n > 0 and ent > MAXC:
            flags.append("SHARD_FOLDED")     # 语义折叠把分段重新合大
        if e[2] and "@other" in e[2].most_common(1)[0][0]:
            flags.append("OTHER_BUCKET")
        lab = label(sem)
        if lab.startswith("Q") and lab[1:].isdigit():
            flags.append("QID_LABEL")
        if "无" in lab and "值" in lab:
            flags.append("NOVALUE_BUCKET")
        rows.append((sem, lab, ent, img, len(cats), shard_n, ";".join(flags)))

    rows.sort(key=lambda r: -r[2])
    with open(f"{OUT}/cls_diag.tsv", "w", encoding="utf-8") as f:
        f.write("semantic_cat\tlabel\tentities\timages\traw_cats\tshard_qids\tflags\n")
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")

    over = [r for r in rows if "OVERSIZE" in r[6]]
    shard_folded = [r for r in over if "SHARD_FOLDED" in r[6]]
    other_big = [r for r in over if "SHARD_FOLDED" not in r[6]]
    small = [r for r in rows if "SMALL" in r[6]]
    tiny = [r for r in rows if r[2] < MINC]
    qidlab = [r for r in rows if "QID_LABEL" in r[6]]
    summary = {
        "universe_qids_anyimg": len(qc),
        "semantic_classes": len(rows),
        "pass200": sum(1 for r in rows if r[2] >= MINC),
        "oversize_gt5000": len(over),
        "  of_which_shard_folded": len(shard_folded),
        "  of_which_structural(@other/noaux/其他)": len(other_big),
        "small_200_500": len(small),
        "tiny_lt200": len(tiny),
        "qid_label_classes": len(qidlab),
        "top10_oversize": [(r[1], r[2], r[6]) for r in over[:10]],
    }
    with open(f"{OUT}/cls_diag_summary.json", "w") as f:
        json.dump(summary, f, indent=1, ensure_ascii=False)
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
