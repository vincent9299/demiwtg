"""Visible business operators for knowledge-first task authoring.

No per-case plans, assistant-written intents, or review sidecars are inputs.
All model calls remain in the notebook's native map_prompt_async chain.
"""
import json
from pathlib import Path


from curation.preparation import records as storage
from curation.preparation.materials import SplitGuard, duplicate, pixels, tokens
from curation.training.operators import fail
from curation.preparation.published import published_record
from curation.preparation.records import digest


class AuthoringRunFiles(storage.RunFiles):
    def __init__(self, run, knowledge_runs, branch, config, graph, visual_runs=None):
        self.run = Path(run).resolve()
        from curation.preparation.config import check_run_location
        from project import resolve_root
        check_run_location(self.run, storage.ROOT, resolve_root())
        self.branch, self.config = branch, config
        from curation.preparation.publication_sources import freeze_publication_source, iter_publication_rows
        def frozen(sources):
            unique = {digest(spec): spec for spec in map(freeze_publication_source, sources)}
            return [unique[key] for key in sorted(unique)]
        self.knowledge_runs = frozen(knowledge_runs)
        self.visual_runs = frozen(visual_runs or [])
        if not self.knowledge_runs and not self.visual_runs:
            raise ValueError('At least one final publication is required')
        inputs = []
        for spec in self.knowledge_runs + self.visual_runs:
            _records, identity = iter_publication_rows(spec)
            inputs.append(identity)
        self.knowledge_version = digest(inputs)
        self.manifest = {'schema': 'v4-autonomous-authoring/4', 'pipeline_version': 'V2', 'branch': branch,
            'knowledge_inputs': inputs, 'knowledge_runs': self.knowledge_runs,
            'visual_runs': self.visual_runs,
            'config': config, 'graph': graph, 'implementation': storage.code_version()}
        # Delivery integrity reads final published figures only. Previously
        # inspected scene/target bytes are rechecked as recorded dependencies.
        # 资产身份 = 内容 SHA + 来源（file/lake 表+版本）；路径仅提示。
        # 逐资产校验：单个资产不可解码/不可读记录失败状态，不中断整个 run。
        from curation.preparation.delivery import source_entries
        from curation.preparation.asset_io import asset_resolution
        published_assets = {}
        unreadable_assets = []
        for directory in self.knowledge_runs + self.visual_runs:
            for item in source_entries(directory):
                if item['kind'] == 'image':
                    asset = item['asset']
                    resolution = asset_resolution(asset.get('path'), asset['sha256'])
                    entry = {'sha256': asset['sha256'], 'path': asset.get('path'),
                             'asset_source': resolution.source if resolution.status == 'ok' else None}
                    try:
                        pixels(asset)
                        published_assets[asset['sha256']] = entry
                    except (ValueError, OSError) as error:
                        entry['status'] = 'unreadable'
                        entry['error'] = str(error)[:200]
                        unreadable_assets.append(entry)
        if unreadable_assets:
            self.manifest['unreadable_published_assets'] = unreadable_assets
        self.manifest['published_assets'] = published_assets
        self.initialize_business()
        for key, observed in self.records.items(prefix='observed_asset/').items():
            if not key.startswith('observed_asset/'): continue
            if observed.get('blob_ref'):
                from curation.preparation.materials import asset_pixels
                asset_pixels({'blob_ref':observed['blob_ref'],'sha256':observed['expected_sha256']})
                continue
            resolution = asset_resolution(observed.get('path'), observed['expected_sha256'])
            actual = resolution.actual_sha256 if resolution.status == 'ok' else None
            previous = observed.get('actual_sha256')
            if previous is not None and actual != previous:
                raise ValueError('Previously inspected scene/target bytes changed: ' + str(observed.get('path')))
            if resolution.status == 'corrupt':
                raise ValueError('Previously inspected bytes now resolve corrupt: ' + str(observed.get('path')))
            # 缺失→可读 属于可用性恢复（湖接线后预期发生），不视为字节变化
        self.version = digest(self.manifest)
        self.previous = self.version
        self.stages, self.reused, self.new = {}, [], []


def input_records(files):
    from curation.preparation.publication_sources import combined_input_records
    yield from combined_input_records(files.knowledge_runs, files.visual_runs)


def split_guard(files):
    registry = files.config.get('split_registry') or {'schema': 'v4-split-registry/1',
        'scope':'development_only','formal_test':{'concepts':[], 'rule_families':[], 'images':[],
        'source_urls':[], 'answer_text_sha256':[], 'question_sha256':[]}}
    files.records.put('split_registry',registry)
    return SplitGuard(registry)


