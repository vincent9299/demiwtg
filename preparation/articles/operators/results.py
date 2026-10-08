"""Pipeline stage schemas and run bindings over demiflow Lance storage."""
import pyarrow as pa
SCHEMA_VERSION = "v1"

PIPELINE_STAGE_ROWS = pa.schema([
    pa.field("row_id", pa.string(), nullable=False),
    pa.field("stage", pa.string(), nullable=False),
    pa.field("concept", pa.string(), nullable=True),
    pa.field("status", pa.string(), nullable=True),
    pa.field("upstream_ref", pa.large_string(), nullable=True),
    pa.field("asset_shas", pa.list_(pa.string()), nullable=True),
    pa.field("review_json", pa.large_string(), nullable=True),
    pa.field("payload", pa.large_string(), nullable=False),
    pa.field("migrated_at_us", pa.timestamp("us", tz="UTC"), nullable=False),
])


import json
import re
from pathlib import Path


from demiflow.execution.artifacts import digest

def _collect_asset_shas(value, out, budget=64):
    """递归收集行内 64-hex sha256 引用（资产/目标/候选），有界防膨胀。"""
    if len(out) >= budget:
        return
    if isinstance(value, dict):
        sha = value.get("sha256")
        if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
            out.append(sha)
        for key, item in value.items():
            if key not in ("review",):
                _collect_asset_shas(item, out, budget)
    elif isinstance(value, list):
        for item in value:
            _collect_asset_shas(item, out, budget)

def to_stage_row(row, stage, upstream_identity, migrated_us):
    """业务行 → 阶段表行：结构化主键/概念/状态/上游/资产/审核 + 完整行 JSON。"""
    shas = []
    _collect_asset_shas({k: v for k, v in row.items()
                         if k in ("asset", "edit_source", "target", "design_targets",
                                  "source_candidates", "target_pool", "materials",
                                  "answer_materials", "source_asset")}, shas)
    review = {k: row[k] for k in row if "review" in k or k in ("publication", "visual_dependencies")}
    import datetime as _dt

    from demiflow.objects import LocalObjectStore, externalize_data_uris
    from project import resolve_root
    stored_row = externalize_data_uris(row, LocalObjectStore(resolve_root() / 'objects'))
    stored_review = externalize_data_uris(review, LocalObjectStore(resolve_root() / 'objects'))

    return {
        "row_id": str(row.get("task_id") or row.get("unit_id") or row.get("concept")
                   or digest(row)[:20]),
        "stage": stage,
        "concept": row.get("concept"),
        "status": row.get("status"),
        "upstream_ref": json.dumps(upstream_identity, ensure_ascii=False, sort_keys=True)
                        if upstream_identity else None,
        "asset_shas": shas or None,
        "review_json": json.dumps(stored_review, ensure_ascii=False, sort_keys=True) if review else None,
        "payload": json.dumps(stored_row, ensure_ascii=False, sort_keys=True),
        "migrated_at_us": _dt.datetime.fromtimestamp(migrated_us / 1_000_000, tz=_dt.timezone.utc),
    }

def from_stage_row(stage_row):
    """阶段表行 → 业务行（payload 逐字还原；无目录哈希冒充内容身份）。"""
    from demiflow.objects import restore_data_uris
    return restore_data_uris(json.loads(stage_row["payload"]))

class EncodeStage:
    def __init__(self, name): self.name = name
    def __call__(self, row): return to_stage_row(row, self.name, None, 0)

