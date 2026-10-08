"""2026-09-27 三层存储迁移：整表移动，不改行、版本或历史引用。

入口：python -m tools.lake_migration.three_layer_storage --workspace <共同根> --apply。
默认只显示计划；--apply 移动四张 qid 原始表和两张公共样本表及其控制目录，
更新现有精确位置映射，核对文件身份/版本/行数/样本后写控制回执。
用户随后确认 preparation 现有目标表全部迁出；--remaining-preparation-targets
补迁其余两张历史目标表，单独保留回执，不改表结构或内容。
"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
from pathlib import Path

import lance
import pyarrow as pa
from demiflow.lance.control import control_directory, table_lock_path
from demiflow.lance.storage import resolve_local_uri, schema_hash
from tools.lake_migration.flatten_datasets import inventory


PLAN = [
    {'old': f'datasets/{name}.lance', 'new': f'demiwtg/collect/datasets/{name}.lance'}
    for name in ('qid_images_v2', 'qid_edges', 'qid_concepts_fat', 'qid_concept_xref')
] + [
    {'old': f'demiwtg/preparation/datasets/{name}.lance', 'new': f'datasets/{name}.lance'}
    for name in ('images', 'articles')
]

REMAINING_PREPARATION_TARGETS = [
    {'old': f'demiwtg/preparation/datasets/{name}.lance', 'new': f'datasets/{name}.lance'}
    for name in ('knowledge_base__bench200_production_20260920_v8', 'metadata__test__configured_answering')
]


def sample_digest(dataset):
    """只读两行，补充验证移动前后实际数据仍可解码；整表文件身份另作核验。"""
    stream = pa.BufferOutputStream()
    with pa.ipc.new_stream(stream, dataset.schema) as writer:
        writer.write_table(dataset.head(2).combine_chunks())
    return hashlib.sha256(stream.getvalue().to_pybytes()).hexdigest()


def run(workspace, *, apply=False, remaining_preparation_targets=False):
    root = Path(workspace).resolve()
    plan = REMAINING_PREPARATION_TARGETS if remaining_preparation_targets else PLAN
    if not apply:
        return {'apply': False, 'plan': plan}
    manifest = root / '_demiflow/lance_locations.json'
    receipt_name = 'three_layer_storage_20260927' + ('_remaining_preparation' if remaining_preparation_targets else '')
    receipt_dir = root / '_demiflow' / receipt_name
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / 'receipt.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved['plan'] != plan:
            raise ValueError('Completed migration has a different plan')
        for item in saved['tables']:
            if (root / item['old']).exists() or resolve_local_uri(root / item['old']) != root / item['new']:
                raise ValueError('Completed relocation no longer matches the location map')
            for version in item['versions']:
                if lance.dataset(str(root / item['new']), version=version['version']).count_rows() != version['rows']:
                    raise ValueError('A recorded version is no longer readable')
        return {'already_complete': True, 'tables': len(plan), 'receipt': str(receipt)}
    original = manifest.read_bytes() if manifest.exists() else None
    before = json.loads(original) if original else {'version': 1, 'tables': {}}
    moves = {item['old']: item['new'] for item in plan}
    if len(set(moves.values())) != len(plan):
        raise ValueError('Destination collision')
    # 所有前置条件先检查，避免迁到一半才发现目标同名表或不同挂载点。
    for item in plan:
        source, target = root / item['old'], root / item['new']
        if not source.is_dir() or source.is_symlink() or target.exists():
            raise ValueError('Missing source or existing target: ' + str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_dev != target.parent.stat().st_dev:
            raise ValueError('Only same-filesystem directory renames are supported')
        if control_directory(target).exists():
            raise ValueError('Destination control directory already exists: ' + str(target))
    moved = []
    snapshots = []
    published_mapping = False
    with ExitStack() as locks:
        # 与标准 writer 共用表锁；非阻塞获取，正在写入时不移动其目录。
        for item in sorted(plan, key=lambda item: item['old']):
            lock = locks.enter_context(table_lock_path(root / item['old']).open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for item in plan:
            source = root / item['old']
            ds = lance.dataset(str(source))
            versions = []
            for version in sorted(v['version'] for v in ds.versions()):
                fixed = lance.dataset(str(source), version=version)
                versions.append({'version': version, 'rows': fixed.count_rows(), 'schema_hash': schema_hash(fixed.schema)})
            snapshots.append({**item, 'head': ds.version, 'rows': ds.count_rows(),
                              'versions': versions, 'sample_digest': sample_digest(ds),
                              'files': inventory(source), 'controls': inventory(control_directory(source))})
        (receipt_dir / 'locations.before.json').write_bytes(original or b'{"version":1,"tables":{}}\n')
        (receipt_dir / 'preflight.json').write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + '\n')
        try:
            for item in snapshots:
                source, target = root / item['old'], root / item['new']
                source.rename(target)
                moved.append((target, source))
                control, destination_control = control_directory(source), control_directory(target)
                destination_control.parent.mkdir(parents=True, exist_ok=True)
                control.rename(destination_control)
                moved.append((destination_control, control))
                if inventory(target) != item['files'] or inventory(destination_control) != item['controls']:
                    raise ValueError('Table/control files changed during relocation')
                ds = lance.dataset(str(target))
                if ds.version != item['head'] or sample_digest(ds) != item['sample_digest']:
                    raise ValueError('Dataset head/sample changed during relocation')
                for version in item['versions']:
                    fixed = lance.dataset(str(target), version=version['version'])
                    if fixed.count_rows() != version['rows'] or schema_hash(fixed.schema) != version['schema_hash']:
                        raise ValueError('Historical version changed during relocation')
            if (manifest.read_bytes() if manifest.exists() else None) != original:
                raise ValueError('Location map changed concurrently')
            # 更新所有旧别名的终点，保持一跳精确映射；不改变冻结版本/身份，不建软链接。
            mapping = {key: moves.get(value, value) for key, value in before['tables'].items()}
            mapping.update(moves)
            pending = receipt_dir / 'locations.pending.json'
            pending.write_text(json.dumps({**before, 'tables': mapping}, ensure_ascii=False, indent=2) + '\n')
            pending.replace(manifest)
            published_mapping = True
            for item in snapshots:
                aliases = [key for key, value in mapping.items() if value == item['new']]
                if any(resolve_local_uri(root / alias) != root / item['new'] for alias in aliases):
                    raise ValueError('A frozen alias failed to resolve')
            summary = [{key: value for key, value in item.items() if key not in {'files', 'controls'}} | {
                'files': len(item['files']), 'bytes': sum(stat[2] for stat in item['files'].values()),
                'same_files_verified': True,
            } for item in snapshots]
            pending_receipt = receipt_dir / 'receipt.pending.json'
            pending_receipt.write_text(json.dumps({'plan': plan, 'tables': summary}, ensure_ascii=False, indent=2) + '\n')
            pending_receipt.replace(receipt)
        except BaseException:
            if published_mapping:
                if original is None:
                    manifest.unlink(missing_ok=True)
                else:
                    rollback = receipt_dir / 'locations.rollback.json'
                    rollback.write_bytes(original)
                    rollback.replace(manifest)
            for destination, source in reversed(moved):
                destination.rename(source)
            raise
    return {'tables': len(plan), 'versions': sum(len(item['versions']) for item in snapshots),
            'bytes_unchanged': sum(sum(stat[2] for stat in item['files'].values()) for item in snapshots),
            'receipt': str(receipt)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--remaining-preparation-targets', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, apply=args.apply,
                         remaining_preparation_targets=args.remaining_preparation_targets),
                     ensure_ascii=False, indent=2))
