"""将概念挂载元数据并入主表，核验后退役独立关系表。

入口：python -m tools.lake_migration.merge_concept_taxonomy --workspace ROOT --apply。
默认只显示计划。读取两表固定 v2，按概念名合并 taxonomy_metadata，保留主表
原字段和历史版本；新建三表发布，清理旧发布/登记/映射，最后删除关系表。
核验与删除回执放 _demiflow，不创建新的公共业务表，不运行模型。
"""
import argparse
from collections import defaultdict
from contextlib import ExitStack
import fcntl
import hashlib
import json
from pathlib import Path
import shutil

import lance
import pyarrow as pa

from collect.schemas import MASTER_CONCEPTS, TAXONOMY_METADATA
from demiflow.lance.control import control_directory, table_lock_path
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog, ReleaseRegistry
from demiflow.lance.storage import resolve_local_uri, schema_hash

MASTER = 'demiwtg/collect/datasets/concepts.lance'
RELATIONS = 'datasets/concept_taxonomy.lance'
OLD_RELEASE = 'master_data_current_20260921'
NEW_RELEASE = 'master_data_merged_20260927'
OPERATION = 'concept_taxonomy_merge_20260927'


def save_json(path, value):
    """原子写入本次维护的控制回执。"""
    pending = path.with_suffix('.pending.json')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    pending.replace(path)


def digest(table):
    """对完整 Arrow 字段和值生成带协议名的核验摘要。"""
    output = pa.BufferOutputStream()
    with pa.ipc.new_stream(output, table.schema) as writer:
        writer.write_table(table.combine_chunks())
    return 'arrow-ipc-v1:' + hashlib.sha256(output.getvalue()).hexdigest()


def metadata_for_concepts(concepts, relations):
    """逐概念核对全部路径，按主表路径顺序保留关系的所有非主键字段。"""
    groups = defaultdict(dict)
    for batch in relations.to_batches():
        for row in batch.to_pylist():
            name = row.pop('concept_key')
            path = row['taxonomy_node_key'].removeprefix('demiwtg / ')
            if path in groups[name]:
                raise ValueError('Duplicate relationship: ' + name + ' / ' + path)
            groups[name][path] = row
    names, values, seen = [], [], set()
    for row in concepts.select(['name', 'taxonomy']).to_pylist():
        name = row['name']
        if name in seen:
            raise ValueError('Duplicate concept: ' + name)
        seen.add(name)
        paths = json.loads(row['taxonomy'] or '[]')
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError('Invalid taxonomy: ' + name)
        members = groups.pop(name, {})
        if len(paths) != len(set(paths)) or set(paths) != set(members):
            raise ValueError('Taxonomy relationships differ: ' + name)
        names.append(name)
        values.append([members[path] for path in paths])
    if groups:
        raise ValueError('Relationships reference missing concepts: ' + next(iter(groups)))
    return pa.Table.from_arrays([pa.array(names, pa.string()),
                                 pa.array(values, TAXONOMY_METADATA.type)],
                                schema=pa.schema([MASTER_CONCEPTS.field('name'), TAXONOMY_METADATA]))


def verify_result(root, summary):
    """从固定新版本全量回读，确认原字段与新增元数据均未丢失。"""
    ds = DatasetRef.from_dict(summary['master_ref']).open(root)
    old = lance.dataset(str(root / MASTER), version=summary['master_before_version'])
    if not ds.to_table(columns=old.schema.names).equals(old.to_table()):
        raise ValueError('Existing concept columns changed')
    if digest(ds.to_table(columns=['name', 'taxonomy_metadata'])) != summary['metadata_digest']:
        raise ValueError('Merged relationship metadata changed')
    return ds


