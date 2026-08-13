"""Lance / LanceDB 存储层（在现有数据湖之上做可查询索引）。

定位：本系统真正的"存储"仍是磁盘上的内容寻址 blob（dataset/blobs/<aa>/<sha>.<ext>）
+ 主清单 meta/images.jsonl。Lance 在这里扮演【元数据列式索引 + 查询引擎】角色：

  - 把 meta/images.jsonl（已按 sha256 跨批次去重的全局清单）镜像成 Lance 数据集
    meta/.lancedb/images，字段含 宽/高/源/许可证/授权标志/上游名次+分数/tags/path。
  - 选图过滤（≥768×768、按 source / license / source_authorized 切片、按
    source_rank/source_score 排序、限定张数）以 Lance SQL 表达式在数据集上做
    SELECT，返回命中的记录（含指向磁盘 blob 的 path），下游据此读取原图。
  - 与 DuckDB 不冲突：Lance 是列存+向量友好的存储/扫描层，DuckDB 可 `SELECT ... FROM
    read_lance(...)` 对其跑完整 SQL（含多表 JOIN）。二者是搭档，不是二选一。

为什么只存元数据、不把原图塞进 Lance：blob 已是内容寻址、可断点续采的权威存储；
把 GB 级原图二进制复制进 Lance 只会重复占盘。需要"以图搜图"时，再向本数据集加一个
nullable 的 embedding 向量列即可（见 build(..., with_blobs=False) 注释）。

用法（CLI）：
  python -m scripts.multimodal.lancedb_store --meta-dir dataset/meta --build
  python -m scripts.multimodal.lancedb_store --meta-dir dataset/meta --count
  python -m scripts.multimodal.lancedb_store --meta-dir dataset/meta \
      --min-w 768 --min-h 768 --source wikimedia inaturalist \
      --only-authorized --order-by "source_rank" --limit 20 --out sel.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Iterable, List, Optional

import lancedb
import pyarrow as pa


# Lance 数据集 schema：与 meta/images.jsonl 的记录字段对齐。
# 允许 None（旧记录可能缺 actual_width 等），因此整型/浮点用 nullable。
SCHEMA = pa.schema([
    ("sha256", pa.string()),
    ("ext", pa.string()),
    ("source", pa.string()),
    ("source_kind", pa.string()),
    ("source_authorized", pa.bool_()),
    ("license", pa.string()),
    ("author", pa.string()),
    ("credit", pa.string()),
    ("width", pa.int64()),
    ("height", pa.int64()),
    ("orig_width", pa.int64()),
    ("orig_height", pa.int64()),
    ("size_bytes", pa.int64()),
    ("mime", pa.string()),
    ("tags", pa.list_(pa.string())),
    ("tiers", pa.list_(pa.int64())),
    ("source_rank", pa.int64()),
    ("source_score", pa.float64()),
    ("landing_url", pa.string()),
    ("fetched_at", pa.float64()),
    ("path", pa.string()),
])


def _db_path(meta_dir: str) -> str:
    return os.path.join(meta_dir, ".lancedb")


def _read_manifest(meta_dir: str) -> Iterable[dict]:
    mpath = os.path.join(meta_dir, "images.jsonl")
    if not os.path.exists(mpath):
        return
    with open(mpath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def build(meta_dir: str, mode: str = "overwrite",
          with_blobs: bool = False) -> "lancedb.table.Table":
    """从 meta/images.jsonl 重建 Lance 数据集。

    with_blobs=True 时额外把磁盘原图二进制读入 "blob" 列（BINARY），使 Lance 成为
    自包含存储（适合小批量/需整体搬运的场景）；默认 False，仅索引、原图仍在磁盘 blob。
    mode="overwrite" 重建；"append" 增量（需与现有 schema 兼容）。
    """
    rows: List[dict] = list(_read_manifest(meta_dir))
    if not rows:
        raise RuntimeError(f"主清单为空或无记录: {os.path.join(meta_dir, 'images.jsonl')}")

    if with_blobs:
        lake_root = os.path.dirname(os.path.normpath(meta_dir))
        for r in rows:
            sha = r.get("sha256") or ""
            ext = r.get("ext") or ""
            blob_path = os.path.join(lake_root, "blobs", sha[:2], sha + ext) if sha else ""
            data = b""
            try:
                if blob_path and os.path.exists(blob_path):
                    with open(blob_path, "rb") as bf:
                        data = bf.read()
            except Exception:  # noqa: BLE001
                data = b""
            r["blob"] = data
        # schema 需加入 blob 列
        blob_schema = SCHEMA.append(pa.field("blob", pa.binary()))
    else:
        blob_schema = SCHEMA

    db = lancedb.connect(_db_path(meta_dir))
    table = db.create_table("images", data=rows, schema=blob_schema, mode=mode)
    print(f"[lance] 数据集已{'重建' if mode=='overwrite' else '更新'}: "
          f"{len(rows)} 行 -> {_db_path(meta_dir)}/images")
    return table


def open_table(meta_dir: str) -> "lancedb.table.Table":
    db = lancedb.connect(_db_path(meta_dir))
    return db.open_table("images")


def build_where(min_w: Optional[int] = None, min_h: Optional[int] = None,
                sources: Optional[List[str]] = None,
                source_authorized: Optional[bool] = None,
                license_in: Optional[List[str]] = None,
                extra: Optional[str] = None) -> Optional[str]:
    """拼装 Lance 过滤表达式（SQL WHERE 子集）。返回 None 表示不过滤。"""
    conds: List[str] = []
    if min_w:
        conds.append(f"width >= {int(min_w)}")
    if min_h:
        conds.append(f"height >= {int(min_h)}")
    if sources:
        lst = ", ".join(f"'{s}'" for s in sources)
        conds.append(f"source IN ({lst})")
    if source_authorized is not None:
        conds.append(f"source_authorized = {str(bool(source_authorized)).lower()}")
    if license_in:
        lst = ", ".join(f"'{l}'" for l in license_in)
        conds.append(f"license IN ({lst})")
    if extra:
        conds.append(extra)
    return " AND ".join(conds) if conds else None


def select(meta_dir: str, where: Optional[str] = None,
           order_by: Optional[str] = None, limit: Optional[int] = None,
           columns: Optional[List[str]] = None) -> List[dict]:
    """在 Lance 数据集上做 SELECT，返回命中记录（dict 列表）。

    使用底层 LanceDataset 的 to_table(filter=, sort=, limit=, columns=)。
    """
    tbl = open_table(meta_dir)
    ds = tbl.to_lance()
    arrow = ds.to_table(filter=where, columns=columns, limit=limit)
    rows = arrow.to_pylist()
    # 排序在 Python 侧做（数据集规模小，避免依赖各版本 Lance 的 sort API 差异）。
    # 支持 "col" / "col ASC" / "col DESC"。None 值统一排末尾。
    if order_by:
        import re as _re
        m = _re.match(r"\s*(\w+)\s*(ASC|DESC)?\s*$", order_by, _re.IGNORECASE)
        if m:
            col, desc = m.group(1), (m.group(2) or "ASC").upper() == "DESC"
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col) or 0),
                      reverse=desc)
    return rows


def count(meta_dir: str, where: Optional[str] = None) -> int:
    tbl = open_table(meta_dir)
    ds = tbl.to_lance()
    return ds.scanner(filter=where).count_rows()


def _main():
    ap = argparse.ArgumentParser(description="Lance 存储层：构建/查询数据湖索引")
    ap.add_argument("--meta-dir", required=True, help="数据湖 meta 目录（含 images.jsonl）")
    ap.add_argument("--build", action="store_true", help="从 images.jsonl 重建 Lance 数据集")
    ap.add_argument("--with-blobs", action="store_true", help="把原图二进制也读入 blob 列")
    ap.add_argument("--count", action="store_true", help="打印行数（可配合 --where）")
    ap.add_argument("--where", default=None, help="Lance 过滤表达式")
    ap.add_argument("--order-by", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-w", type=int, default=None)
    ap.add_argument("--min-h", type=int, default=None)
    ap.add_argument("--source", action="append", default=None)
    ap.add_argument("--license", action="append", default=None, help="许可证命中列表")
    ap.add_argument("--only-authorized", action="store_true")
    ap.add_argument("--columns", default=None, help="逗号分隔的列名")
    ap.add_argument("--out", default=None, help="把命中记录写到该 JSONL")
    args = ap.parse_args()

    if args.build:
        build(args.meta_dir, mode="overwrite", with_blobs=args.with_blobs)

    if args.count:
        where = args.where or build_where(
            min_w=args.min_w, min_h=args.min_h, sources=args.source,
            source_authorized=(True if args.only_authorized else None),
            license_in=args.license)
        print("count =", count(args.meta_dir, where))
        return

    # 默认走查询路径（仅在显式带查询意图时才 SELECT，避免无参数时全量dump）
    has_query = (
        args.where or args.order_by or args.limit is not None
        or args.min_w or args.min_h or args.source or args.license
        or args.columns or args.out or args.only_authorized
    )
    if not has_query:
        if not args.build:
            print("[lance] 未指定查询参数；仅 --build 时重建数据集，未执行查询。")
        return

    where = args.where or build_where(
        min_w=args.min_w, min_h=args.min_h, sources=args.source,
        source_authorized=(True if args.only_authorized else None),
        license_in=args.license)
    cols = args.columns.split(",") if args.columns else None
    rows = select(args.meta_dir, where=where, order_by=args.order_by,
                  limit=args.limit, columns=cols)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[lance] 命中 {len(rows)} 条 -> {args.out}")
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    _main()
