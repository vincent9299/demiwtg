"""固定来源 → 显式等价关系 → 候选聚合与择值 → 每概念一行的 Lance 快照。"""
import argparse
from functools import partial
from contextlib import ExitStack
import json
from pathlib import Path
import re

import lance
from demiflow import data
from demiflow.execution.artifacts import run_lock
from demiflow.lance.storage import resolve_local_uri
from project import resolve_root
from .operators import merge as m
from .operators.contracts import check_schema, fixed
from .operators.schema import (
    ALIGNMENTS, CHOICES, CONCEPTS, LEGACY_SELECTION, SOURCE_SCHEMAS, RELATED,
    SUMMARY, IDENTITIES, SCHEMA_VERSION, RULES_VERSION,
)

MODULE = 'demiwtg/preparation/visual_concepts'


def config(*, run, qid_source=None, qid_review_source=None, legacy_sources=(),
           legacy_selection_source=None, alignment_source=None, choice_source=None,
           previous_source=None, related_sources=(), resource_sources=(),
           target_uri=MODULE + '/datasets/concepts.lance', write_mode='overwrite',
           read_batch_size=32, workers=1, worker_mode='thread', relation_memory_bytes=512*1024**2,
           max_group_bytes=32*1024**2):
    """只校验配置，不启动 QID 筛选、不调用模型、不跟随源表 latest。

    本轮全量快照仅包含配置来源中通过准入的概念。多张旧审核表必须提供最终
    selection，避免把历史 ready 和后来 hold 混为当前结论。相关资源通过固定
    来源引用交付，不读图片/文档字节。重跑覆盖新版本，历史版本继续可读。
    """
    if not isinstance(run, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', run):
        raise ValueError('run must be a safe nonempty name')
    if worker_mode not in {'thread', 'process'}:
        raise ValueError('worker_mode must be thread or process')
    if write_mode != 'overwrite':
        raise ValueError('Only full-snapshot overwrite is supported')
    if bool(qid_source) != bool(qid_review_source):
        raise ValueError('qid_source and qid_review_source must be provided together')
    if not qid_source and not legacy_sources:
        raise ValueError('At least one concept input is required')
    if legacy_selection_source is not None and not legacy_sources:
        raise ValueError('legacy_selection_source requires legacy_sources')
    if len(legacy_sources) > 1 and legacy_selection_source is None:
        raise ValueError('Multiple legacy sources require a final legacy_selection_source')
    for name, value in [('read_batch_size', read_batch_size), ('workers', workers),
                        ('relation_memory_bytes', relation_memory_bytes), ('max_group_bytes', max_group_bytes)]:
        if type(value) is not int or value < 1:
            raise ValueError(name + ' must be a positive integer')
    target_uri = str(target_uri)
    target = Path(target_uri)
    if target.is_absolute() or '..' in target.parts or target.parent != Path(MODULE + '/datasets') or target.suffix != '.lance':
        raise ValueError('Target must be an owned flat visual_concepts/datasets/*.lance table')
    refs = {k: fixed(v) if v is not None else None for k, v in {
        'qid_source': qid_source, 'qid_review_source': qid_review_source,
        'legacy_selection_source': legacy_selection_source, 'alignment_source': alignment_source,
        'choice_source': choice_source, 'previous_source': previous_source}.items()}
    related, resources = [], []
    for item in related_sources:
        if set(item) != {'kind', 'source'} or item['kind'] not in RELATED:
            raise ValueError('Unsupported related source kind')
        related.append({'kind': item['kind'], 'source': fixed(item['source'])})
    for item in resource_sources:
        if set(item) != {'kind', 'source'} or not isinstance(item['kind'], str) or not item['kind']:
            raise ValueError('Resource requires a kind and fixed source')
        resources.append({'kind': item['kind'], 'source': fixed(item['source'])})
    if len({(r['uri'], r['version']) for r in legacy_sources}) != len(legacy_sources):
        raise ValueError('Duplicate legacy source reference')
    if len({(r['kind'], r['source']['uri'], r['source']['version']) for r in related}) != len(related):
        raise ValueError('Duplicate related source reference')
    if len(legacy_sources) + len(related) + len(resources) > 128:
        raise ValueError('At most 128 configured legacy/related/resource sources')
    return dict(run=run, **refs, legacy_sources=[fixed(r) for r in legacy_sources],
                related_sources=related, resource_sources=resources, target_uri=target_uri,
                write_mode=write_mode, read_batch_size=read_batch_size, workers=workers, worker_mode=worker_mode,
                relation_memory_bytes=relation_memory_bytes, max_group_bytes=max_group_bytes)


def run_pipeline(options):
    """所有读表、关联键、分组和写入均在主线；只操作本模块输出。"""
    cfg = config(**options)
    root = resolve_root()
    target = root / cfg['target_uri']
    work = root / MODULE / 'runs' / cfg['run']
    summary_path = root / MODULE / 'datasets' / ('summary__' + cfg['run'] + '.lance')
    registry_path = target.with_name(target.stem + '__identities.lance')
    if target == summary_path or registry_path == summary_path:
        raise ValueError('Concept and summary targets must differ')
    sources = [(cfg['qid_source'], 'qid'), (cfg['qid_review_source'], 'qid_review'),
               *[(r, 'legacy') for r in cfg['legacy_sources']],
               *[(r['source'], r['kind']) for r in cfg['related_sources']]]
    schemas = {**SOURCE_SCHEMAS, 'alignment': ALIGNMENTS, 'choice': CHOICES, 'selection': LEGACY_SELECTION}
    sources += [(cfg['alignment_source'], 'alignment'), (cfg['choice_source'], 'choice'),
                (cfg['legacy_selection_source'], 'selection')]
    for ref, kind in sources:
        if ref is None:
            continue
        path = resolve_local_uri(root / ref['uri'])
        if path in {target.resolve(), summary_path.resolve(), registry_path.resolve()}:
            raise ValueError('Owned output cannot overwrite an input source')
        check_schema(lance.dataset(str(path), version=ref['version']).schema, schemas[kind], kind + '.')
    for resource in cfg['resource_sources']:
        ref = resource['source']
        if resolve_local_uri(root / ref['uri']) in {target.resolve(), summary_path.resolve(), registry_path.resolve()}:
            raise ValueError('Owned output cannot overwrite a resource source')
        lance.dataset(str(resolve_local_uri(root / ref['uri'])), version=ref['version'])
    reader = dict(batch_size=cfg['read_batch_size'], batch_readahead=1, fragment_readahead=1)
    chunk = cfg['relation_memory_bytes']
    group = partial(m.group_rows, max_group_bytes=cfg['max_group_bytes'])
    work.mkdir(parents=True, exist_ok=True)
    (work / 'tmp').mkdir(exist_ok=True)
    # 目标锁覆盖概念表、身份登记和摘要；原生 expected_version/create 继续保护提交版本。
    with run_lock(root / '_demiflow' / 'visual_concepts' / target.stem), run_lock(work), data.local_execution(workers=cfg['workers'], worker_mode=cfg['worker_mode'], batch_rows=cfg['read_batch_size'],
            memory_bytes=chunk, temp_directory=str(work / 'tmp')), ExitStack() as caches:
        initial = lance.dataset(str(target)).version if target.exists() else None
        registry_version = lance.dataset(str(registry_path)).version if registry_path.exists() else None
        if registry_version is not None:
            check_schema(lance.dataset(str(registry_path), version=registry_version).schema, IDENTITIES, 'identity_registry.')
        previous = cfg['previous_source']
        if previous is None and initial is not None:
            previous = {'uri': cfg['target_uri'], 'version': initial}
        if previous is not None:
            if initial is not None and (resolve_local_uri(root / previous['uri']) != target.resolve() or previous['version'] != initial):
                raise ValueError('Updating an existing target requires its current version as previous_source')
            prior_schema = lance.dataset(str(resolve_local_uri(root / previous['uri'])), version=previous['version']).schema
            check_schema(prior_schema, CONCEPTS, 'previous.')
        cfg['previous_source'] = previous
        print('[visual_concepts] reading fixed source snapshots', flush=True)
        # 先构造旧侧最终范围；名单是交付契约，缺失或不合格的目标不能被半连接吞掉。
        legacy_members = None
        legacy_inputs = []
        for ref in cfg['legacy_sources']:
            legacy_inputs.append(data.read_lance(str(root / ref['uri']), version=ref['version'], **reader)
                                 .map(m.legacy_input, fn_kwargs={'source': ref}))
        if legacy_inputs:
            legacy = legacy_inputs[0]
            for stream in legacy_inputs[1:]:
                legacy = legacy.union(stream)
            selection = cfg['legacy_selection_source']
            if selection:
                selected = (data.read_lance(str(root / selection['uri']), version=selection['version'], **reader)
                            .map(m.legacy_selection))
                legacy = selected.join(legacy, on=['_source_uri', '_source_version', 'concept_id', 'assessment_id'],
                                       how='left', chunk_bytes=chunk)
            else:
                legacy = legacy.filter(m.legacy_admitted_input)
            legacy_members = legacy.map(m.legacy_member, fn_kwargs={'require_selected': selection is not None})
            if cfg['alignment_source']:
                ref = cfg['alignment_source']
                alignments = (data.read_lance(str(root / ref['uri']), version=ref['version'], **reader)
                    .map(m.alignment_row, fn_kwargs={'source': ref})
                    .reduce_by_key('source_key', partial(group, key='source_key', output='alignment_rows'), chunk_bytes=chunk))
                legacy_members = legacy_members.join(alignments, on='source_key', how='left', chunk_bytes=chunk)
            legacy_members = caches.enter_context(legacy_members.map(m.identity_member).materialize())
            print('[visual_concepts] admitted legacy records=' + str(legacy_members.count()), flush=True)
        members = legacy_members
        if cfg['qid_source'] is not None:
            ref, reviews = cfg['qid_source'], cfg['qid_review_source']
            # QID 自身 candidate 可准入；旧侧已准入且 exact 对齐的 QID 也携带完整元数据与原判断。
            scope = (data.read_lance(str(root / reviews['uri']), version=reviews['version'],
                     columns=['qid', 'status', 'review'], **reader)
                     .filter(m.admitted_qid).map(m.admitted_qid_key))
            if legacy_members is not None:
                scope = scope.union(legacy_members.filter(m.known_qid).map(m.resolved_qid_row))
            scope = caches.enter_context(scope.reduce_by_key('qid', m.merge_qid_key, chunk_bytes=chunk).materialize())
            print('[visual_concepts] QID metadata scope=' + str(scope.count()), flush=True)
            base = (data.read_lance(str(root / ref['uri']), version=ref['version'], **reader)
                    .join(scope, on='qid', how='semi', chunk_bytes=chunk).map(m.qid_value))
            review_rows = (data.read_lance(str(root / reviews['uri']), version=reviews['version'], **reader)
                .join(scope, on='qid', how='semi', chunk_bytes=chunk)
                .map(m.qid_review_row, fn_kwargs={'source': reviews})
                .reduce_by_key('qid', partial(group, key='qid', output='review_rows'), chunk_bytes=chunk))
            qid_members = (scope.join(base, on='qid', how='left', chunk_bytes=chunk)
                           .join(review_rows, on='qid', how='left', chunk_bytes=chunk)
                           .flat_map(m.qid_members, fn_kwargs={'source': ref}))
            members = qid_members if members is None else members.union(qid_members)
        if cfg['related_sources']:
            attachments = []
            for item in cfg['related_sources']:
                ref = item['source']
                attachments.append(data.read_lance(str(root / ref['uri']), version=ref['version'], **reader)
                    .map(m.related_row, fn_kwargs={'source': ref, 'kind': item['kind']}))
            related = attachments[0]
            for extra in attachments[1:]:
                related = related.union(extra)
            related = related.reduce_by_key('source_key', partial(group, key='source_key', output='attachment_rows'), chunk_bytes=chunk)
            members = members.join(related, on='source_key', how='left', chunk_bytes=chunk)
        members = members.map(m.identity_member)
        prior = None
        if previous:
            prior = (data.read_lance(str(root / previous['uri']), version=previous['version'],
                columns=['concept_id', 'identity_key', 'source_members', 'redirected_concept_ids', 'resolutions'], **reader)
                .flat_map(m.previous_members, fn_kwargs={'source': previous}))
        if registry_version is not None:
            registered = (data.read_lance(str(registry_path), version=registry_version, **reader)
                          .map(m.registry_previous))
            prior = registered if prior is None else prior.union(registered)
        if prior is not None:
            prior = prior.reduce_by_key('source_key', m.latest_previous, chunk_bytes=chunk)
            prior_groups = prior.reduce_by_key('source_key', partial(group, key='source_key', output='previous_rows'), chunk_bytes=chunk)
            prior_groups = caches.enter_context(prior_groups.materialize())
            members = (members.join(prior_groups, on='source_key', how='left', chunk_bytes=chunk)
                .join(prior_groups.map(m.identity_previous_group), on='identity_key', how='left', chunk_bytes=chunk)
                .map(m.combine_previous))
        merged = members.map(m.identity_member).reduce_by_key('identity_key', partial(group, key='identity_key', output='members'), chunk_bytes=chunk)
        if cfg['choice_source']:
            ref = cfg['choice_source']
            choices = (data.read_lance(str(root / ref['uri']), version=ref['version'], **reader)
                .map(m.choice_row, fn_kwargs={'source': ref}).reduce_by_key('identity_key', partial(group, key='identity_key', output='choices'), chunk_bytes=chunk))
            merged = merged.join(choices, on='identity_key', how='left', chunk_bytes=chunk)
        receipt = merged.map(m.finish_concept, fn_kwargs={'resource_sources': cfg['resource_sources']}).write_lance(
            str(target), schema=CONCEPTS, mode='create' if initial is None else 'overwrite',
            expected_version=initial, return_receipt=True)
        output = {'uri': cfg['target_uri'], 'version': receipt.committed_version}
        # 登记历史身份但不扩大本轮范围。只读取新表的窄身份投影，原生按键 merge 保留移出名单的键。
        current_ids = (data.read_lance(str(target), version=receipt.committed_version,
            columns=['concept_id', 'identity_key', 'source_members', 'redirected_concept_ids', 'resolutions'], **reader)
            .flat_map(m.previous_members, fn_kwargs={'source': output, 'priority': -1}))
        if registry_version is None and previous is not None:
            current_ids = current_ids.union(prior)
        registry_rows = current_ids.reduce_by_key('source_key', m.latest_previous, chunk_bytes=chunk).map(m.registry_value)
        registry_receipt = registry_rows.write_lance(str(registry_path), schema=IDENTITIES,
            mode='create' if registry_version is None else 'merge', on='source_key' if registry_version is not None else None,
            when_not_matched='insert' if registry_version is not None else None,
            expected_version=registry_version, return_receipt=True)
        registry_ref = {'uri': str(registry_path.relative_to(root)), 'version': registry_receipt.committed_version}
        summary = {'run': cfg['run'], 'complete': True, 'concept_count': receipt.written_rows,
                   'config_json': m.canonical(cfg), 'output': output, 'identity_registry': registry_ref,
                   'identity_registry_input': {'uri': registry_ref['uri'], 'version': registry_version} if registry_version else None,
                   'schema_version': SCHEMA_VERSION, 'rules_version': RULES_VERSION}
        # 一个小运行摘要；不为统计重扫全部宽行或正文对象。
        data.from_items([summary]).write_lance(str(summary_path), schema=SUMMARY, mode='overwrite')
        print('[visual_concepts] committed ' + m.canonical(output) + ' rows=' + str(receipt.written_rows), flush=True)
        return {**summary, 'requests_issued': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='JSON configuration for config(**values)')
    parser.add_argument('--run', help='Override run name')
    parser.add_argument('--write-mode', choices=['overwrite'], help='Full current-scope snapshot')
    args = parser.parse_args()
    options = json.loads(Path(args.config).read_text())
    if args.run:
        options['run'] = args.run
    if args.write_mode:
        options['write_mode'] = args.write_mode
    print(json.dumps(run_pipeline(config(**options)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
