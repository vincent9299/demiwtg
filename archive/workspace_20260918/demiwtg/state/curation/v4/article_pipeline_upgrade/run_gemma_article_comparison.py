"""One bounded comparison on completed frozen drafts; restore original resources."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')
sys.path.insert(0, str(ROOT))
from curation.v4.local_review_service import ANNOT, matching, ready, spawn, image_review_service, wait_until, command

base = ROOT / 'state/curation/v4'
upgrade = base / 'article_pipeline_upgrade'
run = base / 'article_gemma_review_comparison_v1'
run.mkdir(exist_ok=True)
parents = ['article_holdout_population_v9', 'bench200_sample5_article_v9']
def active_qwen_clients():
    modules = {'curation.v4.run_notebook_pipeline', 'curation.v4.article_trial'}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        args = command(int(proc.name))
        if '-m' in args and args.index('-m') + 1 < len(args) and args[args.index('-m') + 1] in modules:
            return True
    return False

print('Waiting for the current Qwen runs to finish before borrowing GPUs', flush=True)
wait_until(lambda: all((base / name / 'knowledge_base.jsonl').exists() for name in parents)
           and not active_qwen_clients(), 2400, 'all Qwen comparison clients finished')
assert ready(8000, 'qwen3.8-27b')
audit_path = upgrade / 'qwen_experiment_pause_round2.json'
audit = json.loads(audit_path.read_text())
assert 'restored_time' not in audit
assert Path(audit['stop']) == ANNOT / 'STOP' and (ANNOT / 'STOP').exists()
assert not matching('curation.image_preannotate run --run ' + str(ANNOT))
assert not matching('curation.image_supervisor --run ' + str(ANNOT))
assert not matching('bash ' + str(ROOT / 'curation/run_image_pipeline.sh'))
(ANNOT / 'STOP').unlink()
launcher = spawn(['bash', str(ROOT / 'curation/run_image_pipeline.sh')], upgrade / 'restore_after_qwen_experiments_round2.log')
wait_until(lambda: bool(matching('curation.image_preannotate run --run ' + str(ANNOT))), 120, 'preannotation restoration')
audit.update(restored_time=time.time(), restored_launcher=launcher.pid)
audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
criteria = {
    'hypothesis': 'A different final reviewer may reduce reproduction of extraction errors; stage count stays one.',
    'parents': parents,
    'prompt': 'final-review-v26',
    'model': 'gemma-4-31b-it',
    'checks': [
        'All draft text, actually cited original passages and adopted pixels match the Qwen final-review inputs.',
        'White-flowered Peony: no unresolved family classification or unconfirmed photos.',
        'Plateau: no invented nationality, image-inferred rock origin, location mismatch, or loss of useful formation/climate conditions.',
        'Potassium permanganate: terms match original, 2D diagram does not assert visible 3D geometry; static image does not prove process; preserve supported packaging differences.',
        'Population: target is not synonym of dot mapping; no unsupported population map identity, guessed legend ranges, mistranslated nomograph, wrong dates, or independent disease-map history.',
        'Format, complete output, citations, placement and image uniqueness checked separately from content.'
    ],
    'capacity': 'Diagnostic uses server rejection for overflow, no prompt truncation; not formal tokenizer validation.'
}
(run / 'predeclared_review_criteria.json').write_text(json.dumps(criteria, ensure_ascii=False, indent=2) + '\n')
max_images = max(len(json.loads(l)['article_image_ids']) for name in parents
                 for l in (base / name / 'datasets/final_review_requests.jsonl').read_text().splitlines())
config = {'image_review_model': 'gemma-4-31b-it', 'image_review_base_url': 'http://127.0.0.1:8001/v1',
          'image_review_service': 'borrow', 'image_batch_size': max_images, 'local_review_context_tokens': 131072}
with image_review_service(run, config):
    for parent, suffix in zip(parents, ['population', 'sample5']):
        target = base / ('article_gemma_review_' + suffix + '_v1')
        command = [sys.executable, '-m', 'curation.v4.article_trial', '--run', str(target), '--parent', str(base / parent),
                   '--mode', 'reuse-extraction', '--review-model', 'gemma-4-31b-it', '--max-output-tokens', '32768',
                   '--timeout-s', '1200', '--identity-definitions', str(upgrade / 'native_identity_definitions.json')]
        with (run / (suffix + '.log')).open('ab') as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=log)
        (run / (suffix + '_exit.json')).write_text(json.dumps({'returncode': result.returncode, 'time': time.time()}))
        if result.returncode:
            raise RuntimeError('Gemma comparison failed: ' + suffix)
(run / 'resource_after.json').write_text(json.dumps({'qwen_healthy': ready(8000, 'qwen3.8-27b'),
    'worker': matching('curation.image_preannotate run --run ' + str(ANNOT)),
    'stop_exists': (ANNOT / 'STOP').exists(), 'time': time.time()}, indent=2) + '\n')
print('Gemma article comparison completed and original resources restored', flush=True)
