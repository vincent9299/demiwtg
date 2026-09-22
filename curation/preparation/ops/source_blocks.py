"""Select intact source text, then compare sources without rewriting statements.

Pure row/flat-map/reduce operators; demiflow owns scheduling, calls and sinks.
This branch starts at the existing identity boundary, not historical clean_docs.
"""

from curation.preparation.ops.prompt_loader import load_instruction
from itertools import combinations_with_replacement
import re

from curation.preparation.contracts import digest
from curation.preparation.ops.identity import family
from curation.preparation.ops.passage_selection import reference_notes


SELECT_BLOCKS = load_instruction("select_blocks")

SELECT_BLOCKS_STRICT = load_instruction("select_blocks_strict")


from curation.preparation.ops.body_text import block_role


COMPARE_BLOCKS = load_instruction("compare_blocks")


def pack_whole(items, max_chars, text=lambda x: x['text']):
    """Soft per-request target, never a corpus truncation or oversized-block drop."""
    if not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError('batch character target must be positive')
    batch, count = [], 0
    for item in items:
        size = len(text(item))
        if batch and count + size > max_chars:
            yield batch
            batch, count = [], 0
        batch.append(item)
        count += size
    if batch:
        yield batch


class BuildSourceBlocks:
    """Identity row -> all accepted document blocks + explicit excluded scope.

    Units use contiguous blocks within one section. Original offsets and all
    source variants survive; no two-document limit, ranking or family deletion.
    """
    def __init__(self, unit_chars=1800, body_only=False):
        self.unit_chars = unit_chars
        self.body_only = body_only

    def __call__(self, row):
        if row.get('blocked'):
            return {**row, 'source_units': [], 'block_scope': {'upstream_blocked': row['blocked']}}
        accepted = set(row['identity']['accepted_material_ids'])
        units, scope = [], []
        for m in row['identity_materials']:
            if 'cleaning' not in m or m['material_id'] not in accepted:
                continue
            c = m['cleaning']
            # Trust offsets only after verifying every retained source block.
            kept = [b for b in c['blocks'] if b.get('decision') == 'keep' and 'clean_start' in b]
            for b in kept:
                if c['text'][b['clean_start']:b['clean_end']] != b['text']:
                    raise ValueError('cleaned block offsets changed')
            sections, filtered = [], []
            boundary = True
            for b in kept:
                role = block_role(b)
                if self.body_only and role != 'body_candidate':
                    filtered.append({'block_id': b['block_id'], 'role': role,
                        'start': b['clean_start'], 'end': b['clean_end'],
                        'reason': 'Outside strict body-first input; original retained for recovery'})
                    boundary = True
                    continue
                # A filtered block creates a hard boundary: never include it again
                # by taking a contiguous range across the removed control/card.
                if boundary or not sections or sections[-1][0] != b.get('section', []):
                    sections.append((b.get('section', []), []))
                sections[-1][1].append(b)
                boundary = False
            for section, blocks in sections:
                for group in pack_whole(blocks, self.unit_chars):
                    if self.body_only and all(b.get('kind') == 'heading' for b in group):
                        filtered.extend({'block_id': b['block_id'], 'role': 'heading_metadata',
                            'start': b['clean_start'], 'end': b['clean_end'],
                            'reason': 'Heading kept as section metadata, not a standalone knowledge candidate'} for b in group)
                        continue
                    start, end = group[0]['clean_start'], group[-1]['clean_end']
                    first, last = blocks.index(group[0]), blocks.index(group[-1])
                    uid = 'U' + digest({'material': m['material_id'], 'start': start, 'end': end,
                                         'clean_sha': digest(c['text'].encode())})[:16]
                    units.append({'unit_id': uid, 'source_id': uid, 'material_id': m['material_id'],
                        'text': c['text'][start:end], 'start': start, 'end': end,
                        'sections': section, 'source_family': family(m),
                        'title': m['record'].get('title'), 'provenance': m['provenance'],
                        'source_locator': c['source_locator'], 'source_blocks': group,
                        'reference_notes': reference_notes(c, group),
                        'document_sha256': digest(c['text'].encode()),
                        'raw_source_sha256': c['source_sha256'], 'cleaning_version': c['version'],
                        'quote_basis': 'cleaned_text', 'selection': 'all_accepted_source_blocks',
                        'context_before': [b['text'] for b in blocks[max(0, first-1):first]],
                        'context_after': [b['text'] for b in blocks[last+1:last+2]]})
            scope.append({'material_id': m['material_id'], 'title': m['record'].get('title'),
                'retained_blocks': len(kept), 'retained_chars': sum(len(b['text']) for b in kept),
                'structurally_filtered': filtered,
                'cleaning_excluded_or_deferred': [b for b in c['blocks'] if b.get('decision') != 'keep']})
        return {**row, 'source_units': units, 'block_scope': {
            'documents': scope, 'identity_unexamined': row.get('identity_unexamined', []),
            'identity_ineligible': row.get('identity_ineligible', []),
            'scope': ('Body-first structural selection; all excluded blocks recorded. ' if self.body_only
                      else 'All kept blocks in accepted documents. ') + 'Identity-accepted documents of THIS material batch only. '
                     'No full identity, raw-markup, cross-material-batch or truth certification.'}}


