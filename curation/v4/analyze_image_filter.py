"""Compare frozen image decisions; provisional visual references are not expert gold."""
import argparse
import itertools
import json
import hashlib
from collections import Counter
from pathlib import Path
import yaml


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def metrics(predictions, references):
    matrix = Counter((r['reference'], predictions[r['image_id']]) for r in references)
    return {
        'correct_keep': matrix['keep', 'keep'],
        'false_exclude': matrix['keep', 'exclude'],
        'positive_pending': matrix['keep', 'pending'],
        'false_keep': matrix['exclude', 'keep'],
        'correct_exclude': matrix['exclude', 'exclude'],
        'negative_pending': matrix['exclude', 'pending'],
        'uncertain_kept': matrix['pending', 'keep'],
        'uncertain_excluded': matrix['pending', 'exclude'],
        'uncertain_pending': matrix['pending', 'pending'],
    }


def combine(first, second, policy):
    if policy == 'agreement':
        return first if first == second else 'pending'
    if policy == 'pending_cascade':
        return second if first == 'pending' else first
    if policy == 'confirm_keep':
        return first if first != 'keep' else ('keep' if second == 'keep' else 'pending')
    raise ValueError(policy)


def analyze(run):
    refs = read_rows(run / 'reference_review.jsonl')
    reference_manifest = json.loads((run / 'reference_manifest.json').read_text())
    if hashlib.sha256((run / 'reference_review.jsonl').read_bytes()).hexdigest() != reference_manifest['sha256']:
        raise ValueError('Frozen reference changed')
    reference_ids = {r['image_id'] for r in refs}
    inputs = read_rows(run / 'inputs.jsonl')
    events = read_rows(run / 'session/events.jsonl')
    summaries, predictions, details = [], {}, {r['image_id']: dict(r, models={}) for r in refs}
    input_hashes, templates, execution_code = set(), set(), set()
    message_sets = []
    for directory in sorted((run / 'session').iterdir()):
        if not directory.is_dir() or not (directory / 'decisions.jsonl').exists():
            continue
        rows = read_rows(directory / 'decisions.jsonl')
        manifest = json.loads((directory / 'manifest.json').read_text())
        input_hashes.add(manifest['sha256'])
        templates.add(yaml.safe_load(manifest['prompt'])['prompts']['select_images']['template'])
        execution_code.add(manifest['code']['ops/multimodal.py'])
        parsed, raw, errors, duplicated = {}, {}, 0, set()
        for row in rows:
            for d in row['image_decisions']:
                if d['image_id'] in parsed:
                    raise ValueError('Duplicate model image decision')
                parsed[d['image_id']] = d['decision']
                errors += not d['protocol_valid']
            for call in row['image_selection_calls']:
                items = (call.get('result') or {}).get('images', [])
                if not isinstance(items, list):
                    continue
                for d in items:
                    if not isinstance(d, dict) or d.get('image_id') not in reference_ids:
                        continue
                    iid = d['image_id']
                    if iid in raw:
                        duplicated.add(iid)
                    else:
                        raw[iid] = d
        for iid in duplicated:
            raw[iid] = {'image_id': iid, 'decision': 'pending', 'reason': 'Duplicate image_id in raw response; no arbitrary selection.'}
        if set(parsed) != reference_ids:
            raise ValueError('Missing/extra model image decisions')
        semantic = {iid: raw.get(iid, {}).get('decision', 'pending') for iid in reference_ids}
        semantic = {iid: d if d in {'keep', 'exclude', 'pending'} else 'pending' for iid, d in semantic.items()}
        predictions[directory.name] = semantic
        responses = [json.loads(p.read_text()) for p in (directory / 'knowledge/calls').glob('*.response.json')]
        messages = set()
        for path in (directory / 'knowledge/calls').glob('*.request.json'):
            request = json.loads(path.read_text())
            serialized = json.dumps(request['payload']['messages'], sort_keys=True, ensure_ascii=False)
            messages.add(hashlib.sha256(serialized.encode()).hexdigest())
        message_sets.append(messages)
        usage = Counter()
        for response in responses:
            usage.update({k: v for k, v in response.get('body', {}).get('usage', {}).items() if isinstance(v, int)})
        model_events = {e['status']: e['time'] for e in events if e.get('model') == directory.name}
        wall = (round(model_events['evaluation_finished'] - model_events['evaluation_started'], 2)
                if 'evaluation_finished' in model_events else None)
        summaries.append({'model': directory.name, 'calls': len(responses),
            'concurrency': manifest['config'].get('comparison_concurrency', 1),
            'evaluation_wall_s': wall,
            'request_seconds': round(sum(r.get('elapsed_s', 0) for r in responses), 2),
            'tokens': dict(usage), 'protocol_invalid_images': errors,
            'raw_semantics': metrics(semantic, refs), 'pipeline_decisions': metrics(parsed, refs),
            'by_concept': {concept: metrics(semantic, [r for r in refs if r['concept'] == concept])
                           for concept in sorted({r['concept'] for r in refs})}})
        replay_path = run / 'parser_v2' / directory.name / 'decisions.jsonl'
        replayed = {}
        if replay_path.exists():
            replay_items = [d for row in read_rows(replay_path) for d in row['image_decisions']]
            replayed = {d['image_id']: d['decision'] for d in replay_items}
            if len(replay_items) != len(reference_ids) or set(replayed) != reference_ids:
                raise ValueError('Invalid replay coverage')
            summaries[-1]['revalidated'] = metrics(replayed, refs)
            summaries[-1]['revalidated_invalid_images'] = sum(not d['protocol_valid'] for d in replay_items)
        for iid, item in details.items():
            item['models'][directory.name] = {'raw': raw.get(iid), 'pipeline_decision': parsed[iid]}
            if replayed:
                item['models'][directory.name]['revalidated_decision'] = replayed[iid]
    combinations = []
    for first, second in itertools.permutations(predictions, 2):
        for policy in ['agreement', 'pending_cascade', 'confirm_keep']:
            result = {iid: combine(predictions[first][iid], predictions[second][iid], policy) for iid in reference_ids}
            trigger = lambda d: policy == 'agreement' or (policy == 'pending_cascade' and d == 'pending') or (policy == 'confirm_keep' and d == 'keep')
            images = sum(trigger(d) for d in predictions[first].values())
            # Batch-preserving replay estimate, not a measured cascade run.
            batches = sum(any(trigger(predictions[first][iid]) for iid in r['image_prompt']['image_ids']) for r in inputs)
            combinations.append({'first': first, 'second': second, 'policy': policy,
                'second_images': images, 'estimated_second_batches': batches,
                'raw_semantics': metrics(result, refs)})
    report = {'scope': 'Same 114 preselection images, fixed concept definitions; Codex provisional visual references, not expert gold. Raw semantic results are diagnostic and may fail the current parser.',
        'consistency': {'input_hashes': len(input_hashes), 'prompt_templates': len(templates), 'selection_operator_versions': len(execution_code),
                        'same_actual_messages': bool(message_sets) and all(s == message_sets[0] for s in message_sets),
                        'actual_message_counts': [len(s) for s in message_sets]},
        'reference_counts': dict(Counter(r['reference'] for r in refs)), 'models': summaries,
        'combinations': combinations, 'combination_scope': 'Offline deterministic replay; no additional model reasoning or measured cascade timing.'}
    if (run / 'post_review_notes.json').exists():
        notes = json.loads((run / 'post_review_notes.json').read_text())
        disputed = {r['sample_id'] for r in notes['items']}
        report['post_review_notes'] = notes
        report['sensitivity_without_disputed_references'] = {
            model: metrics(pred, [r for r in refs if r['sample_id'] not in disputed])
            for model, pred in predictions.items()}
    (run / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (run / 'comparison_rows.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in details.values()))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--run', type=Path, required=True)
    report = analyze(parser.parse_args().run)
    print(json.dumps(report['models'], ensure_ascii=False, indent=2))
