"""Revalidate saved model answers in a new run, without model requests."""
import argparse
from pathlib import Path

from demiflow.standalone import local_data
from .contracts import digest, immutable, run_lock, source_code
from .ops.multimodal import ApplyImageSelection


def restore_input(row):
    call, = row['image_selection_calls']
    return {'case_id': row['case_id'],
            'image_prompt': {'selection_protocol': 'image-relevance-v2',
                             'image_ids': [d['image_id'] for d in row['image_decisions']]},
            'prompt_result': call.get('result'), 'prompt_error': call.get('error'),
            'prompt_call': call.get('call'), 'pixel_roles': call['pixel_roles']}


def replay(source, run):
    with run_lock(run):
        manifest = {'source': str(source.resolve()), 'source_sha256': digest(source.read_bytes()),
                    'code': source_code(), 'purpose': 'Reparse original answers, no model requests'}
        immutable(run / 'manifest.json', manifest)
        version = digest(manifest)
        (local_data().read_json(str(source)).map(restore_input)
         .map_cached(ApplyImageSelection(), cache_dir=run / 'cache', version=version)
         .checkpoint(run / 'decisions.jsonl', version=version))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--source', type=Path, required=True); p.add_argument('--run', type=Path, required=True)
    a = p.parse_args(); replay(a.source, a.run)
