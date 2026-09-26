"""证据目录 external 模式资产登记（sha256 + stat，不复制字节）。

面向"证据保全"型散落大件（benchmark 媒体、taxonomy 迁移证据等）：
逐文件读字节算 sha256，登记为 storage_mode=external 的资产表
（demiwtg/collect/datasets/evidence_assets__<source>.lance）。来源只读、不移动不删除；
退役删除仍需显式裁决，本登记只提供可核验清单。

用法（需 demiflow 源码仓 + env 组合 PYTHONPATH）：
  python -m tools.lake_migration.register_evidence_assets --source benchmark \
      --repo-root /path/to/demiwtg [--roots benchmark/edit benchmark/t2i ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from project import resolve_root
from tools.lake_migration.write import write_table

_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
_HASH_BATCH = 2048  # 有界批大小：并行 map 保序且内存不随文件总数增长


def hash_file(path_str: str):
    """分块 sha256（单文件不整读入内存，兼容 GB 级大件）。子进程安全。

    返回 (sha, size, error)：成功时 error 为 None，读失败时 sha 为 None。
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path_str, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        return (None, 0, str(exc))
    return (digest.hexdigest(), size, None)


def _walk_files(repo_root: Path, roots):
    for top in roots:
        top_path = (repo_root / top) if not top.startswith("/") else Path(top)
        for dirpath, dirnames, filenames in os.walk(top_path):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = path.relative_to(repo_root).as_posix() if path.is_relative_to(repo_root) else str(path)
                yield path, rel, top


def iter_asset_rows(repo_root: Path, roots, migrated, stats, workers: int = 1):
    def flush(batch, pool):
        if not batch:
            return
        if pool is not None:
            results = pool.map(hash_file, [str(p) for p, _, _ in batch], chunksize=32)
        else:
            results = (hash_file(str(p)) for p, _, _ in batch)
        for (path, rel, top), (sha, size, error) in zip(batch, results):
            if error is not None:
                stats["read_errors"] += 1
                if len(stats["error_samples"]) < 5:
                    stats["error_samples"].append({"path": rel, "error": error})
                continue
            ext = path.suffix.lstrip(".").lower() or "none"
            stats["rows"] += 1
            stats["bytes"] += size
            yield {
                "asset_id": sha, "ext": ext, "byte_size": size,
                "storage_mode": "external", "relative_path": rel,
                "source_file": top, "source_row": -1, "raw_payload": "",
                "migrated_at_us": migrated,
            }

    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        batch = []
        for item in _walk_files(repo_root, roots):
            batch.append(item)
            if len(batch) >= _HASH_BATCH:
                yield from flush(batch, pool)
                batch = []
        yield from flush(batch, pool)
    finally:
        if pool is not None:
            pool.shutdown()


def run(source: str, repo_root: Path, roots, datasets_root=None, workers: int = 1) -> dict:
    root = Path(datasets_root) if datasets_root else resolve_root()
    migrated = int(time.time() * 1_000_000)
    stats = {"rows": 0, "bytes": 0, "read_errors": 0, "error_samples": []}

    def rows_factory():
        yield from iter_asset_rows(repo_root, roots, migrated, stats, workers=workers)

    relative = f"demiwtg/collect/datasets/evidence_assets__{source}.lance"
    # fingerprint 用源清单＋mtime 快照派生；内容变化走新 fingerprint。
    stamp = "|".join(f"{r}:{int((repo_root / r).stat().st_mtime)}" for r in roots)
    ref, rows, replayed = write_table(
        root, relative, schema_name="blob_assets",
        rows_factory=rows_factory,
        fingerprint=f"evidence-assets-v1:{source}:{hashlib.sha256(stamp.encode()).hexdigest()[:16]}",
    )
    return {"source": source, "rows": rows, "replayed": replayed,
            "bytes": stats["bytes"], "read_errors": stats["read_errors"],
            "error_samples": stats["error_samples"], "ref": ref.to_dict()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="证据源标识（表名片段）")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--roots", nargs="+", required=True,
                        help="repo 根下的相对目录列表（或绝对路径）")
    parser.add_argument("--datasets-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=1,
                        help="并行哈希进程数（保序；默认单进程）")
    args = parser.parse_args()
    result = run(args.source, args.repo_root, args.roots,
                 datasets_root=args.datasets_root, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
