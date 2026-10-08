"""固定审定名单的单行消费；概念、文档与正例引用保留上游身份。"""
import json

from demiflow.collect.documents import read_document
from .images import image_object_ref


def cohort_input(row, *, source, max_reference_images):
    record = row['concept_record']
    if (not record or row['concept_id'] != record['concept_id']
            or row['assessment_id'] != record['assessment_id']
            or not record['canonical_name'].strip() or not record['definition'].strip()):
        raise ValueError('Cohort concept/assessment identity or definition is missing')
    references = [{'kind': 'concept', 'concept': record['canonical_name'],
        'definition': record['definition'], 'original_name': record['original_name'],
        'concept_id': record['concept_id'], 'assessment_id': record['assessment_id'],
        'adopted_source': record['adopted_source'], 'cohort_source': source,
        'selection_rank': row['selection_rank']}]
    by_id = {item['evidence_id']: item for item in record['evidence']}
    ids = list(dict.fromkeys([*record['identity_evidence_ids'],
        *(eid for fact in record['core_facts'] for eid in fact['evidence_ids'])]))
    if len(ids) > 1024:
        raise ValueError('Cohort exceeds 1024 evidence references per concept')
    documents = {}
    for i, eid in enumerate(ids, 1):
        item = by_id[eid]
        ref = item['document_ref']
        if eid != ref['sha256'] + ':' + item['block_id']:
            raise ValueError('Cohort evidence does not match its document/block')
        key = (ref['uri'], ref['sha256'])
        if key not in documents:
            if len(documents) >= 64:
                raise ValueError('Cohort exceeds 64 documents per concept')
            document = read_document(ref)  # 单行固定对象、最多 8 MiB；正文不进入模型初始输入。
            documents[key] = {'ref': ref, 'source': document['source'],
                'blocks': {block['block_id'] for block in document['blocks']}, 'evidence': []}
        if item['block_id'] not in documents[key]['blocks']:
            raise ValueError('Cohort evidence block is absent from the fixed document')
        documents[key]['evidence'].append({'id': f'E{i}', 'block_id': item['block_id']})
    for i, doc in enumerate(documents.values(), 1):
        references.append({'kind': 'document', 'document_id': f'D{i}', 'document': {
            'document_ref': doc['ref'], 'url': doc['source']['url'],
            'title': doc['source']['title'], 'evidence': doc['evidence']}})
    images = row['positive_images'] or []
    if not images:
        raise ValueError('Audited cohort has no positive images')
    for image in images[:max_reference_images]:
        if not image['review_sources']:
            raise ValueError('Cohort positive image lacks its audit source')
        references.append({'kind': 'image', 'role': 'concept_reference',
            'object_ref': image_object_ref(image), 'review_sources': image['review_sources']})
    return {'concept': record['canonical_name'], 'taxonomy': row['taxonomy'] or [],
        'status': 'ready', 'reason': '', 'references_json': json.dumps([
            {'number': i, **ref} for i, ref in enumerate(references, 1)], ensure_ascii=False)}
