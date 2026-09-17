"""Run the source-block operators from an immutable identity checkpoint.

Upstream parsing/cleaning/identity are reused, not rerun or called raw downloads.
The same operators are explicitly composed in the raw-data notebook branch.
"""
import argparse
from pathlib import Path

from demiflow.standalone import local_data
from .contracts import digest, immutable, source_code, runtime_version, run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack, prompt_execution_options, save_prompt_config
from .ops.source_blocks import (BuildSourceBlocks, BatchSourceBlocks, ApplyBlockSelection,
    merge_block_decisions, BuildVerbatimCandidates, BatchSourceComparisons,
    ApplySourceComparison, merge_source_comparisons, ApplyComparedCandidates)
from .ops.prompt_operators import BuildCandidateRecords
from .final_results import BuildKnowledgeRecord


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    source, run = args.source.resolve(), args.run.resolve()
    config = {**DEFAULT, 'max_calls': None, 'max_output_tokens': 16384, 'body_only': True,
              'temperature': 0, 'timeout_s': 600, 'text_mode': 'source_blocks',
              'block_unit_chars': 1800, 'block_batch_chars': 8000, 'comparison_group_chars': 16000}
    with run_lock(run):
        manifest = {'purpose': 'Same-case source-block experiment; reused frozen upstream identity boundary',
                    'source': str(source), 'source_sha256': digest(source.read_bytes()),
                    'config': config, 'source_code': source_code(), 'runtime': runtime_version()}
        immutable(run/'manifest.json', manifest)
        version = digest(manifest)
        pack, text = knowledge_prompt_pack(config)
        options = prompt_execution_options(run, config)
        save_prompt_config(run, text, options)
        data = local_data(prompt_packs={'knowledge.yaml': pack},
                          max_prompt_requests=None, prompt_options=options)
        tables = run/'datasets'
        identified = data.read_json(str(source)).checkpoint(tables/'knowledge_identity.jsonl', version=version)

        # Identity-accepted documents -> every intact source block, with offsets and scope.
        blocks = (identified.map(BuildSourceBlocks(config['block_unit_chars'], body_only=config['body_only']))
            .checkpoint(tables/'source_blocks.jsonl', version=version))
        requests = (blocks.flat_map(BatchSourceBlocks(config['block_batch_chars']))
            .checkpoint(tables/'block_requests.jsonl', version=version))
        print('Source blocks and selection requests saved', flush=True)
        selected_batches = (requests
            .map_prompt_async('select_blocks', config='knowledge.yaml', inputs={'payload': 'block_prompt'},
                output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                concurrency=1, queue_depth=1)
            .map_cached(ApplyBlockSelection(relevance_only=True), cache_dir=run/'cache/ApplyBlockSelection', version=version)
            .checkpoint(tables/'block_decisions.jsonl', version=version))
        decisions = selected_batches.reduce_by_key('case_id', merge_block_decisions)
        candidates = (blocks.join(decisions, on='case_id', how='left')
            .map(BuildVerbatimCandidates())
            .checkpoint(tables/'verbatim_candidates.jsonl', version=version))
        comparisons = (candidates.flat_map(BatchSourceComparisons(config['comparison_group_chars']))
            .checkpoint(tables/'comparison_requests.jsonl', version=version))
        print('Verbatim candidates and comparison requests saved', flush=True)
        compared = (comparisons
            .map_prompt_async('compare_blocks', config='knowledge.yaml', inputs={'payload': 'compare_prompt'},
                output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                concurrency=1, queue_depth=1)
            .map_cached(ApplySourceComparison(), cache_dir=run/'cache/ApplySourceComparison', version=version)
            .checkpoint(tables/'source_comparisons.jsonl', version=version))
        comparison_results = compared.reduce_by_key('case_id', merge_source_comparisons)
        reviewed = (candidates.join(comparison_results, on='case_id', how='left')
            .map(ApplyComparedCandidates())
            .checkpoint(tables/'knowledge_block_review.jsonl', version=version))
        exported = (reviewed.map_cached(BuildCandidateRecords(run, config),
            cache_dir=run/'cache/BuildCandidateRecords', version=version)
            .checkpoint(tables/'knowledge_export.jsonl', version=version))
        exported.map(BuildKnowledgeRecord()).checkpoint(run/'knowledge_base.jsonl', version=version)
        print(run/'knowledge_base.jsonl', flush=True)


if __name__ == '__main__':
    main()
