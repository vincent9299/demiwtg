"""Candidate images from explicitly declared publications; no scene search."""
from pathlib import Path

from preparation.operaters.inputs import pixels
from preparation.operaters.inputs import source_asset
from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import run_records


def candidate_images(record):
    """Use published figures/visuals, never the article's raw provenance pool."""
    seen = set()
    values = []
    for visual in record.get('visual_materials', []):
        publication = visual.get('publication', {})
        image = visual.get('image', {})
        support = publication.get('support', {})
        if (visual.get('concept') == record['concept']
                and publication.get('schema') == 'concept-visual-publication/1'
                and publication.get('status') == 'reviewed'
                and publication.get('sha256') == image.get('bytes', {}).get('sha256')
                and support.get('supports') and support.get('region')):
            values.append(image)
    if record.get('status') == 'reviewed' and not record.get('audit', {}).get('preflight_error'):
        selected = set(record.get('audit', {}).get('selected_image_ids', []))
        placed = {p['image_id'] for topic in record.get('knowledge', [])
                  for p in topic.get('content', {}).get('images', [])}
        values.extend(image for image in record.get('published_images', [])
                      if (image.get('image_id') or image.get('record', {}).get('image_id')) in selected & placed)
    for image in values:
        sha = image.get('bytes', {}).get('sha256')
        if sha not in seen:
            seen.add(sha)
            yield image


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
        from preparation.operaters.inputs import iter_material_rows
        images = []
        for spec in source['specs']:
            records, _ = iter_material_rows(spec, concepts=[row['concept']])
            images.extend((image, record, spec) for record in records if record['concept'] == row['concept']
                          for image in candidate_images(record))
        candidates, excluded, inspected = [], [], 0
        for image, record, spec in images:
            info = image.get('bytes', {})
            sha = info.get('sha256')
            try:
                if sha:
                    from preparation.operaters.images import asset_resolution
                    resolution = asset_resolution(info.get('path'), sha)
                    # 分态留档：ok/corrupt/missing/read_error 不互相吞并
                    run_records(self.run).put('observed_asset/' + sha + '/' + digest([info.get('path'), spec])[:16],
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
                    'status': 'reviewed_visual' if visual else 'published_article_figure',
                    'support': visual['publication'].get('support', {}) if visual else {},
                    'task_target_reviewed': False}
                asset['dhash'] = pixels(asset)[2]
                if not self.guard.allows_asset(asset, row['branch'] == 'training'):
                    excluded.append({'sha256': sha, 'reason': 'Formal-test reservation'})
                    continue
                # Dedup exact bytes here; target/reference near-duplicates are
                # excluded per sample when the roles have actually been chosen.
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
