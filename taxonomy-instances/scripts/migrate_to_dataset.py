#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把现有散落的图片存储迁移到数据湖布局 dataset/（见 docs/IP图片数据集_存储重设计方案.md）。

分阶段、幂等（可重复运行）：
  1. 建 dataset/{blobs,meta/runs,by_tag}
  2. 搬 blob：images_full / images_unauthorized / images_validate / images_pilot3 /
     images_full_unauthorized 内的真实图片文件并入 dataset/blobs/<aa>/<sha256>.<ext>，
     按文件名（含 sha256）去重（已存在则跳过）。
  3. 重建主清单 dataset/meta/images.jsonl（聚合 data/image_manifest.csv +
     各 data/multimodal*/downloads_success.jsonl，按 sha256 去重合并 tags/tiers；
     仅纳入实际存在于 blobs/ 的图；并补齐 blob 扫描发现的孤儿图）。
  4. 派生 dataset/meta/tags.json，并生成 dataset/by_tag/ 软链树。
  5. 归档：旧顶层 images_* 目录、images_by_tag、data/multimodal* 运行目录
     移入 archive/ 与 dataset/meta/runs/<name>（仅 mv，不删除任何文件）。

用法：
  python3 scripts/migrate_to_dataset.py
  python3 scripts/migrate_to_dataset.py --no-archive   # 只重建，不动旧目录
