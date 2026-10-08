"""显式重置零阶段、零请求、零发布的材料 run；已有工作必须保留。"""
from pathlib import Path
import re
import time
import uuid
from demiflow.execution.artifacts import immutable, run_lock
from demiflow.lance.legacy import scan_legacy_records
from .runfiles import run_records


def reset_empty_run(run, *, actor, reason):
    if not actor.strip() or not reason.strip():
        raise ValueError('actor and reason are required')
    run = Path(run).resolve()
    store = run_records(run)
    # Same lock as article/image/benchmark/training/evaluation entry points.
    with run_lock(run.parent / '_demiflow' / run.name):
        manifest = store.load_manifest()
        if manifest is None:
            raise ValueError('Run has no manifest')
        legacy = store.root / store.legacy_uri
        scaffolds = {'manifest', 'pipeline_source', 'prompt_config', 'judge_config', 'split_registry', 'output', 'image_filter_policy'}
        if legacy.exists():
            import lance
            for key, _, _ in scan_legacy_records(store.root, store.legacy_uri, version=lance.dataset(str(legacy)).version):
                if key not in scaffolds:
                    raise ValueError('Run is not empty: legacy ' + key)
        allowed = {name + '.json' for name in scaffolds} | {'manifest_history', 'configuration_history'}
        if store.directory.exists():
            for path in store.directory.iterdir():
                if path.name not in allowed:
                    raise ValueError('Run has activity or unaccounted files: ' + str(path))
        pattern = re.compile(r'(?:^|__)' + re.escape(store.relative.name) + r'(?:__|\.|$)')
        for path in (store.root / store.relative.parent).iterdir():
            if pattern.search(path.name) and path != legacy:
                # A run identity is not a data directory. Only an empty directory
                # is harmless; calls, stages, publication receipts or logs block.
                if path == store.root / store.relative and path.is_dir() and not any(path.iterdir()):
                    continue
                raise ValueError('Run has durable artifacts: ' + str(path))
        control = run.parent / '_demiflow' / run.name
        if any(path.name != '.lock' for path in control.iterdir()):
            raise ValueError('Run has control artifacts')
        operation = uuid.uuid4().hex
        archive = store.root / '_demiflow' / 'run_resets' / store.relative / operation
        receipt = {'operation_id': operation, 'actor':actor, 'reason':reason, 'time':time.time(),
                   'run':str(run), 'manifest':manifest}
        immutable(archive / 'planned.json', receipt)
        if store.directory.exists():
            store.directory.rename(archive / 'scaffold')
        if legacy.exists():
            legacy.rename(archive / 'legacy_metadata.lance')
        immutable(archive / 'complete.json', receipt)
        return {'operation_id':operation, 'archive':str(archive), 'actor':actor, 'reason':reason}
