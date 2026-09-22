"""Explicit, late scene/target sourcing. Never part of knowledge delivery."""
from pathlib import Path








def accept_edit_source(row, stage='select_edit_source'):
    if row['status'] != 'edit_source_proposed':
        return row
    from curation.benchmark.authoring import require_texts
    from curation.benchmark.operators import fail
    result = row['edit_source_selection']
    try:
        if result.get('status') == 'insufficient':
            require_texts(result, ['reason'])
            return fail(row, 'needs_edit_source', result['reason'])
        number = result.get('image')
        if result.get('status') != 'ok' or type(number) is not int or not 1 <= number <= len(row['source_candidates']):
            raise ValueError('Selected edit source is not a supplied image')
        if not all(result.get('checks', {}).get(k) is True for k in
                   ('identity_supported', 'existing_image', 'anchor_visible', 'knowledge_change_possible')):
            raise ValueError('Edit-source role checks did not all pass')
        require_texts(result, ['reason', 'anchor', 'selection_evidence'])
        source = row['source_candidates'][number-1]
        source = {**source, 'role': 'edit_source', 'review': {'decision': 'accept',
            'reviewer_kind': row[stage + '_provenance'].get('reviewer_kind', 'model'), 'reviewer': row[stage + '_provenance'].get('model', stage),
            'reason': result['reason'], 'evidence': [result['selection_evidence']], 'scope': result['anchor'],
            'support_scope': result['anchor'], 'identity_checked': True, 'sha256': source['sha256']}}
        return {**row, 'status': 'selected', 'edit_source': source, 'plan': {**row['plan'], 'edit_source': source}}
    except (ValueError, TypeError, KeyError) as error:
        return fail(row, 'needs_edit_source', str(error))
