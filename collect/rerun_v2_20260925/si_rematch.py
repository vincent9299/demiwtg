#!/usr/bin/env python3
"""si name 兜底重匹配 · Step1: 学名(P225)重挂 + 旧关系处置标记.

背景: si 清单生成时 taxon 匹配(P225 学名→QID)失败的 media 落入 name 兜底,
把目录元数据(年份/编号/采集人/采集地)匹配成了垃圾 QID(详见报告第六节).
本脚本用与原 taxon 匹配同源的 concept_xref.tsv.gz(P225) 对 name 兜底 media 重挂.

输入:
  _staging/raw/concept_xref.tsv.gz      qid\tP225\t学名 (原始 taxon 匹配同源索引)
  batch2/lists/fetch_smithsonian.tsv.gz media_id\trecord_id\tQID\tmatch_type\tlic\t标题\turl
  sample_1m_transfer/assets/p31_all.tsv 全量 P31 主类(为旧目标分类垃圾/错挂)
输出(本目录):
  si_rematch_map.tsv    media_id\told_qid\tnew_qid\tmatched_name\tmethod\tdisposition
  si_rematch_stats.json 漏斗统计

处置(disposition)口径:
  replace       学名命中 → 旧关系替换为 taxon QID
  remove_junk   未命中且旧目标是元数据类(年/数字/消歧义页/姓氏) → 删旧关系
  remove_wrong  未命中且旧目标是语义错挂(采集人Q5/采集地/机构) → 删旧关系
  keep          未命中但旧目标看起来是实体级匹配(艺术品等长尾) → 保留
method: binomial/trinomial/genus/full(学名命中方式)
"""
import collections
import gzip
import json
import os
import re
import subprocess
import time

BASE = "/yzp/zhaozy/yangzepeng/0905"
XREF = f"{BASE}/_staging/raw/concept_xref.tsv.gz"
LIST = f"{BASE}/demiwtg/collect/batch2/lists/fetch_smithsonian.tsv.gz"
P31 = f"{BASE}/demiwtg/collect/sample_1m_transfer/assets/p31_all.tsv"
OUT = f"{BASE}/demiwtg/collect/rerun_v2_20260925"

# 旧目标主类 → 处置分类(未命中学名时的兜底规则)
JUNK_MAINS = {          # 纯元数据类 → 删
    "Q577": "year", "Q3186692": "cal_year", "Q3311614": "century_year",
    "Q16317911": "pos_int", "Q125577": "nat_num", "Q11563": "number",
    "Q4167410": "disambig", "Q22808320": "person_disambig", "Q15633587": "wiki_page",
    "Q101352": "surname", "Q49008": "prime",
}
WRONG_MAINS = {         # 语义错挂(真实体但非照片内容) → 删
    "Q5": "collector_person", "Q852013": "craton", "Q4022": "river_place",
    "Q20857065": "us_agency", "Q1970365": "museum", "Q366301": "expedition",
}

# 学名解析: 二名法/三名法/属名; 标题=「学名 + 命名人」形态
RANK3 = {"var.", "subsp.", "ssp.", "f.", "f.sp."}


def norm_title(t):
    """标题 → 候选学名列表(从完整到粗粒度, 依次尝试)."""
    t = t.replace("×", " ").replace("  ", " ").strip()
    toks = t.split()
    if not toks:
        return []
    cands = []
    # 三名法: Genus ep. rank ep.
    if len(toks) >= 5 and toks[2].lower() in RANK3 and toks[1][0].islower():
        cands.append(" ".join(toks[:2] + toks[3:5]))
        cands[-1] = f"{toks[0]} {toks[1]} {toks[2]} {toks[3]}"  # 保留 rank 记法
    # 二名法: Genus epithet
    if len(toks) >= 2 and toks[0][0].isupper() and toks[1][0].islower():
        cands.append(f"{toks[0]} {toks[1]}")
        if len(toks) >= 5 and toks[2].lower() in RANK3:
            cands.append(f"{toks[0]} {toks[1]} {toks[2]} {toks[4] if len(toks)>4 else toks[3]}")
    # 全串(标题本身就是学名, 无命名人)
    if len(toks) <= 3:
        cands.append(t)
    # 属名兜底(最后尝试)
    if toks[0][0].isupper():
        cands.append(toks[0])
    return cands