"""

import argparse
import csv
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm_tier(v):
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    return str(v).strip().lower() in ("true", "1", "yes")


def _phase_move_blobs(blobs_root):
    """Phase 2: 把各旧图库的真实图片文件并入 dataset/blobs。"""
    src_dirs = [
        "images_full",
        "images_unauthorized",
        "images_validate",
        "images_pilot3",
        "images_full_unauthorized",
    ]
    moved = skipped = 0
    for sd in src_dirs:
        sp = os.path.join(ROOT, sd)
        if not os.path.isdir(sp):
            continue
        for root, _, files in os.walk(sp):
            for fn in files:
                if fn.lower().endswith(".bin"):
                    # 历史上误命名为 .bin 的文件：按真实格式已在前序步骤修正；
                    # 若仍有 .bin 遗留，按 sha[:2] 直接搬（保留原扩展名）。
                    pass
                sha = fn.split(".", 1)[0]
                aa = sha[:2]
                tdir = os.path.join(blobs_root, aa)
                tgt = os.path.join(tdir, fn)
                if os.path.exists(tgt):
                    skipped += 1
                    continue
                os.makedirs(tdir, exist_ok=True)
                os.rename(os.path.join(root, fn), tgt)
                moved += 1
    print(f"[phase2] blobs 搬移: moved={moved} skipped(已存在)={skipped}")
    return moved, skipped


def _lookup_blob(blobs_root, sha):
    aa = sha[:2]
    matches = glob.glob(os.path.join(blobs_root, aa, sha + ".*"))
    matches = [m for m in matches if not os.path.islink(m)]
    if not matches:
        return None, None
    fn = os.path.basename(matches[0])
    ext = fn.rsplit(".", 1)[1] if "." in fn else ""
    return ext, os.path.join("blobs", aa, fn)


def _blank_record(sha, ext, path):
    return {
        "sha256": sha, "ext": ext, "source": "", "source_kind": "",
        "source_authorized": None, "license": "", "author": "", "credit": "",
        "width": None, "height": None, "orig_width": None, "orig_height": None,
        "size_bytes": None, "mime": "", "tags": [], "tiers": [],
        "landing_url": "", "fetched_at": None, "path": path,
    }


def _apply(rec, n):
    if n.get("tag") and n["tag"] not in rec["tags"]:
        rec["tags"].append(n["tag"])
    if n.get("tier") is not None and n["tier"] not in rec["tiers"]:
        rec["tiers"].append(n["tier"])
    for fld in ("source", "source_kind", "license", "author", "credit",
                "mime", "landing_url"):
        if not rec.get(fld) and n.get(fld):
            rec[fld] = n[fld]
    for fld in ("width", "height", "orig_width", "orig_height", "size_bytes",
                "fetched_at", "source_authorized"):
        if rec.get(fld) is None and n.get(fld) is not None:
            rec[fld] = n[fld]


def _phase_build_manifest(meta_root, blobs_root):
    """Phase 3: 聚合 CSV + 各 downloads_success.jsonl 重建 images.jsonl。"""
    merged = {}

    # --- JSONL 来源（更丰富，优先）---
    for jf in glob.glob(os.path.join(ROOT, "data", "multimodal*", "downloads_success.jsonl")):
        if not os.path.exists(jf):
            continue
        with open(jf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sha = d.get("sha256")
                if not sha:
                    continue
                ext, path = _lookup_blob(blobs_root, sha)
                if ext is None:
                    continue
                n = {
                    "tag": d.get("tag", ""),
                    "tier": _norm_tier(d.get("selected_tier")),
                    "source": d.get("source", ""),
                    "source_kind": d.get("source_kind", ""),
                    "source_authorized": d.get("source_authorized"),
                    "license": d.get("license_raw") or "",
                    "author": d.get("author") or "",
                    "credit": d.get("credit") or "",
                    "width": d.get("actual_width"),
                    "height": d.get("actual_height"),
                    "orig_width": d.get("orig_width"),
                    "orig_height": d.get("orig_height"),
                    "size_bytes": d.get("actual_size"),
                    "mime": d.get("actual_mime") or d.get("declared_mime") or "",
                    "landing_url": d.get("landing_url") or "",
                    "fetched_at": d.get("fetched_at"),
                }
                rec = merged.get(sha) or _blank_record(sha, ext, path)
                _apply(rec, n)
                merged[sha] = rec

    # --- CSV 主清单（补缺口）---
    csv_path = os.path.join(ROOT, "data", "image_manifest.csv")
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sha = (row.get("sha256") or "").strip()
                if not sha:
                    continue
                ext, path = _lookup_blob(blobs_root, sha)
                if ext is None:
                    continue
                n = {
                    "tag": row.get("tag", ""),
                    "tier": _norm_tier(row.get("selected_tier")),
                    "source": row.get("source", ""),
                    "source_kind": row.get("source_kind", ""),
                    "source_authorized": _to_bool(row.get("source_authorized")),
                    "license": row.get("license", ""),
                    "author": row.get("author", ""),
                    "credit": "",
                    "width": _to_int(row.get("width")),
                    "height": _to_int(row.get("height")),
                    "orig_width": _to_int(row.get("orig_width")),
                    "orig_height": _to_int(row.get("orig_height")),
                    "size_bytes": _to_int(row.get("size_bytes")),
                    "mime": row.get("mime", ""),
                    "landing_url": "",
                    "fetched_at": None,
                }
                rec = merged.get(sha) or _blank_record(sha, ext, path)
                _apply(rec, n)
                merged[sha] = rec

    # --- 补齐 blob 扫描发现的孤儿图（不在任何 manifest 中）---
    orphans = 0
    for root, _, files in os.walk(blobs_root):
        for fn in files:
            sha = fn.split(".", 1)[0]
            if sha in merged:
                continue
            ext = fn.rsplit(".", 1)[1] if "." in fn else ""
            rel = os.path.relpath(os.path.join(root, fn), ROOT)
            merged[sha] = _blank_record(sha, ext, rel)
            orphans += 1

    out = os.path.join(meta_root, "images.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for sha in sorted(merged):
            f.write(json.dumps(merged[sha], ensure_ascii=False) + "\n")

    # tags.json
    tags = {}
    for rec in merged.values():
        for t in rec.get("tags", []):
            tags.setdefault(t, []).append({
                "sha256": rec["sha256"],
                "ext": rec.get("ext", ""),
                "source": rec.get("source", ""),
                "tiers": rec.get("tiers", [0]),
            })
    with open(os.path.join(meta_root, "tags.json"), "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=1)

    print(f"[phase3] images.jsonl 记录数={len(merged)} (含孤儿 {orphans})；tags.json tag 数={len(tags)}")
    return len(merged)


def _phase_archive(no_archive):
    """Phase 5: 归档旧目录（仅 mv，不删除）。"""
    if no_archive:
        print("[phase5] --no-archive：跳过归档")
        return
    archive = os.path.join(ROOT, "archive")
    os.makedirs(archive, exist_ok=True)

    # 旧顶层 images_* + images_by_tag
    for d in ("images_full", "images_unauthorized", "images_validate",
              "images_pilot3", "images_full_unauthorized", "images_by_tag"):
        sp = os.path.join(ROOT, d)
        if os.path.isdir(sp) and not os.path.islink(sp):
            dst = os.path.join(archive, d)
            if not os.path.exists(dst):
                os.rename(sp, dst)
                print(f"[phase5] 归档 {d} -> archive/{d}")

    # 旧 master CSV / README（迁到 meta 快照 / archive）
    snap = os.path.join(ROOT, "dataset", "meta", "images_manifest.legacy.csv")
    csvp = os.path.join(ROOT, "data", "image_manifest.csv")
    if os.path.exists(csvp) and not os.path.exists(snap):
        shutil.copy2(csvp, snap)
        print(f"[phase5] 快照 master CSV -> dataset/meta/images_manifest.legacy.csv")

    old_readme = os.path.join(ROOT, "data", "image_collection_README.md")
    if os.path.exists(old_readme):
        dst = os.path.join(archive, "image_collection_README.md")
        if not os.path.exists(dst):
            os.rename(old_readme, dst)
            print("[phase5] 归档 data/image_collection_README.md -> archive/")

    # data/multimodal* 运行目录 -> dataset/meta/runs/<name>
    runs_dir = os.path.join(ROOT, "dataset", "meta", "runs")
    os.makedirs(runs_dir, exist_ok=True)
    for sp in glob.glob(os.path.join(ROOT, "data", "multimodal*")):
        if not os.path.isdir(sp):
            continue
        name = os.path.basename(sp)
        dst = os.path.join(runs_dir, name)
        if not os.path.exists(dst):
            os.rename(sp, dst)
            print(f"[phase5] 迁移运行目录 {name} -> dataset/meta/runs/{name}")

    # 让 _latest 指向最新的完整验证运行（若存在）
    latest_src = os.path.join(runs_dir, "multimodal_verify_cn2")
    latest = os.path.join(runs_dir, "_latest")
    if os.path.isdir(latest_src):
        if os.path.lexists(latest):
            os.remove(latest)
        os.symlink("multimodal_verify_cn2", latest)
        print("[phase5] runs/_latest -> multimodal_verify_cn2")


def _write_dataset_readme():
    p = os.path.join(ROOT, "dataset", "README.md")
    if os.path.exists(p):
        return
    content = """# IP 图片数据集（数据湖布局）

