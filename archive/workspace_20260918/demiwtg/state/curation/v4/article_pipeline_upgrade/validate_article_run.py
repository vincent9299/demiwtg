"""Audit stored article evidence and publication mappings; no model calls."""
import collections
import hashlib
import json
from pathlib import Path
import re
import sys


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate(run, *, diagnostic_no_preflight=False):
    run = Path(run)
    if diagnostic_no_preflight:
        policy = json.loads((run / 'capacity_policy.json').read_text())
        assert policy['not_formal_token_budget_validation'] is True and policy['truncate_prompt_tokens'] is None
    drafts = rows(run / 'datasets/paragraph_extract.jsonl')
    requests = rows(run / 'datasets/final_review_requests.jsonl')
    finals = rows(run / 'datasets/final_review.jsonl')
    published = rows(run / 'knowledge_base.jsonl')
    pool = collections.defaultdict(lambda: {'sources': set(), 'images': set()})
    for draft in drafts:
        assert draft['article_status'] in ('draft', 'reviewed', 'empty', 'valid', 'no_supported_knowledge'), draft['article_status']
        assert not draft['validation_issues']
        for topic in draft['article_topics']:
            for para in topic['paragraphs']:
                for kind, number in para['refs']:
                    key = 'sources' if kind == '资料' else 'images'
                    value = draft['source_catalog'][number - 1]['source_id'] if kind == '资料' else draft['article_image_ids'][number - 1]
                    pool[draft['concept']][key].add(value)
            for image in topic['images']:
                pool[draft['concept']]['images'].add(draft['article_image_ids'][image['number'] - 1])
    report = {'run': str(run), 'evidence_pools': [], 'publication': [], 'calls': 0, 'stages': {}, 'usage': {}}
    for request in requests:
        concept = request['concept']
        sources = {s['source_id'] for s in request['source_catalog']}
        images = set(request['article_image_ids'])
        assert sources == pool[concept]['sources'], concept
        assert images == pool[concept]['images'], concept
        if not diagnostic_no_preflight:
            assert request['input_token_budget'] <= request['input_token_limit']
        assert not request['preflight_error']
        report['evidence_pools'].append({'concept': concept, 'cited_sources_only': len(sources), 'adopted_images_only': len(images), 'input_token_budget': request.get('input_token_budget'), 'input_token_limit': request.get('input_token_limit')})
    by_concept = {r['concept']: r for r in finals}
    for row in published:
        figures = []
        for topic in row['knowledge']:
            content = topic['content']
            text = '\n'.join([topic['title'], *content['paragraphs'], *(i['caption'] for i in content['images'])])
            assert not re.search(r'\b[IUM][0-9a-f]{10,}\b|https?://|【资料\d+】|【图\d+】', text), row['concept']
            covered = set()
            for ref in topic['references']:
                covered.update(ref.get('paragraph_indices', []))
                if 'image' in ref.get('kinds', []):
                    assert Path(ref['local_path']).is_file()
                for sid in ref['source_ids']:
                    assert sid in pool[row['concept']]['sources'] | pool[row['concept']]['images'], sid
            assert covered == set(range(len(content['paragraphs']))), (row['concept'], topic['title'], covered)
            for image in content['images']:
                assert 0 <= image['paragraph_index'] < len(content['paragraphs'])
                assert image['image_id'] in pool[row['concept']]['images']
                figures.append((image['figure_number'], image['image_id']))
        assert [f[0] for f in figures] == list(range(1, len(figures) + 1))
        assert len({f[1] for f in figures}) == len(figures)
        if row['status'] == 'reviewed':
            final = by_concept[row['concept']]
            assert not final['validation_issues'] and not final['review_required_issues']
            if 'article_review_notes' in final:
                assert final['article_review_notes'] and row['audit']['review_notes'] == final['article_review_notes']
                for ref in row['audit']['review_references']:
                    marker = re.fullmatch(r'【(资料|图)([1-9][0-9]*)】', ref['input_marker'])
                    assert marker
                    kind, number = marker[1], int(marker[2])
                    expected = final['source_catalog'][number-1]['source_id'] if kind == '资料' else final['article_image_ids'][number-1]
                    assert ref['source_ids'] == [expected]
                    if kind == '图':assert Path(ref['local_path']).is_file()
            call = final['article_call']
            if not diagnostic_no_preflight:
                assert final['input_token_budget'] - call['usage']['prompt_tokens'] == 256
        report['publication'].append({'concept': row['concept'], 'status': row['status'], 'topics': len(row['knowledge']), 'images': len(figures)})
    stages, usage = collections.Counter(), collections.Counter()
    blocked_incomplete = []
    for path in run.glob('**/calls/*.request.json'):
        request = json.loads(path.read_text())
        response = json.loads(path.with_name(path.name.replace('.request.json', '.response.json')).read_text())
        assert response['status_code'] == 200
        if not all(c['finish_reason'] == 'stop' for c in response['body']['choices']):
            failed = [f for f in finals if (f.get('prompt_error') or {}).get('call', {}).get('response_path') == str(path.with_name(path.name.replace('.request.json', '.response.json')).resolve())]
            assert len(failed) == 1 and failed[0]['article_status'] == 'failed'
            pub = [r for r in published if r['concept'] == failed[0]['concept']]
            assert len(pub) == 1 and pub[0]['status'] == 'failed' and pub[0]['knowledge'] == []
            blocked_incomplete.append({'concept': failed[0]['concept'], 'finish_reasons': [c['finish_reason'] for c in response['body']['choices']], 'published_knowledge': 0})
        stages[request['stage']] += 1
        usage.update({k: v for k, v in response['body']['usage'].items() if isinstance(v, int)})
    report.update(incomplete_responses_blocked=blocked_incomplete, calls=sum(stages.values()), stages=dict(stages), usage=dict(usage), knowledge_sha256=hashlib.sha256((run / 'knowledge_base.jsonl').read_bytes()).hexdigest(), mapping_checks_passed=True, formal_token_preflight_validated=not diagnostic_no_preflight)
    (run / 'article_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    validate(sys.argv[1], diagnostic_no_preflight='--diagnostic-no-preflight' in sys.argv[2:])
