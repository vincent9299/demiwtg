#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""孤儿 tag 双向同步：隔离 + 重挂（幂等）。

背景：采集图库的 tag 以标签体系（data/instances_meta.json）为准。体系演化
（删分支/改路径）后，部分旧 tag 失去归属；但后续版本可能新增适合挂载的
节点，因此孤儿不删除、单独隔离存放，待可匹配时重挂回主索引。

行为：
  1. 重挂：dataset/meta/tags_orphan.json 中能在当前体系匹配到的 tag
     （精确匹配优先，其次归一化实例名：去《》、去（...）注释）并回
     dataset/meta/tags.json，同步更新 images.jsonl 的 tags 字段；
     多候选时优先同一级分支 + 公共路径段最多者。
  2. 隔离：dataset/meta/tags.json 中不在当前体系 tag 全集的键移入
     tags_orphan.json（images.jsonl 记录保留原 tag，图不动）。
  3. 每次操作追加审计到 dataset/meta/tag_relocations.jsonl。

用法：
  python3 scripts/relink_orphan_tags.py            # 对 data/instances_meta.json
  python3 scripts/relink_orphan_tags.py --taxonomy data/instances_meta.json

完成后重建软链：
  python3 scripts/link_by_tag.py
  python3 scripts/link_by_tag.py --tags dataset/meta/tags_orphan.json --out dataset/by_tag_orphan
"""
import argparse
import json
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_NAME = "融合世界标签体系 / "
TAGS = os.path.join(ROOT, "dataset/meta/tags.json")
ORPHAN = os.path.join(ROOT, "dataset/meta/tags_orphan.json")
AUDIT = os.path.join(ROOT, "dataset/meta/tag_relocations.jsonl")


def segs(tag: str) -> list:
    return tag.split(" / ")


def norm(s: str) -> str:
    s = s.strip().replace("《", "").replace("》", "")
    return re.sub(r"[（(].*?[)）]", "", s).strip()


def pick(old: str, cands: list) -> str:
    if len(cands) == 1:
        return cands[0]
    o = segs(old)

    def score(c):
        cs = segs(c)
        s = 100 if cs[0] == o[0] else 0
        return s + len(set(cs[:-1]) & set(o[:-1]))

    return max(cands, key=score)


def merge_into(dst: dict, key: str, imgs: list):
    tgt = dst.setdefault(key, [])
    have = {e["sha256"] for e in tgt}
    for e in imgs:
        if e["sha256"] not in have:
            tgt.append(e)
            have.add(e["sha256"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", default=os.path.join(ROOT, "data", "instances_meta.json"))
    args = ap.parse_args()

    tags = json.load(open(TAGS, encoding="utf-8")) if os.path.exists(TAGS) else {}

    def tag_of(category: str, name: str) -> str:
        cat = category or ""
        if cat.startswith(ROOT_NAME):
            cat = cat[len(ROOT_NAME):]
        return f"{cat} / {name}" if cat else name

    v3 = json.load(open(args.taxonomy, encoding="utf-8")).get("instances") or []
    v3tags = {tag_of(it.get("category", ""), it["name"]) for it in v3 if it.get("name")}
    by_norm = {}
    for it in v3:
        nm = it.get("name")
        if not nm:
            continue
        by_norm.setdefault(norm(nm), []).append(tag_of(it.get("category", ""), nm))

    store = json.load(open(ORPHAN, encoding="utf-8")) if os.path.exists(ORPHAN) else {}

    # 1) 重挂：隔离区 -> 主索引
    reloc = {}
    for t in list(store):
        if t in v3tags:
            reloc[t] = t
            continue
        cands = [c for c in by_norm.get(norm(segs(t)[-1]), []) if c in v3tags]
        if cands:
            reloc[t] = pick(t, cands)
    for old, new in reloc.items():
        merge_into(tags, new, store.pop(old))

    # 2) 隔离：主索引 -> 隔离区
    quar = {t: tags[t] for t in tags if t not in v3tags}
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
        shutil.copy2(TAGS, TAGS + ".bak-sync")
        json.dump(tags, open(TAGS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(store, open(ORPHAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "action": "orphan_tag_sync",
                "taxonomy": os.path.basename(args.taxonomy),
                "relinked": reloc,
                "quarantined": sorted(quar),
                "images_touched": n_touched,
            }, ensure_ascii=False) + "\n")

    print(f"重挂 {len(reloc)} | 新隔离 {len(quar)} | 主索引 {len(tags)} 键 | 隔离区 {len(store)} 键"
          f" | images.jsonl 更新 {n_touched} 条")


if __name__ == "__main__":
    main()