由 `scripts/migrate_to_dataset.py` 从旧布局迁移而来。结构见 `docs/IP图片数据集_存储重设计方案.md`。

- `blobs/`：原始图片字节，内容寻址 `blobs/<aa>/<sha256>.<ext>`，全源统一、去重、不可变。
- `meta/images.jsonl`：主清单，每张已存图一行（按 sha256 去重）。
- `meta/tags.json`：tag↔图 关系索引。
- `meta/runs/<run_id>/`：各批次流水线过程产物；`meta/runs/_latest` 指向最新批次。
- `by_tag/`：按标签的软链浏览树（由 `meta/tags.json` 派生，可随时重建）。

重建 `by_tag/`：`python3 scripts/link_by_tag.py`
"""
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-archive", action="store_true",
                    help="只重建 dataset/，不归档/迁移旧目录")
    args = ap.parse_args()

    blobs_root = os.path.join(ROOT, "dataset", "blobs")
    meta_root = os.path.join(ROOT, "dataset", "meta")
    by_tag_root = os.path.join(ROOT, "dataset", "by_tag")
    os.makedirs(blobs_root, exist_ok=True)
    os.makedirs(meta_root, exist_ok=True)
    os.makedirs(by_tag_root, exist_ok=True)

    _phase_move_blobs(blobs_root)
    n = _phase_build_manifest(meta_root, blobs_root)

    # Phase 4: 重建 by_tag 软链树
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import link_by_tag
    link_by_tag.link_from_tags(
        os.path.join(meta_root, "tags.json"), by_tag_root, blobs_root)

    _write_dataset_readme()
    _phase_archive(args.no_archive)

    print(f"\n迁移完成：dataset/blobs 图片总数={n}（见 dataset/meta/images.jsonl）")


if __name__ == "__main__":
    main()