def main():
    t0 = time.time()
    # ---- 1. P225 学名索引(同源 taxon 匹配); 同名多 QID 时记冲突, 后续择一 ----
    by_name = {}
    conflicts = 0
    with gzip.open(XREF, "rt") as f:
        for l in f:
            qid, prop, val = l.rstrip("\n").split("\t")
            if prop != "P225":
                continue
            if val in by_name:
                if by_name[val] != qid:
                    conflicts += 1
                    by_name[val] = min(by_name[val], qid)  # 稳定择小, 可复现
            else:
                by_name[val] = qid
    print(f"[1] P225 索引 {len(by_name):,} 学名(同名冲突 {conflicts:,}) {time.time()-t0:.0f}s", flush=True)

    # ---- 2. name 兜底唯一 media(标题+旧QID), 同时记 taxon 匹配的标题→QID(一致性检查用) ----
    name_media = {}                     # media → (old_qid, title)
    taxon_titles = {}                   # 标题 → QID(taxon 匹配, 自洽性对照)
    with gzip.open(LIST, "rt") as f:
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            media, _rec, qid, mtype = p[0], p[1], p[2], p[3]
            if mtype == "name":
                if media not in name_media:
                    name_media[media] = (qid, p[5])
            elif mtype == "taxon":
                taxon_titles.setdefault(p[5], set()).add(qid)
    print(f"[2] name 兜底 media {len(name_media):,}, taxon 标题 {len(taxon_titles):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 3. 旧目标主类(p31_all 扫一遍, 只取目标集) ----
    olds = {q for q, _ in name_media.values()}
    main_of = {}
    want = set(olds)
    with open(P31) as f:
        for l in f:
            q, _, rest = l.partition("\t")
            if q in want:
                main_of[q] = "Q" + rest.split(",")[0].strip()
                want.discard(q)
                if not want:
                    break
    print(f"[3] 旧目标主类 {len(main_of):,} {time.time()-t0:.0f}s", flush=True)

    # ---- 4. 重匹配 + 处置 ----
    stats = collections.Counter()
    out_rows = []
    agree = 0
    for media, (old_qid, title) in name_media.items():
        new_qid = ""
        method = ""
        name_hit = ""
        for cand in norm_title(title):
            hit = by_name.get(cand)
            if hit:
                new_qid, name_hit = hit, cand
                method = ("full" if " " not in cand and cand == title.strip()
                          else "genus" if " " not in cand
                          else "trinomial" if cand.count(" ") >= 3 else "binomial")
                break
        if new_qid:
            disp = "replace"
            stats[f"hit_{method}"] += 1
            # 一致性: taxon 匹配里同标题的 QID 是否一致
            tt = taxon_titles.get(title)
            if tt:
                stats["taxon_title_exists"] += 1
                if new_qid in tt:
                    agree += 1
                    stats["agree_with_taxon"] += 1
        else:
            m = main_of.get(old_qid, "")
            if m in JUNK_MAINS:
                disp = "remove_junk"
                stats[f"rm_junk_{JUNK_MAINS[m]}"] += 1
            elif m in WRONG_MAINS:
                disp = "remove_wrong"
                stats[f"rm_wrong_{WRONG_MAINS[m]}"] += 1
            else:
                disp = "keep"
                stats["keep_no_hit"] += 1
            stats["miss"] += 1
        stats[disp] += 1
        out_rows.append((media, old_qid, new_qid, name_hit, method, disp))

    with open(f"{OUT}/si_rematch_map.tsv", "w") as f:
        f.write("media_id\told_qid\tnew_qid\tmatched_name\tmethod\tdisposition\n")
        for r in out_rows:
            f.write("\t".join(r) + "\n")
    stats["total_media"] = len(name_media)
    stats["p225_index"] = len(by_name)
    with open(f"{OUT}/si_rematch_stats.json", "w") as f:
        json.dump(dict(stats), f, indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in sorted(stats.items())}, indent=1, ensure_ascii=False))
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
