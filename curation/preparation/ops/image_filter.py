"""Independent second-model image selection, with original batches preserved."""
from curation.preparation.ops.image_selection import ApplyImageSelection

IMAGE_FILTER_DEFAULTS = {
    'image_review_model': 'gemma-4-31b-it',
    'image_review_base_url': 'http://127.0.0.1:8001/v1',
    'image_review_service': 'borrow',
    'image_identity_definitions': {},
    'image_filter_output_tokens': 8192,
    'image_review_concurrency': 2,
}
IMAGE_FILTER_POLICY = 'v2-qwen38-gemma31-visual-publication-1'


class RecordPrimaryImageSelection:
    def __call__(self, row):
        return {**row, 'primary_selection': ApplyImageSelection()(row)}


class PrepareImageReview:
    """Same pixels, IDs and neutral payload; primary decisions never enter the prompt."""
    def __call__(self, row):
        return {**{k: row[k] for k in ['case_id', 'batch_id', 'image_prompt', 'pixel_images', 'pixel_roles']},
                'image_index': row.get('image_index', {}),
                'primary_selection': row['primary_selection'],
                'review_required': any(d.get('protocol_valid') and d.get('decision') == 'keep'
                                       for d in row['primary_selection']['image_decisions'])}


class ApplyConfirmedImageSelection:
    def __call__(self, row):
        first = row['primary_selection']
        second = ApplyImageSelection()(row) if row['review_required'] else None
        by_id = {d['image_id']: d for d in second['image_decisions']} if second else {}
        decisions = []
        for primary in first['image_decisions']:
            if not primary.get('protocol_valid') or primary.get('decision') != 'keep':
                decisions.append(primary)
                continue
            review = by_id.get(primary['image_id'], {})
            if review.get('protocol_valid') and review.get('decision') == 'keep':
                final = dict(review)
                from curation.preparation.ops.visual_materials import confirmed_publication
                publication = confirmed_publication(row, primary, review)
                if publication:
                    final['visual_publication'] = publication
            else:
                final = {'image_id': primary['image_id'], 'decision': 'pending',
                         'concept_relation': 'uncertain', 'relation': 'uncertain',
                         'observability': review.get('observability', 'unknown'),
                         'protocol_valid': bool(review.get('protocol_valid')),
                         'reason': 'Independent image review disagreed or was unavailable; not selected for joint extraction.',
                         'visible_information': review.get('visible_information', ''),
                         'limitations': 'Identity/relevance remains unresolved.'}
            decisions.append({**final, 'primary_review': primary, 'independent_review': review})
        return {'case_id': row['case_id'], 'batch_id': row['batch_id'],
                'image_decisions': decisions,
                'image_selection_calls': first['image_selection_calls'] + (second['image_selection_calls'] if second else []),
                'image_filter_policy': IMAGE_FILTER_POLICY}
