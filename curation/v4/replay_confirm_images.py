"""Replay frozen model responses through the formal image operators; no HTTP calls."""
import argparse
from collections import Counter
import json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest, immutable
from .ops.image_filter import RecordPrimaryImageSelection, PrepareImageReview, ApplyConfirmedImageSelection


def replay(source, run):
    def responses(model):
        rows = [json.loads(line) for line in (source / 'session' / model / 'decisions.jsonl').open()]
        return {tuple(d['image_id'] for d in row['image_decisions']): row['image_selection_calls'][0] for row in rows}
    primary, secondary = responses('qwen3.8-27b'), responses('gemma-4-31b-it')
    def attach(row, saved):
        response = saved[tuple(row['image_prompt']['image_ids'])]
        return {**row, 'prompt_result': response['result'], 'prompt_call': response['call'], 'prompt_error': response['error']}
    version = digest({'input': digest((source / 'inputs.jsonl').read_bytes()), 'replay': 'confirm-keep-v1'})
    data = local_data()
    first = (data.read_json(str(source / 'inputs.jsonl')).map(lambda row: attach(row, primary))
             .map(RecordPrimaryImageSelection()).map(PrepareImageReview())
             .checkpoint(run / 'review_inputs.jsonl', version=version))
    reviewed = first.filter(lambda row: row['review_required']).map(lambda row: attach(row, secondary))
    final = (reviewed.union(first.filter(lambda row: not row['review_required']))
             .map(ApplyConfirmedImageSelection()).checkpoint(run / 'decisions.jsonl', version=version))
    rows = list(final.iter_rows())
    decisions = {d['image_id']: d for row in rows for d in row['image_decisions']}
    counts = dict(Counter(d['decision'] for d in decisions.values()))
    assert len(decisions) == 114 and counts == {'keep': 56, 'exclude': 52, 'pending': 6}, counts
    assert decisions['I6790beeb9453']['decision'] == 'pending'
    assert decisions['Ia42c35b15185']['decision'] == 'exclude'
    report = {'validation': 'saved response replay, not live model cascade', 'new_model_calls': 0,
              'images': len(decisions), 'decisions': counts, 'primary_batches': len(rows),
              'review_batches': sum(row['review_required'] for row in first.iter_rows()),
              'thermometer': 'pending', 'hollow_tube': 'exclude', 'input_version': version}
    assert report['review_batches'] == 28
    immutable(run / 'validation.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    replay(args.source, args.run)
