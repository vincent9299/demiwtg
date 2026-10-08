"""固定 taxonomy 候选的抽样投影；只校验身份与版本，不作分类语义判断。"""
import json

from preparation.taxonomy.operators.tree import tree_hash, validate_tree


MAX_NODES = 4096
MAX_BYTES = 8 * 1024 * 1024


def snapshot(row, source):
    outputs = row['outputs'] or []
    refs = {item['stage']: {key: item[key] for key in ('uri', 'version')} for item in outputs}
    if len(refs) != len(outputs) or not {'nodes', 'placements'} <= refs.keys():
        raise ValueError('Taxonomy summary must bind unique nodes and placements snapshots')
    for ref in refs.values():
        if not ref['uri'] or type(ref['version']) is not int or ref['version'] < 1:
            raise ValueError('Taxonomy outputs require fixed uri/version')
    return {'summary': source, 'run': row['run'], 'phase': row['phase'],
            'complete': row['complete'], 'quality_passed': row['quality_passed'], 'outputs': refs}


def collect_nodes(acc, row):
    node = row['node']
    size = len(json.dumps(node, ensure_ascii=False).encode())
    total = (acc['bytes'] if acc else 0) + size
    count = (len(acc['nodes']) if acc else 0) + 1
    if count > MAX_NODES or total > MAX_BYTES:
        raise ValueError('Sampling taxonomy exceeds 4096 nodes / 8 MiB')
    return {'group': 'tree', 'bytes': total, 'nodes': (acc['nodes'] if acc else []) + [node]}


def sampling_tree(row):
    nodes = row['nodes']
    index = validate_tree(nodes, {'max_nodes': MAX_NODES, 'max_tree_bytes': MAX_BYTES})
    paths, size = {}, 0
    for key, node in index.items():
        if node['status'] != 'active':
            continue
        labels, current = [], node
        while current:
            labels.append(current['name'])
            current = index[current['parent_id']] if current['parent_id'] else None
        path = ' / '.join(reversed(labels))
        size += len(key.encode()) + len(path.encode())
        if size > MAX_BYTES:
            raise ValueError('Sampling taxonomy paths exceed 8 MiB')
        paths[key] = path
    if len(set(paths.values())) != len(paths):
        raise ValueError('Taxonomy has ambiguous duplicate label paths')
    return {'tree_hash': tree_hash(nodes), 'paths': paths}


def unique_placement(acc, row):
    if acc is not None:
        raise ValueError('Duplicate taxonomy placement for source_record_id/assessment_id')
    return row


def placement_category(row, *, tree):
    if row['tree_hash'] != tree['tree_hash']:
        raise ValueError('Taxonomy placement belongs to a different tree snapshot')
    node = row['node_id']
    if node not in tree['paths']:
        raise ValueError('Taxonomy assignment references an absent or retired node')
    if not row['source_record_id'] or not row['assessment_id']:
        raise ValueError('Taxonomy assignment requires source_record_id and assessment_id')
    return {key: row[key] for key in ('source_record_id', 'assessment_id')} | {
        'sampling_node_id': node, 'sampling_taxonomy': [tree['paths'][node]]}
