"""Explicit, late scene/target sourcing. Never part of knowledge delivery."""
from pathlib import Path

from curation.preparation.materials import pixels
from curation.preparation.published import source_asset
from curation.preparation.records import digest, run_records



class ExistingImagePool:
    def __init__(self, run, guard, config):
        self.run, self.guard, self.config = Path(run), guard, config
        self.cache = {}

    def load(self, row, role):
        """Open only the declared source metadata and the role's eligible pixels."""
        source = row['asset_source']
        key = (digest(source), row['concept'], row['branch'], role)
        if key in self.cache:
            return self.cache[key]
        from curation.preparation.publication_sources import iter_publication_rows
        images = []
        for spec in source['specs']:
            records, _ = iter_publication_rows(spec)
            images.extend((image, record, spec) for record in records if record['concept'] == row['concept']
                          for image in record.get('images', []))
        candidates, excluded, inspected = [], [], 0
        for image, record, spec in images:
            info = image.get('bytes', {})
            sha = info.get('sha256')
            try:
                if sha:
                    from curation.preparation.asset_io import asset_resolution
                    resolution = asset_resolution(info.get('path'), sha)
                    # 分态留档：ok/corrupt/missing/read_error 不互相吞并
                    run_records(self.run).put('observed_asset/' + sha,
                              {'path': info.get('path'),
                               'actual_sha256': resolution.actual_sha256 if resolution.status == 'ok' else None,
                               'expected_sha256': sha, 'asset_status': resolution.status,
                               'asset_source': resolution.source})
                    inspected += int(resolution.status == 'ok')
                asset = source_asset(image)
                if not asset:
                    excluded.append({'sha256': sha, 'reason': 'Missing bytes/provenance or known generated source'})
                    continue
                visual = next((v for v in record.get('visual_materials', [])
                               if v.get('publication', {}).get('sha256') == asset['sha256']
                               and v.get('publication', {}).get('status') == 'reviewed'), None)
                asset['candidate_publication'] = {
                    'source_ref': spec,
                    'status': 'reviewed_visual' if visual else 'publication_carried_candidate',
                    'support': visual['publication'].get('support', {}) if visual else {},
                    'task_target_reviewed': False}
                asset['dhash'] = pixels(asset)[2]
                if not self.guard.allows_asset(asset, row['branch'] == 'training'):
                    excluded.append({'sha256': sha, 'reason': 'Formal-test reservation'})
                    continue
                # Keep possible near-identical before/after pairs, dedup exact bytes.
                prior = next((a for a in candidates if a['sha256'] == asset['sha256']), None)
                if prior is None:
                    asset['candidate_publications'] = [asset['candidate_publication']]
                    candidates.append(asset)
                else:
                    prior['candidate_publications'].append(asset['candidate_publication'])
                    if asset['candidate_publication']['status'] == 'reviewed_visual':
                        prior['candidate_publication'] = asset['candidate_publication']
            except (ValueError, OSError) as error:
                excluded.append({'sha256': sha, 'reason': str(error)})
        trace = {'source': source, 'role': role, 'metadata_records': len(images), 'pixels_inspected': inspected,
                 'partition': 'shared_assets_task_local_roles',
                 'excluded': excluded}
        self.cache[key] = candidates, trace
        return candidates, trace




def accept_edit_source(row, stage='select_edit_source'):
    if row['status'] != 'edit_source_proposed':
        return row
    from curation.training.authoring import require_texts
    from curation.training.operators import fail
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