def model_unit(u):
    return {k: u[k] for k in ('unit_id', 'text', 'sections', 'source_family', 'title',
                               'context_before', 'context_after', 'reference_notes')}


class BatchSourceBlocks:
    """Concept row -> bounded request rows; original concept row stays in its Dataset."""
    def __init__(self, batch_chars=8000):
        self.batch_chars = batch_chars

    def __call__(self, row):
        if row.get('blocked'):
            return []
        return [{'case_id': row['case_id'], 'batch_id': f"{row['case_id']}:blocks:{i}",
                 'block_prompt': {'concept': row['identity']['target_label'],
                                  'units': [model_unit(u) for u in group]}}
                for i, group in enumerate(pack_whole(row['source_units'], self.batch_chars))]


class ApplyBlockSelection:
    """Validate one decision per source unit. Missing/invalid decisions defer locally."""
    def __init__(self, strict=False, relevance_only=False):
        self.strict = strict
        self.relevance_only = relevance_only

    def __call__(self, row):
        supplied = {u['unit_id']: u for u in row['block_prompt']['units']}
        result = row.get('prompt_result') or {}
        decisions = result.get('decisions', [])
        if not isinstance(decisions, list):
            decisions = []
        by_id = {}
        for d in decisions:
            if isinstance(d, dict) and isinstance(d.get('unit_id'), str):
                by_id.setdefault(d.get('unit_id'), []).append(d)
        out = []
        for uid in supplied:
            items = by_id.get(uid, [])
            if len(items) == 1 and 'source_check_needed' not in items[0]:
                # Missing optional source-risk metadata is NOT a truth approval.
                # Require source review explicitly; never fill scores or decisions.
                items = [{**items[0], 'source_check_needed': True,
                          'protocol_notes': ['Missing source_check_needed; program defaults to requiring source review']}]
            valid = (not row.get('prompt_error') and len(items) == 1
                     and items[0].get('decision') in {'selected', 'deferred', 'excluded'}
                     and isinstance(items[0].get('source_check_needed'), bool)
                     and isinstance(items[0].get('reason'), str) and bool(items[0]['reason'].strip()))
            if valid and self.relevance_only:
                relation=items[0].get('relation')
                expected={'direct':'selected','background':'selected','unrelated':'excluded','uncertain':'deferred'}
                valid=relation in expected and items[0]['decision']==expected.get(relation)
            if valid and self.strict:
                d = items[0]
                valid = (all(type(d.get(k)) is int and 0 <= d[k] <= 3 for k in ('relevance_score', 'usability_score'))
                         and all(type(d.get(k)) is bool for k in ('standalone', 'mixed_content')))
                if valid and d['decision'] == 'selected' and not (
                        d['relevance_score'] == d['usability_score'] == 3
                        and d['standalone'] and not d['mixed_content']):
                    items = [{**d, 'decision': 'deferred', 'model_decision': 'selected',
                              'reason': 'Strict admission checks not met: ' + d['reason']}]
                if valid and items[0]['decision'] == 'selected' and re.search(
                        r'(?m)^\s*\[(?:编辑|編輯|edit)\](?:\s|$)', supplied[uid]['text']):
                    items = [{**items[0], 'decision': 'deferred', 'model_decision': 'selected',
                              'reason': '编辑按钮仍与正文/表格粘连，需解析外壳后重新选择；保留原表，不判知识无效。'}]
                if valid and items[0]['decision'] == 'selected' and re.search(
                        r'!\[[^\]\n]*\]|<img\b', supplied[uid]['text'], flags=re.I):
                    items = [{**items[0], 'decision': 'deferred', 'model_decision': 'selected',
                              'reason': '正文混入图片标记或图注，需拆分文本与图片材料；原文和图片线索保留。'}]
            out.append({**items[0], 'protocol_valid': True} if valid else {
                'unit_id': uid, 'decision': 'deferred', 'reason': 'Missing/invalid model decision',
                'source_check_needed': True, 'protocol_valid': False})
        return {'case_id': row['case_id'], 'block_decisions': out,
                'block_calls': [{'batch_id': row['batch_id'], 'call': row.get('prompt_call'),
                                 'error': row.get('prompt_error'), 'raw_result': result,
                                 'unknown_ids': [uid for uid in by_id if uid not in supplied]}]}


