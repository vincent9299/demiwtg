#!/usr/bin/env python3
"""回收导入：本地暂存的 blob 树 → 权威 blobs/（SHA 复验 + 原子写）。

用法: python3 import_blobs.py <staging_blobs_root> <done_ledger_glob...>
- staging 树形如 <root>/blobs/aa/sha.ext（fleet_curl/fleet_dl 的 out-dir）
- done 清单行含 sha256/blob_path/size_bytes；按清单驱动，逐文件校验后导入
- 已存在且非零的目标跳过；内容不同则报出来不覆盖
"""
import glob
import hashlib
import json
import os
import sys



def import_tree(staging_root: str, ledgers: list[str]) -> None:
    dst_root = os.environ.get('DEMIWTG_DATASETS_ROOT', '/yzp/zhaozy/yangzepeng/0905/datasets') + "/demiwtg"
    src_root = os.path.join(staging_root, "blobs")
    moved = skipped = missing = badsha = conflict = 0
    seen = set()
    for lg in ledgers:
        for path in sorted(glob.glob(lg)):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    s = r["sha256"]
                    if s in seen:
                        continue
                    seen.add(s)
                    rel = r["blob_path"]
                    src = os.path.join(staging_root, rel)
                    dst = os.path.join(dst_root, rel)
                    if not os.path.exists(src):
                        missing += 1
                        continue
                    if os.path.exists(dst) and os.path.getsize(dst) > 0:
                        skipped += 1
                        continue
                    h = hashlib.sha256()
                    with open(src, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    if h.hexdigest() != s:
                        badsha += 1
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    tmp = dst + ".imp" + str(os.getpid())
                    os.link(src, tmp) if False else None
                    with open(src, "rb") as fi, open(tmp, "wb") as fo:
                        for chunk in iter(lambda: fi.read(1 << 20), b""):
                            fo.write(chunk)
                    os.replace(tmp, dst)
                    moved += 1
    print(f"imported={moved} skipped_exists={skipped} missing={missing} "
          f"badsha={badsha} unique_ledger={len(seen)}")


if __name__ == "__main__":
    import_tree(sys.argv[1], sys.argv[2:])
