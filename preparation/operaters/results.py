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

    return {
        "row_id": str(row.get("task_id") or row.get("unit_id") or row.get("concept")
                   or digest(row)[:20]),
        "stage": stage,
        "concept": row.get("concept"),
        "status": row.get("status"),
        "upstream_ref": json.dumps(upstream_identity, ensure_ascii=False, sort_keys=True)
                        if upstream_identity else None,
        "asset_shas": shas or None,
        "review_json": json.dumps(review, ensure_ascii=False, sort_keys=True) if review else None,
        "payload": json.dumps(row, ensure_ascii=False, sort_keys=True),
        "migrated_at_us": _dt.datetime.fromtimestamp(migrated_us / 1_000_000, tz=_dt.timezone.utc),
    }

def from_stage_row(stage_row):
    """阶段表行 → 业务行（payload 逐字还原；无目录哈希冒充内容身份）。"""
    return json.loads(stage_row["payload"])

class EncodeStage:
    def __init__(self, name): self.name = name
    def __call__(self, row): return to_stage_row(row, self.name, None, 0)


from demiflow.lance.refs import DatasetRef
from project import resolve_root
from preparation.operaters.article import article_entity, write_articles
from preparation.operaters.images import assessment, identity, write_curation
from preparation.operaters.runfiles import read_stage, run_manifest, run_records, stage_ref


def save_results(run, kind, *, target_uri=None, write_mode='merge'):
    """按指定模式写结果，源阶段、目标和模式共同固定；完成的结果不重复提交。"""
    if write_mode not in {'merge', 'append', 'overwrite'}:
        raise ValueError('write_mode must be merge, append or overwrite')
    if kind not in {'article', 'visual'}:
        raise ValueError('Expected article or visual materials')
    stage = 'visual_image_meta' if kind == 'visual' else 'knowledge_base'
    source = stage_ref(run, stage)
    if not source.row_count and write_mode == 'merge':
        return None
    raw = None
    if kind == 'visual':
        from collect.material_schema import IMAGES_URI
        manifest = run_manifest(run)
        raw = manifest.get('config', {}).get('raw_images_ref') or next(
            (s['dataset_ref'] for s in manifest.get('sources', [])
             if s.get('kind') == 'legacy_images' or s.get('dataset_ref', {}).get('relative_uri') == IMAGES_URI), None)
        if raw is None:
            raise ValueError('Visual results require their frozen raw image source')
    binding = {'stage': source.to_dict(), 'raw_source': raw,
               'target_uri': str(target_uri) if target_uri else None, 'write_mode': write_mode}
    store = run_records(run)
    key = 'results/' + kind
    prior = store.get(key)
    if prior is not None:
        if prior['binding'] != binding:
            raise ValueError('Saved result stage changed; use a new run')
        ref = DatasetRef.from_dict(prior['dataset_ref'])
        ref.open(resolve_root())
        return ref
    # A run-scoped selection, not a separately managed publication lifecycle.
    selection = kind + '_' + identity(str(Path(run).resolve()))[:24]
    rows = list(read_stage(run, stage).iter_rows())
    if kind == 'visual':
        values = [{'sha256': row['sha256'], 'concept_assessments': [
            assessment(row, selection, run_id=str(run))]} for row in rows]
        ref = write_curation(resolve_root(), values, source_ref=raw, target_uri=target_uri, write_mode=write_mode)
    else:
        ref = write_articles(resolve_root(), [
            article_entity(row, release_id=selection, run_id=str(run)) for row in rows],
            target_uri=target_uri, write_mode=write_mode)
    store.put(key, {'binding': binding, 'dataset_ref': ref.to_dict(), 'release_id': selection})
    return ref