def merge_block_decisions(acc, row):
    if acc is None:
        return row
    return {'case_id': row['case_id'], 'block_decisions': acc['block_decisions'] + row['block_decisions'],
            'block_calls': acc['block_calls'] + row['block_calls']}


class BuildVerbatimCandidates:
    """Source units + decisions -> statements copied by code, never model prose."""
    def __call__(self, row):
        decisions = {d['unit_id']: d for d in row.get('block_decisions', [])}
        facts, deferred, exclusions = [], [], []
        for u in row['source_units']:
            d = decisions.get(u['unit_id'], {'decision': 'deferred', 'reason': 'No selection result'})
            f = {'fact_id': 'K' + u['unit_id'][1:], 'statement': u['text'],
                 'conditions': [], 'exceptions': [],
                 'condition_storage': 'Inline in verbatim statement; empty arrays do not mean no conditions',
                 'statement_mode': 'verbatim_source_block', 'sections': u['sections'],
                 'evidence': [{'source_id': u['source_id'], 'quote': u['text']}],
                 'selection_review': d, 'truth_status': 'not_verified'}
            if d['decision'] == 'selected':
                facts.append(f)
            elif d['decision'] == 'deferred':
                deferred.append({'fact': f, 'reasons': [d['reason']],
                                 'next_action': 'Review original block and required context'})
            else:
                exclusions.append({'source_id': u['source_id'], 'reason': d['reason']})
        return {**row, 'material_pack': {'passages': row['source_units'], 'images': [],
                'image_gaps': [], 'duplicates': [], 'omissions': exclusions,
                'coverage': row['block_scope']['scope'] if 'scope' in row['block_scope'] else 'upstream blocked'},
                'knowledge': {'facts': facts, 'deferred_facts': deferred, 'unresolved_conflicts': [],
                    'coverage_note': 'Verbatim candidates, not factual approval. Conditions remain inline.'},
                'image_evidence': {'status': 'not_run', 'reason': 'New text candidates require new scoped pixel checks'},
                'knowledge_review_status': 'source_selection_complete; cross-source review pending'}


class BatchSourceComparisons:
    """Cover all retained/deferred candidate pairs using pairs of bounded groups.

    No topic classifier can silently hide cross-source differences. Quadratic
    group count is explicit; this pilot is not a scalable semantic index.
    """
    def __init__(self, group_chars=16000):
        self.group_chars = group_chars

    def __call__(self, row):
        if row.get('blocked'):
            return []
        sources = {u['source_id']: u for u in row['source_units']}
        facts = row['knowledge']['facts'] + [d['fact'] for d in row['knowledge']['deferred_facts']]
        items = [{**model_unit(sources[f['evidence'][0]['source_id']]), 'fact_id': f['fact_id']} for f in facts]
        groups = list(pack_whole(items, self.group_chars))
        requests = []
        for a, b in combinations_with_replacement(range(len(groups)), 2):
            group = groups[a] if a == b else groups[a] + groups[b]
            requests.append({'case_id': row['case_id'], 'batch_id': f"{row['case_id']}:compare:{a}:{b}",
                'compare_prompt': {'concept': row['identity']['target_label'], 'units': group}})
        return requests


