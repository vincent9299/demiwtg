"""Reapply source-block contracts to frozen real responses. No model transport.

For the optional source-risk field correction: scores/decisions remain model
outputs; absent critical fields still defer. Comparison inputs must be an exact
text/context superset of the new candidate set, verified before reuse.
"""
import argparse
import json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest, immutable, source_code, runtime_version, run_lock
from .ops.source_blocks import (BuildSourceBlocks, ApplyBlockSelection, merge_block_decisions,
    BuildVerbatimCandidates, ApplyComparedCandidates, merge_source_comparisons, model_unit)
from .ops.prompt_operators import BuildCandidateRecords
from .final_results import BuildKnowledgeRecord


def response_rows(row):
    return [{'batch_id': c['batch_id'], 'prompt_result': c['raw_result'],
             'prompt_call': c['call'], 'prompt_error': c['error']} for c in row['block_calls']]


class ValidateComparisonReuse:
    def __init__(self, source):
        self.inputs = {}
        for line in (source/'datasets/comparison_requests.jsonl').open():
            row = json.loads(line)
            self.inputs.setdefault(row['case_id'], []).extend(row['compare_prompt']['units'])

    def __call__(self, row):
        current = {u['unit_id']: model_unit(u) for u in row['source_units']}
        for unit in self.inputs.get(row['case_id'], []):
            previous = {k: v for k, v in unit.items() if k != 'fact_id'}
            if current.get(unit['unit_id']) != previous:
                raise ValueError('Source text/context changed; historical comparison cannot be reused')
        return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-run', type=Path, required=True)
    p.add_argument('--run', type=Path, required=True)
    args = p.parse_args();source, run = args.source_run.resolve(), args.run.resolve()
    paths = ['manifest.json', 'datasets/source_blocks.jsonl', 'datasets/block_requests.jsonl',
             'datasets/block_decisions.jsonl', 'datasets/comparison_requests.jsonl', 'datasets/source_comparisons.jsonl']
    config = json.loads((source/'manifest.json').read_text())['config']
    with run_lock(run):
        manifest = {'purpose': 'Reapply optional-field handling; reuse immutable selection/comparison responses; zero model calls',
                    'source_run': str(source), 'source_hashes': {p: digest((source/p).read_bytes()) for p in paths},
                    'config': config, 'source_code': source_code(), 'runtime': runtime_version()}
        immutable(run/'manifest.json', manifest);version = digest(manifest)
        data = local_data();tables = run/'datasets'
        blocks = (data.read_json(str(source/'datasets/source_blocks.jsonl'))
            .map(BuildSourceBlocks(config['block_unit_chars'], body_only=True))
            .checkpoint(tables/'source_blocks.jsonl', version=version))
        responses = data.read_json(str(source/'datasets/block_decisions.jsonl')).flat_map(response_rows)
        decisions = (data.read_json(str(source/'datasets/block_requests.jsonl'))
            .join(responses, on='batch_id')
            .map(ApplyBlockSelection(strict=True))
            .checkpoint(tables/'block_decisions.jsonl', version=version)
            .reduce_by_key('case_id', merge_block_decisions))
        candidates = (blocks.join(decisions, on='case_id', how='left')
            .map(BuildVerbatimCandidates()).map(ValidateComparisonReuse(source))
            .checkpoint(tables/'verbatim_candidates.jsonl', version=version))
        comparisons = data.read_json(str(source/'datasets/source_comparisons.jsonl')).reduce_by_key(
            'case_id', merge_source_comparisons)
        reviewed = (candidates.join(comparisons, on='case_id', how='left').map(ApplyComparedCandidates())
            .checkpoint(tables/'knowledge_block_review.jsonl', version=version))
        exported = (reviewed.map_cached(BuildCandidateRecords(run, config),
            cache_dir=run/'cache/BuildCandidateRecords', version=version)
            .checkpoint(tables/'knowledge_export.jsonl', version=version))
        exported.map(BuildKnowledgeRecord()).checkpoint(run/'knowledge_base.jsonl', version=version)
        print(run/'knowledge_base.jsonl')


if __name__ == '__main__':
    main()
