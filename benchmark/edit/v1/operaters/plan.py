"""Plan import and dispatch: one authoring job per planned source image + text.

The plan rows carry the batch policy (instance, level/suite/edit_type
assignment, image candidates with SHA identity, prepared request texts and
peak-shave adjustments). This operator only dispatches what the plan records;
it does not invent sampling policy.
"""
from benchmark.edit.v1.operaters.runfiles import resolve_source_image


def read_plan(files):
    """Plan rows become dispatch units; unusable rows get explicit statuses."""
    for index, record in enumerate(files.plan['rows'], 1):
        qid = str(record.get('qid') or f'e{index:03d}')
        row = {'task_id': 'unit_' + qid, 'qid': qid, 'instance': record.get('instance'),
               'target_level': record.get('level'), 'suite': record.get('suite'),
               'edit_type': record.get('edit_type'), 'alt_type': record.get('alt_type'),
               'main_domain': record.get('main_domain'), 'seq': index - 1,
               'branch': 'benchmark', 'status': 'plan_ready'}
        images = record.get('images') or []
        texts = record.get('texts') or []
        if not images or not texts:
            yield {**row, 'status': 'invalid_plan_row', 'fail_reason': 'plan row lacks images or texts'}
            continue
        try:
            resolved = [resolve_source_image(entry, files.plan['source']['path']) for entry in images]
        except (FileNotFoundError, ValueError) as error:
            yield {**row, 'status': 'missing_source_image', 'fail_reason': str(error)}
            continue
        yield {**row, 'plan_images': resolved, 'plan_texts': texts}




def expected_meta(row):
    """The historical metadata joined onto constructed questions (dispatch contract).

    `_file` keeps the plan's own relative path; `_sample_image` carries the
    historical `focus200/` prefix used by the evaluation-side source resolver.
    """
    source = row['source_image']
    return {'task': 'edit', '_protocol': 'edit-v6.1-image-first',
            '_job_qid': row['qid'], '_instance': row['instance'],
            '_sha256': source['sha256'],
            '_file': source.get('file') or _relative(source['path']),
            '_target_level': row['target_level'], '_suite': row['suite'],
            '_edit_type': row['target_edit_type'],
            '_generator': source.get('generator'),
            '_batch': source.get('batch'),
            '_sample_image': 'focus200/' + (source.get('file') or _relative(source['path'])),
            'difficulty': row['target_level']}


def _relative(path):
    from benchmark.edit.v1.operaters.runfiles import BENCH_DIR
    from pathlib import Path
    resolved = Path(path).resolve()
    if resolved.is_relative_to(BENCH_DIR):
        return str(resolved.relative_to(BENCH_DIR))
    return Path(path).name