class ApplySourceComparison:
    """Keep model relations, validate IDs; failed comparisons stay explicit."""
    def __call__(self, row):
        known = {u['fact_id'] for u in row['compare_prompt']['units']}
        result = row.get('prompt_result') or {}
        error = row.get('prompt_error')
        pairs, issues = result.get('pairs'), result.get('issues')
        valid = not error and isinstance(pairs, list) and isinstance(issues, list)
        if valid:
            valid = all(isinstance(p, dict) and isinstance(p.get('fact_ids'), list)
                and len(p['fact_ids']) == 2 and all(isinstance(x, str) for x in p['fact_ids'])
                and len(set(p['fact_ids'])) == 2 and set(p['fact_ids']) <= known
                and p.get('kind') in {'duplicate', 'complement', 'condition_difference', 'potential_conflict'}
                and isinstance(p.get('reason'), str) and bool(p['reason'].strip()) for p in pairs)
            valid = valid and all(isinstance(i, dict) and isinstance(i.get('fact_id'), str) and i['fact_id'] in known
                and isinstance(i.get('reason'), str) and bool(i['reason'].strip()) for i in issues)
        return {'case_id': row['case_id'], 'source_comparisons': [{
            'batch_id': row['batch_id'], 'fact_ids': sorted(known), 'protocol_valid': bool(valid),
            'result': result, 'error': error, 'call': row.get('prompt_call')}]}


def merge_source_comparisons(acc, row):
    return row if acc is None else {'case_id': row['case_id'],
        'source_comparisons': acc['source_comparisons'] + row['source_comparisons']}


class ApplyComparedCandidates:
    """Quarantine disputed/incomplete comparisons; never rewrite or auto-promote."""
    def __call__(self, row):
        knowledge = row['knowledge']
        all_facts = knowledge['facts'] + [d['fact'] for d in knowledge['deferred_facts']]
        active_ids = {f['fact_id'] for f in all_facts}
        reasons, conflicts, relations, seen = {}, [], [], set()
        compared_pairs, compared_ids = set(), set()
        for review in row.get('source_comparisons', []):
            ids = [fid for fid in review['fact_ids'] if fid in active_ids]
            if not review['protocol_valid']:
                for fid in ids:
                    reasons.setdefault(fid, []).append('Cross-source comparison failed protocol validation')
                continue
            compared_ids.update(ids)
            compared_pairs.update(tuple(sorted((a, b))) for i, a in enumerate(ids) for b in ids[i+1:])
            result = review['result']
            for issue in result['issues']:
                if issue['fact_id'] not in active_ids:
                    continue
                reasons.setdefault(issue['fact_id'], []).append(issue['reason'])
            for pair in result['pairs']:
                if not set(pair['fact_ids']) <= active_ids:
                    continue  # Full historical result remains in source_comparisons.
                key = (tuple(sorted(pair['fact_ids'])), pair['kind'], pair['reason'])
                if key in seen:
                    continue
                seen.add(key)
                relations.append(pair)
                if pair['kind'] == 'potential_conflict':
                    cid = 'C' + digest(pair)[:16]
                    conflicts.append({'conflict_id': cid, 'affected_fact_ids': pair['fact_ids'],
                                      'reason': pair['reason'], 'status': 'potential_difference_needs_source_review'})
                    for fid in pair['fact_ids']:
                        reasons.setdefault(fid, []).append({'conflict_id': cid, 'basis': pair['reason']})
        ids = [f['fact_id'] for f in all_facts]
        for i, fid in enumerate(ids):
            if fid not in compared_ids:
                reasons.setdefault(fid, []).append('Source comparison missing')
            for other in ids[i+1:]:
                if tuple(sorted((fid, other))) not in compared_pairs:
                    for affected in (fid, other):
                        if 'Source pair coverage incomplete' not in reasons.setdefault(affected, []):
                            reasons[affected].append('Source pair coverage incomplete')
        deferred = [{**d, 'reasons': d['reasons'] + reasons.get(d['fact']['fact_id'], [])}
                    for d in knowledge['deferred_facts']]
        facts = []
        for f in knowledge['facts']:
            if reasons.get(f['fact_id']):
                deferred.append({'fact': f, 'reasons': reasons[f['fact_id']],
                                 'next_action': 'Review source context and comparison; preserve original statement'})
            else:
                facts.append(f)
        return {**row, 'knowledge': {**knowledge, 'facts': facts, 'deferred_facts': deferred,
                'unresolved_conflicts': conflicts, 'source_relations': relations},
                'knowledge_review_status': 'machine_source_block_candidates; not independent truth verification',
                'block_comparison_coverage': {'candidate_count': len(ids),
                    'expected_pairs': len(ids)*(len(ids)-1)//2, 'validated_pairs': len(compared_pairs)}}
