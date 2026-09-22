"""Image indexing and explicit visual publication, independent of article selection.

Index labels are machine suggestions. Concept support is reviewed from pixels.
Task roles are deliberately absent here; downstream freezes those per dataset.
"""
import json
from pathlib import Path

from curation.preparation.annotation_contracts import validate_description
from curation.preparation.contracts import digest, immutable

from collect.assets import AssetCorrupted, AssetMissing

VISUAL_PROTOCOL = 'concept-visual-publication/1'


def description_valid(value):
    try:
        validate_description(value)
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def index_metadata(image):
    """Reuse SHA-bound labels without promoting them to reviewed knowledge."""
    annotation = image.get('record', {}).get('preannotation') or {}
    sha = image.get('bytes', {}).get('sha256') or image.get('record', {}).get('sha256')
    technical = {k:image['bytes'][k] for k in ('resolution','byte_size','format','dimensions')
                 if k in image.get('bytes', {})}
    if annotation.get('sha256') == sha and description_valid(annotation.get('description')):
        return {**technical, 'status': 'machine_index_only', 'description': annotation['description'],
                'provenance': {k: annotation[k] for k in
                    ('sha256', 'dataset_ref', 'config_id', 'model', 'status', 'record_sha256') if k in annotation}}
    return {**technical, 'status': 'missing', 'description': None}


class ReuseImageAnnotations:
    """Read SHA-bound machine descriptions from one fixed Lance table version."""
    def __init__(self, dataset_ref=None, config_id=None):
        self.config_id = config_id
        self.ref = dataset_ref
        self.dataset = None
        self.cache = {}
        if dataset_ref:
            from demiflow.lance.refs import DatasetRef
            from project import resolve_root
            ref = DatasetRef.from_dict(dataset_ref)
            if ref.schema_name != 'curated_images':
                raise ValueError('Expected a curated_images DatasetRef')
            self.dataset = ref.open(resolve_root())

    def __call__(self, row):
        sha = row.get('sha256') or row.get('byte_details', {}).get('sha256')
        if not sha or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            return {**row, 'preannotation': None, 'image_index_status': 'missing_sha256'}
        existing = row.get('preannotation')
        saved = {'sha256': sha, 'status': 'missing', 'description': None}
        if (existing and existing.get('sha256') == sha and description_valid(existing.get('description'))
                and (self.config_id is None or existing.get('config_id') == self.config_id)):
            saved = dict(existing)
        elif self.dataset is not None:
            if sha not in self.cache:
                records = self.dataset.to_table(columns=['sha256','descriptions'], filter=f"sha256 = '{sha}'").to_pylist()
                if len(records) > 1: raise ValueError('Duplicate annotation SHA: ' + sha)
                self.cache[sha] = records[0] if records else None
            record = self.cache[sha]
            if record:
                descriptions = [r for r in record.get('descriptions') or []
                                if r['status'] == 'done' and description_valid(r.get('description'))
                                and (self.config_id is None or r['config_id'] == self.config_id)]
                if len(descriptions) > 1:
                    raise ValueError('Multiple valid descriptions require an explicit annotation selection')
                if descriptions:
                    annotation = descriptions[0]
                    saved.update(status=annotation['status'], description=annotation['description'],
                                 dataset_ref=self.ref, config_id=annotation['config_id'],
                                 author_type='model', human_reviewed=False)
        saved.pop('record_sha256', None)
        saved['record_sha256'] = digest(saved)
        return {**row, 'preannotation': saved, 'image_index_status':
                'available' if description_valid(saved.get('description')) else 'missing'}


def valid_visual_result(item, prompt):
    metadata = item.get('image_metadata')
    if item['image_id'] in prompt.get('metadata_required_ids', []) and not description_valid(metadata):
        return False
    if metadata is not None and not description_valid(metadata):
        return False
    support = item.get('visual_support')
    if item.get('decision') != 'keep':
        return support is None
    return (isinstance(support, dict)
            and all(isinstance(support.get(k), str) and support[k].strip() for k in ('supports', 'region'))
            and isinstance(support.get('limitations'), str))


