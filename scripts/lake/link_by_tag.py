#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按标签目录树为每张图创建软链。

读取 tag↔图 关系索引（dataset/meta/tags.json，由流水线主清单派生），
把实际文件软链进 by_taxonomy/ 视图树。

tag 即实体名（不含路径，见 AGENTS.md 1.5）：路径由本脚本从
taxonomy-instances/data/instances.json 解析（name -> taxonomy_path 列表）。
一个实体可挂多个路径，则每条路径下都建软链。体系外实体（untaxonomy.json）
解析不到路径，直接平铺在输出根下。

每张原图只保留其单一原始分辨率（下载器不缩放），故每个标签目录下每张图
仅生成一个软链，按稳定短 hash 命名（img_<sha[:12]>.jpg），不再按 768/1024/
2048/最大 分档产生多份（那样会让人误以为同一张图被拆成多个分辨率）。

软链为相对路径，指向 blobs/<aa>/<sha256>.<ext>，整树迁移不断链。

用法:
  python3 scripts/lake/link_by_tag.py
  python3 scripts/lake/link_by_tag.py --tags dataset/meta/tags.json --out dataset/by_taxonomy --blobs dataset/blobs
  python3 scripts/lake/link_by_tag.py --tags dataset/meta/untaxonomy.json --out dataset/untaxonomy
"""

import argparse
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TAXONOMY_PATH = os.path.join(ROOT, "taxonomy-instances", "data", "instances.json")
ROOT_NAME = "融合世界标签体系 / "

_NAME_PATHS_CACHE: dict = {}  # (path, mtime, size) -> {name: [taxonomy_path(去根), ...]}


def load_name_paths(path: str) -> dict:
    """从 instances.json 解析 实体名 -> 挂载路径列表（去根前缀）。按 mtime 缓存。"""
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if key in _NAME_PATHS_CACHE:
        return _NAME_PATHS_CACHE[key]
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    m: dict = {}
    for it in doc.get("instances") or []:
        nm = it.get("name")
        if not nm:
            continue
        tp = it.get("taxonomy_path") or ""
        if tp.startswith(ROOT_NAME):
            tp = tp[len(ROOT_NAME):]
        m.setdefault(nm, [])
        if tp and tp not in m[nm]:
            m[nm].append(tp)
    _NAME_PATHS_CACHE[key] = m
    return m


def link_from_tags(tags_path, out_root, blobs_root, name_paths=None):
    if not os.path.exists(tags_path):
        print(f"跳过（不存在）: {tags_path}")
        return 0, 0
    with open(tags_path, encoding="utf-8") as f:
        tags = json.load(f)

    if name_paths is None:
        try:
            name_paths = load_name_paths(TAXONOMY_PATH)
        except OSError:
            name_paths = {}

    if os.path.isdir(out_root):
        shutil.rmtree(out_root)
    os.makedirs(out_root, exist_ok=True)

    n_link = 0
    n_dir = 0
    seen_dirs = set()

    for tag, images in tags.items():
        paths = name_paths.get(tag) or []
        # 实体名可能含 "/"（如 "22/7"）：叶子目录名换成全角 ／，避免被当成嵌套路径。
        leaf = tag.replace("/", "／")
        dirs = ([os.path.join(out_root, *p.split(" / "), leaf) for p in paths]
                if paths else [os.path.join(out_root, leaf)])
        for d in dirs:
            if d not in seen_dirs:
                os.makedirs(d, exist_ok=True)
                seen_dirs.add(d)
                n_dir += 1
            seen_sha = set()
            for img in images:
                sha = img.get("sha256", "")
                if not sha or sha in seen_sha:
                    # 同一标签下同一张图只建一个软链（即便来自多个来源）。
                    continue
                seen_sha.add(sha)
                ext = img.get("ext", "") or "jpg"
                blob = os.path.join(blobs_root, sha[:2], f"{sha}.{ext}")
                if not os.path.exists(blob):
                    continue
                rel = os.path.relpath(blob, d)
                # 单一原始分辨率 → 单一软链，文件名不体现分辨率档位。
                base = f"img_{sha[:12]}"
                link = os.path.join(d, base + ".jpg")
                if os.path.lexists(link):
                    os.remove(link)
                os.symlink(rel, link)
                n_link += 1

    print(f"{tags_path} -> {out_root}: 目录 {n_dir} 个，软链 {n_link} 条")
    return n_dir, n_link


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", default=os.path.join(ROOT, "dataset", "meta", "tags.json"))
    ap.add_argument("--blobs", default=os.path.join(ROOT, "dataset", "blobs"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dataset", "by_taxonomy"))
    ap.add_argument("--taxonomy", default=TAXONOMY_PATH,
                    help="instances.json（实体名 -> 挂载路径解析源）")
    args = ap.parse_args()

    link_from_tags(args.tags, args.out, args.blobs, load_name_paths(args.taxonomy))


if __name__ == "__main__":
    main()
