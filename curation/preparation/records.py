"""Immutable inputs and revisioned demiflow checkpoints."""
import json
import re
from pathlib import Path


from demiflow.standalone import local_data
from curation.preparation.contracts import ROOT, digest, immutable, read, run_lock


def rows(path):
    if isinstance(path, dict):
        from demiflow.lance.refs import DatasetRef
        from project import resolve_root
        ref = DatasetRef.from_dict(path.get('dataset_ref', path))
        for batch in ref.open(resolve_root()).to_batches():
            for row in batch.to_pylist():
                yield from_stage_row(row) if ref.schema_name == 'pipeline_stage_rows' else row
        return
    raise TypeError('Pipeline data requires a fixed Lance DatasetRef; import source files explicitly')


def file_record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Use a nonempty filesystem-safe task ID")
    return value


def code_version():
    import demiflow
    directory = Path(__file__).parent.parent
    files = [p for branch in ("preparation", "benchmark", "training", "evaluation") for p in (directory / branch).rglob("*") if (p.suffix in {".py", ".yaml"} or p.suffix == ".md" and p.parent.name == "prompts")
             and not {"tests", "validation", "reviews", "__pycache__"} & set(p.relative_to(directory).parts)]
    files += [directory / n for n in ("preparation/contracts.py", "preparation/ops/visual_materials.py")]
    files += [directory / "preparation/annotation_contracts.py"]
    business_files = [ROOT / "project.py", ROOT / "collect/assets.py",
                      ROOT / "collect/schemas.py", ROOT / "collect/material_schema.py", ROOT / "collect/materials.py", ROOT / "collect/material_writer.py",
                      ROOT / "collect/concepts.py",
                      ROOT / "curation/preparation/schemas.py", ROOT / "curation/preparation/images.py", ROOT / "curation/preparation/image_schema.py", ROOT / "curation/preparation/articles.py", ROOT / "curation/preparation/publication.py", ROOT / "curation/preparation/configs/image_annotation_v2.json", directory / "preparation/asset_io.py"]
    return {"code": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(files) if p.is_file()},
            "business": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in business_files},
            "demiflow": digest({str(p.relative_to(Path(demiflow.__file__).parent)): digest(p.read_bytes())
                                for p in sorted(Path(demiflow.__file__).parent.rglob("*.py")) if p.is_file()})}


def inventory(directory):
    directory = Path(directory).resolve()
    return [file_record(p) for p in sorted(directory.glob("*.json"))]


from demiflow.lance.run import LanceRun


def run_relative(run):
    parts = Path(run).resolve().parts
    branches = {'preparation', 'benchmark', 'training', 'evaluation'}
    for at in range(len(parts) - 3):
        if parts[at] == 'curation' and parts[at + 1] in branches and parts[at + 2] == 'runs':
            return 'runs/pipeline/' + parts[at + 1] + '/' + '/'.join(parts[at + 3:])
    raise ValueError('Run belongs under curation/<pipeline>/runs/<run>')


def run_records(run):
    from demiflow.lance.records import LanceRecordStore
    from project import resolve_root
    return LanceRecordStore(resolve_root(), run_relative(run) + '/metadata.lance')


def run_state(run):
    state = run_records(run).get('latest')
    if state is None: raise ValueError('Run has no committed stages')
    return state


def run_manifest(run):
    manifest = run_records(run).get('manifest')
    if manifest is None: raise ValueError('Run manifest missing')
    return manifest


class RunFiles(LanceRun):
    """V2 business binding: schema, row mapping, branch and dataset-root policy."""
    def initialize_business(self):
        from project import resolve_root
        from curation.preparation.schemas import PIPELINE_STAGE_ROWS, SCHEMA_VERSION
        self.initialize(root=resolve_root(), relative=run_relative(self.run), manifest=self.manifest,
                        schema=PIPELINE_STAGE_ROWS, schema_name='pipeline_stage_rows',
                        schema_version=SCHEMA_VERSION, encode_row=to_stage_row, decode_row=from_stage_row)

# ---------------------------------------------------------------- Lance 阶段

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


def stage_schema():
    from curation.preparation.schemas import PIPELINE_STAGE_ROWS

    return PIPELINE_STAGE_ROWS


def read_stage_ref(run, name):
    """latest.json 里的阶段 DatasetRef（固定版本；不存在返回 None）。"""
    state = run_state(run)
    record = state["stages"].get(name) or {}
    ref = record.get("dataset_ref")
    return ref if isinstance(ref, dict) and ref.get("relative_uri") else None


def open_stage_dataset(run, name, data_api=None):
    """按 DatasetRef 固定版本打开阶段表（demiflow Dataset）。"""
    ref = read_stage_ref(run, name)
    if ref is None:
        raise ValueError("Stage has no dataset_ref: " + str(name))
    if data_api is None:
        data_api = local_data()
    from project import resolve_root

    return data_api.read_lance(str(resolve_root() / ref["relative_uri"]),
                               version=ref["lance_version"])


def saved_stage(run, name):
    state = run_state(run)
    record = state["stages"][name]
    ref = record.get("dataset_ref")
    if isinstance(ref, dict) and ref.get("relative_uri"):
        # Lance 阶段：固定版本读取，payload 还原业务行
        rows_out = []
        from project import resolve_root
        import lance as _lance

        from demiflow.lance.refs import DatasetRef
        ds = DatasetRef.from_dict(ref).open(resolve_root())
        for stage_row in ds.to_table().to_pylist():
            rows_out.append(from_stage_row(stage_row))
        return rows_out
    raise ValueError('Stage has no fixed Lance reference: ' + name)




def prompt_store(run, purpose='offline'):
    from project import resolve_root
    return {'root': str(resolve_root()), 'relative_uri': run_relative(run) + '/model/' + purpose + '.lance'}


def read_record(ref):
    from demiflow.lance.records import RecordRef
    from project import resolve_root
    return RecordRef.from_dict(ref).read(resolve_root())


def response_records(run, stage, task_prefix=None):
    from demiflow.lance.records import LanceRecordStore
    store = LanceRecordStore(**prompt_store(run))
    result = []
    for key, request in sorted(run_records(run).items(prefix='request/' + stage + '/').items()):
        if not key.startswith('request/' + stage + '/'): continue
        if task_prefix is not None and not request['task_id'].startswith(task_prefix): continue
        binding = request.get('native_offline')
        if not binding: continue
        response_key = 'response/' + read_record(binding['request_ref'])['request_sha256']
        response = store.get(response_key)
        if response is not None:
            result.append({'record_ref': store.reference(response_key).to_dict(), 'sha256': digest(response)})
    return result


def store_blob(run, data):
    from demiflow.lance.blobs import LanceBlobStore
    from project import resolve_root
    return LanceBlobStore(resolve_root(), run_relative(run) + '/assets.lance').put(data)