def confirmed_publication(row, primary, review):
    if row['image_prompt'].get('selection_protocol') != VISUAL_PROTOCOL:
        return None  # Old keep decisions cannot acquire the new publication status.
    roles = {r['image_id']: r for r in row.get('pixel_roles', [])}
    sha = roles.get(review['image_id'], {}).get('original_sha256')
    if not sha:
        return None
    metadata = row.get('image_index', {}).get(review['image_id'], {'status': 'missing'})
    if metadata.get('status') == 'missing':
        metadata = {**metadata, 'status': 'machine_index_only', 'description': review['image_metadata'],
                    'provenance': {'stage': 'independent_visual_review', 'sha256': sha}}
    return {'schema': VISUAL_PROTOCOL, 'status': 'reviewed', 'sha256': sha,
            'metadata': metadata, 'support': review['visual_support'],
            'identity_reason': review['reason'], 'visible_information': review['visible_information'],
            'primary_support': primary['visual_support'],
            'review_calls': row['primary_selection']['image_selection_calls'] +
                [{'call': row.get('prompt_call'), 'error': row.get('prompt_error'), 'pixel_roles': row.get('pixel_roles', [])}],
            'review_note': 'Two models accepted concept identity; support is the independent reviewer observation, not human certification.'}


class PublishVisualMaterials:
    """Release only explicitly reviewed, pixel-bound visual records."""
    def __call__(self, row):
        visuals, issues, seen = [], [], set()
        for image in row.get('material_pack', {}).get('images', []):
            decision = image.get('selection_review', {})
            publication = decision.get('visual_publication') or {}
            info = image.get('bytes', {})
            if (publication.get('schema') != VISUAL_PROTOCOL or publication.get('status') != 'reviewed'
                    or publication.get('sha256') != info.get('sha256')
                    or decision.get('decision') != 'keep' or not decision.get('protocol_valid')):
                issues.append({'image_id': image['image_id'], 'reason': 'No explicit V2 visual publication review'})
                continue
            from curation.preparation.asset_io import asset_bytes
            try:
                _data, _origin = asset_bytes(info.get('path'), info['sha256'])
            except AssetMissing:
                issues.append({'image_id': image['image_id'], 'reason': 'Published visual bytes missing'})
                continue
            except AssetCorrupted:
                raise ValueError('Visual publication pixels changed')
            if info.get('generation_origin', image.get('record', {}).get('generation_origin')) in {'generated', 'synthetic', 'ai_generated'}:
                issues.append({'image_id': image['image_id'], 'reason': 'Known generated image is not a knowledge reference'})
                continue
            if info['sha256'] in seen:
                continue
            seen.add(info['sha256'])
            visuals.append({'image_id': image['image_id'], 'concept': row['identity']['target_label'],
                            'image': image, 'publication': publication})
        return {**row, 'visual_materials': visuals, 'visual_publication_issues': issues}


class ExportVisualMetadata:
    """Derived per-concept/image metadata, including unapproved candidates.

    Machine descriptions remain indexing hints. Publication status is separate.
    This never writes the authoritative dataset meta or the legacy annotation DB.
    """
    def __call__(self, row):
        from curation.preparation.ops.identity import material_id
        decisions = {d['image_id']:d for d in row.get('image_decisions', [])}
        publications = {v['image_id']:v['publication'] for v in row.get('visual_materials', [])}
        seen = set()
        for image in row.get('cleaned_materials', []):
            if image['kind'] not in {'legacy_images','qid_images'}:
                continue
            iid = 'I'+material_id(image)[1:]
            record, info = image['record'], image.get('bytes', {})
            sha = info.get('sha256') or record.get('sha256')
            if (sha or iid) in seen:
                continue
            seen.add(sha or iid)
            decision = decisions.get(iid, {})
            publication = publications.get(iid)
            metadata = index_metadata(image)
            if metadata['status'] == 'missing':
                # Even an excluded image may have useful machine indexing labels.
                for stage, value in [('independent_visual_review', decision.get('independent_review', decision)),
                                     ('primary_visual_review', decision.get('primary_review', decision))]:
                    if value.get('protocol_valid') and description_valid(value.get('image_metadata')):
                        metadata = {**metadata, 'status':'machine_index_only',
                                    'description':value['image_metadata'],
                                    'provenance':{'sha256':sha,'stage':stage}}
                        break
            yield {'schema':'visual-image-meta/1', 'pipeline_version':'V2',
                   'concept':row['identity']['target_label'], 'image_id':iid, 'sha256':sha,
                   'path':info.get('path'), 'byte_status':info.get('status'),
                   'resolution':info.get('resolution'), 'byte_size':info.get('byte_size'),
                   'format':info.get('format'), 'image_metadata':metadata,
                   'source':{k:record[k] for k in ('url','landing_url','content_url','license','author','sources') if record.get(k)},
                   'concept_review':decision, 'publication_status':'reviewed' if publication else 'not_published',
                   'visual_support':publication.get('support') if publication else None,
                   'human_reviewed':False, 'task_roles':None}
