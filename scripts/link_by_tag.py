#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 IP 标签目录树为每张图创建软链。

读取 tag↔图 关系索引（dataset/meta/tags.json，由流水线主清单派生），
依 tag 路径（按 " / " 切分）在 by_tag/ 下建多级目录，把实际文件软链进去。

每张原图只保留其单一原始分辨率（下载器不缩放），故每个标签目录下每张图
仅生成一个软链，按稳定短 hash 命名（img_<sha[:12]>.jpg），不再按 768/1024/
2048/最大 分档产生多份（那样会让人误以为同一张图被拆成多个分辨率）。

软链为相对路径，指向 blobs/<aa>/<sha256>.<ext>，整树迁移不断链。

用法:
  python3 scripts/link_by_tag.py
  python3 scripts/link_by_tag.py --tags dataset/meta/tags.json --out dataset/by_tag --blobs dataset/blobs
"""

import argparse
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def link_from_tags(tags_path, out_root, blobs_root):
    if not os.path.exists(tags_path):
        print(f"跳过（不存在）: {tags_path}")
        return 0, 0
    with open(tags_path, encoding="utf-8") as f:
        tags = json.load(f)

    if os.path.isdir(out_root):
        shutil.rmtree(out_root)
    os.makedirs(out_root, exist_ok=True)

    n_link = 0
    n_dir = 0
    seen_dirs = set()

    for tag, images in tags.items():
        comps = [c for c in tag.split(" / ") if c]
        if not comps:
            continue
        d = os.path.join(out_root, *comps)
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
    ap.add_argument("--out", default=os.path.join(ROOT, "dataset", "by_tag"))
    args = ap.parse_args()

    link_from_tags(args.tags, args.out, args.blobs)


if __name__ == "__main__":
    main()
