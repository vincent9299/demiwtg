#!/usr/bin/env python3
# 生物源关联器：概念_xref(P225/P846/P3151) × 概念集 → concept_images 边 + 三段漏斗
# 用法: python3 link_bio.py <species_list.tsv: 学名\tgbif_id\t来源\t图数或清单>
import sys, gzip, json, collections

XREF = "/home/ubuntu/demi/raw/concept_xref.tsv.gz"  # 从 d 拷回后路径
CONCEPTS = "/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_concepts.jsonl.gz"

def load_xref():
    by_name, by_gbif = {}, {}
    with gzip.open(XREF, "rt") as f:
        for line in f:
            qid, prop, val = line.rstrip("\n").split("\t")
            if prop == "P225": by_name[val] = qid
            elif prop == "P846": by_gbif[val] = qid
    return by_name, by_gbif

def load_concepts():
    s = set()
    import subprocess
    p = subprocess.Popen(["zcat", CONCEPTS], stdout=subprocess.PIPE, bufsize=1<<22)
    for line in p.stdout:
        try: s.add(line.decode().split('"')[3])
        except Exception: pass
    p.wait(); return s

def main():
    src_list = sys.argv[1]
    by_name, by_gbif = load_xref()
    concepts = load_concepts()
    print(f"xref: P225={len(by_name):,} P846={len(by_gbif):,} | 概念集={len(concepts):,}")
    n_total = n_bridge = n_concept = 0
    edges = {}
    for line in open(src_list, encoding="utf-8"):
        c = line.rstrip("\n").split("\t")
        name_raw, gbif = c[0], (c[1] if len(c) > 1 else "")
        toks = name_raw.split()
        name = " ".join(toks[:2]) if len(toks) > 2 else name_raw
        n_total += 1
        qn = by_name.get(name) or by_name.get(name_raw)
        qg = by_gbif.get(gbif) if gbif else None
        qid = qn if (qn and qg and qn == qg) else (qn or qg)
        if not qid: continue
        n_bridge += 1
        if qid not in concepts: continue
        n_concept += 1
        edges[qid] = name
    print(f"漏斗: 总种数={n_total:,} 过桥={n_bridge:,}({n_bridge*100//max(n_total,1)}%) 进概念集={n_concept:,}({n_concept*100//max(n_total,1)}%)")
    out = src_list + ".linked.tsv"
    with open(out, "w") as f:
        for qid, name in edges.items(): f.write(f"{qid}\t{name}\n")
    print("已写出:", out)

if __name__ == "__main__":
    main()
