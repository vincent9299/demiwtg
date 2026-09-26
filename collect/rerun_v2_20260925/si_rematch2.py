#!/usr/bin/env python3
"""si name 兜底重匹配 · v2: 修正三名法候选 + 同名多 QID 消解 + 分层处置.

v1 教训(见会话记录):
  ① P225 学名有同名异义(异名组合/属同名), 取首/取小会撞错条目;
  ② 三名法候选构造出现去 rank 记法的畸形串;
  ③ 原始 taxon 匹配自身有错(雪鸮标题→乌林鸮), 不能作真值, 一致率仅作参考.

v2 消解策略(同名多 QID 时): 生物类元桶(Q16521 系, p31_all+p31_class_bucket 判定)
  > 带 P846(GBIF 键, xref 判定) > 稳定取小 QID.
学名候选优先级: 三名法(带 rank 记法) > 二名法 > 全串 > 属名(粗粒度, 单独标记).

输出: si_rematch_map2.tsv / si_rematch_stats2.json (列同 v1)
"""
import collections
import gzip
import json
import re
import time

BASE = "/yzp/zhaozy/yangzepeng/0905"
XREF = f"{BASE}/_staging/raw/concept_xref.tsv.gz"
LIST = f"{BASE}/demiwtg/collect/batch2/lists/fetch_smithsonian.tsv.gz"
P31 = f"{BASE}/demiwtg/collect/sample_1m_transfer/assets/p31_all.tsv"
CLSB = f"{BASE}/demiwtg/collect/p31/p31_class_bucket.tsv"
OUT = f"{BASE}/demiwtg/collect/rerun_v2_20260925"

JUNK_MAINS = {"Q577", "Q3186692", "Q3311614", "Q16317911", "Q125577", "Q11563",
              "Q4167410", "Q22808320", "Q15633587", "Q101352", "Q49008"}
WRONG_MAINS = {"Q5", "Q852013", "Q4022", "Q20857065", "Q1970365", "Q366301"}
RANK3 = {"var.", "subsp.", "ssp.", "f."}


def norm_title(t):
    """标题 → 学名候选(精确→粗粒度, 严格记法, 无畸形串)."""
    t = t.replace("×", " ").replace("  ", " ").strip()
    toks = t.split()
    if not toks or not toks[0][0].isupper():
        return []
    cands = []
    if len(toks) >= 2 and toks[1][0].islower():
        if len(toks) >= 5 and toks[2].lower() in RANK3:
            cands.append(" ".join(toks[:4]))            # G e subsp. s(带 rank, 截到种下加词)
            cands.append(f"{toks[0]} {toks[1]} {toks[3]}")   # G e s(去 rank)
        elif len(toks) == 3 and toks[2][0].islower():
            cands.append(t)                              # G e s(裸三名法)
        cands.append(f"{toks[0]} {toks[1]}")             # 二名法
    if len(toks) <= 3:
        cands.append(t)                                  # 全串
    cands.append(toks[0])                                # 属名兜底
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def main():
    t0 = time.time()
    # ---- 1. xref: P225 名→候选 QID 集; P846 持有集(消解加权用) ----
    name2qids = collections.defaultdict(list)
    has_gbif = set()
    with gzip.open(XREF, "rt") as f:
        for l in f:
            qid, prop, val = l.rstrip("\n").split("\t")
            if prop == "P225":
                name2qids[val].append(qid)
            elif prop == "P846":
                has_gbif.add(qid)
    print(f"[1] P225 名 {len(name2qids):,}, 带 GBIF 键实体 {len(has_gbif):,} "
          f"{time.time()-t0:.0f}s", flush=True)

    # ---- 2. 生物类元桶判定: 主类 → 是否 Q16521 桶 ----
    cls_bucket = {}
    with open(CLSB) as f:
        next(f)
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) >= 4:
                cls_bucket[p[0]] = p[2]
    bio_mains = {c for c, b in cls_bucket.items() if b == "Q16521"}

    # ---- 3. name 兜底 media + taxon 参考集 ----
    name_media = {}
    taxon_titles = collections.defaultdict(set)
    with gzip.open(LIST, "rt") as f:
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            if p[3] == "name":
                name_media.setdefault(p[0], (p[2], p[5]))
            elif p[3] == "taxon":
                taxon_titles[p[5]].add(p[2])
    print(f"[2] name media {len(name_media):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 4. 需要判桶的 QID: 同名候选 ∪ 旧目标(处置用), 一次扫 p31_all ----
    need = set()
    for cands in name2qids.values():
        if len(cands) > 1:
            need.update(cands)
    olds = {q for q, _ in name_media.values()}
    need.update(olds)
    main_of = {}
    want = set(need)
    with open(P31) as f:
        for l in f:
            q, _, rest = l.partition("\t")
            if q in want:
                main_of[q] = "Q" + rest.split(",")[0].strip()
                want.discard(q)
                if not want:
                    break
    print(f"[3] p31 main {len(main_of):,} {time.time()-t0:.0f}s", flush=True)

    def resolve(qids):
        """同名消解: 生物桶 > 带 GBIF > 小 QID."""
        def score(q):
            m = main_of.get(q, "")
            return (0 if m in bio_mains else 1,
                    0 if q in has_gbif else 1, q)
        return min(qids, key=score)

    # ---- 5. 匹配 + 处置 ----
    stats = collections.Counter()
    rows = []
    for media, (old_qid, title) in name_media.items():
        new_qid = name_hit = ""
        method = ""
        for cand in norm_title(title):
            cands = name2qids.get(cand)
            if cands:
                new_qid = resolve(cands)
                name_hit = cand
                ntok = cand.count(" ")
                method = ("full" if cand == title.strip() and ntok >= 1
                          else "genus" if ntok == 0
                          else "trinomial" if ntok >= 2 else "binomial")
                break
        if new_qid:
            disp = "replace"
            stats[f"hit_{method}"] += 1
            tt = taxon_titles.get(title)
            if tt:
                stats["taxon_ref_exists"] += 1
                stats["taxon_ref_agree"] += (new_qid in tt)
        else:
            m = main_of.get(old_qid, "")
            if m in JUNK_MAINS:
                disp = "remove_junk"
            elif m in WRONG_MAINS:
                disp = "remove_wrong"
            else:
                disp = "keep"
            stats["miss"] += 1
        stats[disp] += 1
        rows.append((media, old_qid, new_qid, name_hit, method, disp))

    with open(f"{OUT}/si_rematch_map2.tsv", "w") as f:
        f.write("media_id\told_qid\tnew_qid\tmatched_name\tmethod\tdisposition\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    stats["total_media"] = len(name_media)
    stats["p225_names"] = len(name2qids)
    with open(f"{OUT}/si_rematch_stats2.json", "w") as f:
        json.dump(dict(stats), f, indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in sorted(stats.items())},
                     indent=1, ensure_ascii=False))
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
