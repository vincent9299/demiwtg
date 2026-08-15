#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未归类实体（untaxonomy）双向同步：隔离 + 重挂（幂等）。

背景：图片打标只存实体名（不含路径，见 AGENTS.md 1.5），以标签体系
（taxonomy-instances/data/instances.json 的实例名）为准。体系演化（删实体/改实例名）后，
部分旧实体名失去归属；但后续版本可能重新收录，因此未归类实体不删除、单独隔离存放
（dataset/meta/untaxonomy.json，软链树 dataset/untaxonomy/），待可匹配时重挂回主索引。

行为：
  1. 重挂：dataset/meta/untaxonomy.json 中能在当前体系匹配到的实体名
     （精确匹配优先，其次归一化实例名：去《》、去（...）注释；多候选时放弃，
     避免挂错）移回 dataset/meta/tags.json，同步更新 images.jsonl 的 tags 字段；
  2. 隔离：dataset/meta/tags.json 中不在当前体系实例名集合的键移入
     untaxonomy.json（images.jsonl 记录保留原 tag，图不动）。

用法：
  python3 scripts/lake/relink_orphan_tags.py            # 对 taxonomy-instances/data/instances.json
  python3 scripts/lake/relink_orphan_tags.py --taxonomy taxonomy-instances/data/instances.json

完成后重建软链：
  python3 scripts/lake/link_by_tag.py
  python3 scripts/lake/link_by_tag.py --tags dataset/meta/untaxonomy.json --out dataset/untaxonomy
"""
import argparse
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TAGS = os.path.join(ROOT, "dataset/meta/tags.json")
ORPHAN = os.path.join(ROOT, "dataset/meta/untaxonomy.json")


def norm(s: str) -> str:
    s = s.strip().replace("《", "").replace("》", "")
    return re.sub(r"[（(].*?[)）]", "", s).strip()


def merge_into(dst: dict, key: str, imgs: list):
    tgt = dst.setdefault(key, [])
    have = {e["sha256"] for e in tgt}
    for e in imgs:
        if e["sha256"] not in have:
            tgt.append(e)
            have.add(e["sha256"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy",
                    default=os.path.join(ROOT, "taxonomy-instances", "data", "instances.json"))
    args = ap.parse_args()

    tags = json.load(open(TAGS, encoding="utf-8")) if os.path.exists(TAGS) else {}

    v3 = json.load(open(args.taxonomy, encoding="utf-8")).get("instances") or []
    v3names = {it["name"] for it in v3 if it.get("name")}
    by_norm: dict = {}
    for it in v3:
        nm = it.get("name")
        if not nm:
            continue
        k = norm(nm)
        if k and nm not in by_norm.setdefault(k, []):
            by_norm[k].append(nm)

    store = json.load(open(ORPHAN, encoding="utf-8")) if os.path.exists(ORPHAN) else {}

    # 1) 重挂：隔离区 -> 主索引
    reloc = {}
    for t in list(store):
        if t in v3names:
            reloc[t] = t
            continue
        cands = [c for c in by_norm.get(norm(t), []) if c in v3names and c != t]
        if len(cands) == 1:
            reloc[t] = cands[0]
    for old, new in reloc.items():
        merge_into(tags, new, store.pop(old))

    # 2) 隔离：主索引 -> 隔离区
    quar = {t: tags[t] for t in tags if t not in v3names}
    for t, imgs in quar.items():
        merge_into(store, t, imgs)
        del tags[t]

    # 3) images.jsonl：仅重挂需要改名
    n_touched = 0
    if reloc:
        lines = []
        with open(os.path.join(ROOT, "dataset/meta/images.jsonl"), encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                tl = d.get("tags") or []
                if any(t in reloc for t in tl):
                    seen, new_tl = set(), []
                    for t in tl:
                        t2 = reloc.get(t, t)
                        if t2 not in seen:
                            seen.add(t2)
                            new_tl.append(t2)
                    d["tags"] = new_tl
                    n_touched += 1
                lines.append(json.dumps(d, ensure_ascii=False))
        with open(os.path.join(ROOT, "dataset/meta/images.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    if reloc or quar:
        json.dump(tags, open(TAGS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(store, open(ORPHAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"重挂 {len(reloc)} | 新隔离 {len(quar)} | 主索引 {len(tags)} 键 | 隔离区 {len(store)} 键"
          f" | images.jsonl 更新 {n_touched} 条")


if __name__ == "__main__":
    main()