class DeliverPublishedKnowledge:
    def __init__(self, knowledge_version):
        self.version = knowledge_version

    def __call__(self, row):
        delivered = published_record(row)
        delivered['materials'] = [{**m, 'knowledge_version': self.version} for m in delivered['materials']]
        return {**delivered, 'knowledge_version': self.version,
                'asset_source': {'specs': row['_candidate_specs'], 'concept': row['concept'],
                                 'role': 'raw_scene_metadata_not_knowledge'}}


def knowledge_items(row):
    return row['materials']






def unit_rank(row):
    return {k: row[k] for k in ('unit_id', 'sampling_key', 'concept_sampling_key', 'status')}


def require_texts(value, keys):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k].strip() for k in keys):
        raise ValueError('Nonempty fields required: ' + ', '.join(keys))


def evidence_numbers(value, count):
    if not isinstance(value, list) or not value or any(type(n) is not int or not 1 <= n <= count for n in value):
        raise ValueError('Evidence must refer to supplied numbered materials')
    return list(dict.fromkeys(value))






def focus_material_numbers(row, opportunity):
    numbers = evidence_numbers(opportunity['evidence'], len(row['materials']))
    dependencies = {iid for n in numbers for iid in
                    row['materials'][n-1].get('publication', {}).get('visual_dependencies', [])}
    return sorted(set(numbers) | {n for n, m in enumerate(row['materials'], 1)
                                 if m['kind'] == 'image' and m['image_id'] in dependencies})




class SelectTargetCandidate:
    """Search AFTER task freeze; suitability is decided by the target reviewer."""
    def __init__(self, guard, pool=None, limit=1):
        if type(limit) is not int or limit < 1:
            raise ValueError('Target candidate budget must be a positive integer')
        self.guard, self.pool, self.limit = guard, pool, limit

    def expand(self, row):
        """Review bounded target alternatives for the SAME frozen task.

        Each proposal keeps its own identity and reviewer record. Unreviewed or
        rejected alternatives never become training samples.
        """
        first = self(row)
        if first['status'] != 'target_attached' or self.limit == 1:
            yield first
            return
        assets = {a['sha256']: a for a in first['target_pool']}
        for rank, candidate in enumerate(first['target_search']['ranks'][:self.limit], 1):
            asset = assets[candidate['sha256']]
            yield {**first, 'task_id': row['task_id'] + '__target_' + str(rank),
                   'source_task_id': row['task_id'], 'target_candidate_rank': rank,
                   'target': {**asset, 'role': 'target'}}

    def __call__(self, row):
        if row['status'] != 'accepted_task':
            return row
        # Missing citation IDs are an audit signal, not proof of missing guidance.
        # Keep the recorded gap; the frozen-task target review remains mandatory.
        if row.get('loop_position'):
            pool, source_trace = row['design_targets'], {'method': 'current_target_visit'}
        else:
            pool, source_trace = self.pool.load(row, 'target') if self.pool else (row.get('target_pool', []), {})
        row = {**row, 'target_pool': pool, 'target_source_search': source_trace}
        query = tokens(row['draft']['instruction'])
        candidates = []
        references = [m['asset'] for m in row['materials'] + row.get('answer_materials', []) if m['kind'] == 'image']
        for asset in row.get('target_pool', []):
            if not self.guard.allows_asset(asset, True) or any(duplicate(asset, ref) for ref in references):
                continue
            if row.get('edit_source') and row['edit_source']['sha256'] == asset['sha256']:
                continue
            score = len(query & tokens(asset.get('description') or '')) / max(1, len(query))
            candidates.append((score, asset))
        ranked = sorted(candidates, key=lambda p: (-p[0], p[1]['sha256']))
        if row.get('design_targets'):
            seed_order = {a['sha256']:n for n,a in enumerate(row['design_targets'])}
            ranked.sort(key=lambda p: (seed_order.get(p[1]['sha256'],len(seed_order)), -p[0], p[1]['sha256']))
        trace = {'query_fields': ['instruction'], 'method': 'caption_lexical_then_pixel_review/1',
                 'ranks': [{'sha256': a['sha256'], 'score': s} for s, a in ranked],
                 'task_sha256': row['task_sha256']}
        if not ranked:
            return fail({**row, 'target_search': trace}, 'needs_target', 'No disjoint source-backed target candidate; not a completed sample')
        return {**row, 'target_search': trace, 'target': {**ranked[0][1], 'role': 'target'}, 'status': 'target_attached'}
