"""Source blocks and relevance decisions; no extraction or cross-source comparison."""
from collections import OrderedDict


def reference_notes(cleaning, blocks):
    definitions={r['reference_id']:r for b in cleaning['blocks'] for r in b.get('references',[])
                 if r.get('reference_id') and (r.get('content') or r.get('parameters'))}
    result={}
    for b in blocks:
        for r in b.get('references',[]):
            key=r.get('reference_id')
            if key:result[key]=definitions.get(key,r)
    return list(result.values())


from preparation.prompts import load_instruction
from itertools import combinations_with_replacement
import re

from demiflow.execution.artifacts import digest
from preparation.operaters.identity import family


from preparation.operaters.documents import block_role


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