def run(workspace, *, apply=False):
    """核对固定来源 → 按 name 扩列 → 发布/清理登记 → 删除，失败可重入。"""
    root = Path(workspace).resolve()
    if not apply:
        return {'apply': False, 'master': MASTER, 'source': RELATIONS,
                'source_versions': 2, 'column': 'taxonomy_metadata', 'release': NEW_RELEASE}
    receipt_dir = root / '_demiflow' / OPERATION
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / 'receipt.json'
    plan_path = receipt_dir / 'plan.json'
    summary_path = receipt_dir / 'verified.json'
    manifest = root / '_demiflow/lance_locations.json'
    with ExitStack() as locks:
        lock = locks.enter_context((receipt_dir / 'operation.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if receipt.exists():
            summary = json.loads(receipt.read_text())
            verify_result(root, summary)
            if (root / RELATIONS).exists():
                raise ValueError('Deleted relationship table was recreated')
            current = ReleaseRegistry(root).get(NEW_RELEASE)
            if current is None or current['status'] != 'registered':
                raise ValueError('Merged master release is missing')
            for spec in json.loads(current['table_refs']):
                DatasetRef.from_dict(spec).open(root)
            source_control = control_directory(root / RELATIONS)
            if source_control.exists():
                shutil.rmtree(source_control)
            return {**summary, 'already_complete': True}
        # 与普通 writer 共用表锁；关系表只允许当前 v2，主表允许重入已扩列的版本。
        for uri in sorted([MASTER, RELATIONS]):
            path = root / uri
            if path.is_symlink():
                raise ValueError('Unexpected table symlink: ' + uri)
            lock = locks.enter_context(table_lock_path(path).open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        catalog, releases = Catalog(root), ReleaseRegistry(root)
        if not plan_path.exists():
            master, relations = lance.dataset(str(root / MASTER)), lance.dataset(str(root / RELATIONS))
            if (master.version, relations.version) != (2, 2):
                raise ValueError('Expected master@2 and concept_taxonomy@2')
            old_release = releases.get(OLD_RELEASE)
            if old_release is None or old_release['status'] != 'registered':
                raise ValueError('Expected registered predecessor release')
            # 只退役指定旧发布；任何其他发布仍依赖旧表时，在改主表前失败。
            for row in releases.rows():
                if row['release_id'] != OLD_RELEASE and any(
                    Path(DatasetRef.from_dict(ref).resolve(root)) == root / RELATIONS
                    for ref in json.loads(row['table_refs'])
                ):
                    raise ValueError('Another release references the relationship table: ' + row['release_id'])
            plan = {'master_before_version': 2, 'relations_version': 2,
                    'old_release': old_release,
                    'removed_catalog_rows': [row for row in catalog.rows()
                        if resolve_local_uri(root / row['relative_uri']) == root / RELATIONS],
                    'relation_versions': [v['version'] for v in relations.versions()]}
            retired_uris = {RELATIONS} | {
                row['relative_uri'] for row in plan['removed_catalog_rows']
            }
            if manifest.exists():
                retired_uris.update(key for key, value in json.loads(manifest.read_text())['tables'].items()
                                    if value == RELATIONS)
            plan['retired_uris'] = sorted(retired_uris)
            save_json(plan_path, plan)
        plan = json.loads(plan_path.read_text())
        if (root / RELATIONS).exists():
            relations = lance.dataset(str(root / RELATIONS))
            if relations.version != plan['relations_version']:
                raise ValueError('Relationship head changed during migration')
            old = lance.dataset(str(root / MASTER), version=plan['master_before_version'])
            base = old.to_table()
            metadata = metadata_for_concepts(base, relations.to_table())
            master = lance.dataset(str(root / MASTER))
            if 'taxonomy_metadata' not in master.schema.names:
                if master.version != plan['master_before_version']:
                    raise ValueError('Concept head changed during migration')
                # 原生 Lance 按 name 扩列，不重写原字段；旧快照及其索引保持可读。
                master.merge(metadata, left_on='name')
            if master.schema != MASTER_CONCEPTS:
                raise ValueError('Unexpected merged concept schema')
            if not master.to_table(columns=old.schema.names).equals(base):
                raise ValueError('Existing concept columns changed')
            if not master.to_table(columns=metadata.column_names).equals(metadata):
                raise ValueError('Relationship metadata readback differs')
            ref = DatasetRef(dataset_id='master/concepts/v1/concepts', relative_uri=MASTER,
                             lance_version=master.version, schema_name='master_concepts',
                             schema_version='v2', schema_hash=schema_hash(master.schema),
                             row_count=master.count_rows())
            summary = {'master_before_version': plan['master_before_version'], 'master_ref': ref.to_dict(),
                       'relationships': relations.count_rows(), 'metadata_digest': digest(metadata),
                       'old_columns_equal': True, 'relationship_metadata_equal': True,
                       'retired_release': OLD_RELEASE, 'release': NEW_RELEASE,
                       'deleted_table': RELATIONS, 'deleted_versions': plan['relation_versions']}
            save_json(summary_path, summary)
        else:
            summary = json.loads(summary_path.read_text())
            verify_result(root, summary)
            ref = DatasetRef.from_dict(summary['master_ref'])
        catalog.register(ref)
        kept_refs = [DatasetRef.from_dict(r) for r in json.loads(plan['old_release']['table_refs'])
                     if r['relative_uri'] not in plan['retired_uris']
                     and Path(DatasetRef.from_dict(r).resolve(root)) != root / MASTER]
        releases.register(NEW_RELEASE, release_kind='master_data', table_refs=[ref, *kept_refs],
                          pipeline_run=OPERATION,
                          validation={'receipt': f'_demiflow/{OPERATION}/receipt.json',
                                      'relationships': summary['relationships'], 'lossless_merge': True})
        # 短时独占两张登记表，保留其他并发任务已追加的全部登记；旧行存入维护计划，
        # 登记表历史版本也继续保留。新发布核验成功才清除旧发布和旧关系登记。
        with ExitStack() as registry_locks:
            for uri in (catalog.uri, releases.uri):
                lock = registry_locks.enter_context(table_lock_path(uri).open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for row in releases.rows():
                if row['release_id'] == OLD_RELEASE:
                    if row != plan['old_release']:
                        raise ValueError('Predecessor release changed')
                    continue
                for spec in json.loads(row['table_refs']):
                    if spec['relative_uri'] in plan['retired_uris']:
                        raise ValueError('Surviving release references retired relationships')
            release_ds = lance.dataset(releases.uri)
            release_ds.delete(f"release_id = '{OLD_RELEASE}'")
            removed_uris = set(plan['retired_uris'])
            catalog_ds = lance.dataset(catalog.uri)
            predicate = 'relative_uri IN (' + ','.join("'" + uri.replace("'", "''") + "'"
                                                       for uri in sorted(removed_uris)) + ')'
            catalog_ds.delete(predicate)
            # 删除旧别名，不将旧关系表引用伪装成不同 schema 的概念表引用。
            if manifest.exists():
                before = manifest.read_bytes()
                document = json.loads(before)
                removed_mappings = {key: value for key, value in document['tables'].items()
                                    if key in removed_uris or value == RELATIONS}
                if removed_mappings:
                    save_json(receipt_dir / 'removed_mappings.json', removed_mappings)
                    document['tables'] = {key: value for key, value in document['tables'].items()
                                          if key not in removed_mappings}
                    if manifest.read_bytes() != before:
                        raise ValueError('Location map changed concurrently')
                    save_json(manifest, document)
            if (root / RELATIONS).exists():
                shutil.rmtree(root / RELATIONS)
        save_json(receipt, summary)
    # 源表锁释放后清理其控制文件；主表及两个登记表的控制目录保持。
    source_control = control_directory(root / RELATIONS)
    if source_control.exists():
        shutil.rmtree(source_control)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, apply=args.apply), ensure_ascii=False, indent=2))
