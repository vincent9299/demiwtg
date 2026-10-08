"""同一审定版本的正例图交接、有限规模分层选样；不读业务表或调用模型。"""
import hashlib
import json
from collections import defaultdict, deque

import pyarrow as pa
from .concept_context import AUTHORING_CONTEXT

REF = pa.struct([('uri', pa.string()), ('version', pa.int64())])
POSITIVE = pa.struct([
    ('sha256', pa.string()), ('image_uri', pa.string()), ('review_sources', pa.list_(REF)),
])
COHORT = pa.schema([
    ('concept', pa.string()), ('concept_id', pa.string()), ('assessment_id', pa.string()),
    ('taxonomy', pa.list_(pa.string())), ('sampling_taxonomy', pa.list_(pa.string())),
    ('sampling_node_id', pa.string()),
    ('sampling_category', pa.string()), ('selection_rank', pa.int64()),
    ('positive_image_count', pa.int64()), ('positive_images', pa.list_(POSITIVE)),
    ('concept_record', AUTHORING_CONTEXT),
])
VARIANTS = ('with_positive_images', 'without_positive_images')


def rank(seed, value):
    return hashlib.sha256(json.dumps([seed, value], ensure_ascii=False).encode()).hexdigest()


def merge_review(acc, row):
    """相同概念审定×SHA只计一次；有效复核冲突不作为正例，技术失败不覆盖有效判断。"""
    positive = row['aligned'] is True and row['decision'] == 'match'
    out = dict(row) if acc is None else dict(acc)
    out['positive'] = positive if acc is None else acc['positive'] and positive
    if acc is not None and row['image_uri'] != acc['image_uri']:
        raise ValueError('The same reviewed SHA has conflicting object URIs')
    refs = list(acc['review_sources']) if acc else []
    if row['review_source'] not in refs:
        refs.append(row['review_source'])
    if len(refs) > 64:
        raise ValueError('Too many review sources for one image')
    out['review_sources'] = sorted(refs, key=lambda ref: (ref['uri'], ref['version']))
    return out


def collect_positives(acc, row, *, maximum, seed):
    images = (acc['positive_images'] if acc else []) + [
        {key: row[key] for key in ('sha256', 'image_uri', 'review_sources')}]
    images.sort(key=lambda image: (rank(seed, [row['concept_id'], image['sha256']]), image['sha256']))
    return {key: row[key] for key in ('concept_id', 'assessment_id')} | {
        'positive_image_count': (acc['positive_image_count'] if acc else 0) + 1,
        'positive_images': images[:maximum],
    }


def sampling_key(row, *, depth):
    paths = row['sampling_taxonomy']
    # 分类只用于抽样；固定新树的唯一主路径，或旧入口第一条原路径，确定分层桶。
    parts = [part.strip() for part in (paths[0] if paths else '未分类').split('/') if part.strip()]
    category = ' / '.join(parts[:depth])
    return {key: row[key] for key in ('concept_id', 'assessment_id', 'concept')} | {'sampling_category': category}


def collect_sampling_keys(acc, row):
    """仅汇总短控制键；最多4096概念、8MiB，正文和图片引用不进入抽样累积器。"""
    size = len(json.dumps(row, ensure_ascii=False).encode())
    count = (acc['count'] if acc else 0) + 1
    total = (acc['bytes'] if acc else 0) + size
    if count > 4096 or total > 8 * 1024 * 1024:
        raise ValueError('Comparison selection exceeds 4096 concepts / 8 MiB of control keys')
    return {'group': 'all', 'count': count, 'bytes': total,
            'items': (acc['items'] if acc else []) + [row]}


def choose_balanced(row, *, size, seed):
    """先在一级类别间轮转，再在各一级的子桶间轮转；桶耗尽后分配给其余桶。"""
    if row['count'] < size:
        raise ValueError(f'Only {row["count"]} ready concepts meet the reviewed-image threshold; need {size}')
    buckets = defaultdict(list)
    for item in row['items']:
        buckets[item['sampling_category']].append(item)
    queues = {key: deque(sorted(items, key=lambda item: rank(seed, item['concept_id'])))
              for key, items in buckets.items()}
    branches = defaultdict(list)
    for category in queues:
        branches[category.split(' / ')[0]].append(category)
    active = deque(sorted(branches, key=lambda key: rank(seed, key)))
    children = {key: deque(sorted(value, key=lambda child: rank(seed, child))) for key, value in branches.items()}
    selected = []
    while len(selected) < size:
        branch = active.popleft()
        category = children[branch].popleft()
        selected.append({**queues[category].popleft(), 'selection_rank': len(selected) + 1})
        if queues[category]:
            children[branch].append(category)
        if children[branch]:
            active.append(branch)
    return {**{key: row[key] for key in ('group', 'count', 'bytes')}, 'selected': selected}


def revise_cohort(row, *, size, seed, common_limit):
    """优先纳入原分类1，保留旧名单；新增占用分类3的靠后名额，最后才动其他类别。"""
    if row['count'] < size:
        raise ValueError(f'Only {row["count"]} eligible concepts; need {size}')
    previous = sorted((r for r in row['items'] if r.get('base_rank') is not None),
                      key=lambda r: r['base_rank'])
    common = sorted((r for r in row['items'] if '1' in (r.get('original_categories') or [])),
                    key=lambda r: (r.get('base_rank') is None, r.get('base_rank') or 0,
                                   rank(seed, r['concept_id'])))
    selected = {r['concept_id']: r for r in common[:min(size, common_limit)]}
    # 多来源1/3或2/3冲突不当作纯专业类；原判断全部保留。
    retained = sorted(previous, key=lambda r: (r.get('original_categories') == ['3'], r['base_rank']))
    for item in retained:
        if len(selected) == size:
            break
        if '1' in (item.get('original_categories') or []) and item['concept_id'] not in selected:
            continue
        selected.setdefault(item['concept_id'], item)
    if len(selected) < size:
        balanced = choose_balanced(row, size=row['count'], seed=seed)['selected']
        for item in balanced:
            if len(selected) == size:
                break
            if '1' in (item.get('original_categories') or []) and item['concept_id'] not in selected:
                continue
            selected.setdefault(item['concept_id'], item)
    if len(selected) != size:
        raise ValueError('Not enough eligible concepts after applying common_limit')
    # 未被替换的旧概念保留原位置；新概念只填空位。
    slots = {r['base_rank']: r for r in selected.values() if r.get('base_rank') is not None}
    additions = iter(sorted((r for r in selected.values() if r.get('base_rank') is None),
                            key=lambda r: rank(seed, r['concept_id'])))
    chosen = [{**(slots[i] if i in slots else next(additions)), 'selection_rank': i}
              for i in range(1, size + 1)]
    return {**{key: row[key] for key in ('group', 'count', 'bytes')}, 'selected': chosen,
            'revision': {'eligible_common_count': len(common),
                         'requested_common_count': common_limit,
                         'selected_common_count': min(len(common), size, common_limit),
                         'retained_count': len(slots),
                         'added': [r['concept'] for r in chosen if r.get('base_rank') is None],
                         'removed': [r['concept'] for r in previous if r['concept_id'] not in selected]}}


def authoring_images(row, variant):
    return json.dumps([
        {'kind': 'image', 'number': i, 'object_ref': {'uri': image['image_uri'], 'sha256': image['sha256']}}
        for i, image in enumerate(row['positive_images'], 1)
    ] if variant == 'with_positive_images' else [], ensure_ascii=False)
