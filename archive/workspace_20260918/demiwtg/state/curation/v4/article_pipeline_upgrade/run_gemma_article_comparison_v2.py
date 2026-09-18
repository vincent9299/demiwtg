"""Bounded same-evidence final-review comparison, using owned GPU borrowing."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')
sys.path.insert(0, str(ROOT))
from curation.v4.local_review_service import ANNOT, command, image_review_service, matching, ready

base = ROOT / 'state/curation/v4'
up = base / 'article_pipeline_upgrade'
session = base / 'article_gemma_review_comparison_v2'
session.mkdir(exist_ok=False)
prompt = up / 'final_review_v44.yaml'
canonical = ROOT / 'curation/v4/ops/prompts/final_review.yaml'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
prompt_hash = sha(prompt)
assert sha(canonical) == prompt_hash
assert ready(8000, 'qwen3.8-27b') and not (ANNOT / 'STOP').exists()
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    argv = command(int(proc.name))
    if '-m' in argv and argv[argv.index('-m') + 1] in {'curation.v4.run_notebook_pipeline', 'curation.v4.article_trial'}:
        raise RuntimeError('A model experiment is active')

entries = [
    ('bench200_sample5_article_v27', 'article_gemma_review_sample5_v2'),
    ('article_holdout_population_v27', 'article_gemma_review_population_v2'),
    ('article_holdout_basic_flowchart_v18', 'article_gemma_review_basic_flowchart_v2'),
]
criteria = json.loads((up / 'convergence_criteria_v42.json').read_text())
plan = {
    'model': 'gemma-4-31b-it', 'prompt': str(prompt), 'prompt_sha256': prompt_hash,
    'runs': entries, 'time': time.time(), 'criteria': criteria,
    'input': 'Same outline-only draft input, adopted original sources and pixels as Qwen v44; full frozen drafts retained.',
    'capacity': 'Diagnostic: local server rejects overflow without truncation; formal Gemma token preflight not implemented.',
    'adoption': 'Model comparison only; canonical final model remains Qwen until demonstrated and integrated.',
}
(session / 'predeclared_review_criteria.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
max_images = max(len(json.loads(line)['article_image_ids']) for parent, _ in entries
                 for line in (base / parent / 'datasets/final_review_requests.jsonl').read_text().splitlines())
config = {'image_review_model': 'gemma-4-31b-it', 'image_review_base_url': 'http://127.0.0.1:8001/v1',
          'image_review_service': 'borrow', 'image_batch_size': max_images, 'local_review_context_tokens': 131072}
try:
    with image_review_service(session, config):
        for parent, target in entries:
            assert sha(prompt) == prompt_hash and sha(canonical) == prompt_hash
            args = [sys.executable, '-m', 'curation.v4.article_trial', '--run', str(base / target),
                    '--parent', str(base / parent), '--mode', 'reuse-extraction', '--review-model', 'gemma-4-31b-it',
                    '--review-prompt', str(prompt), '--max-output-tokens', '32768', '--timeout-s', '1200',
                    '--identity-definitions', str(up / 'native_identity_definitions.json')]
            print('Started ' + target, flush=True)
            with (session / (target + '.log')).open('ab') as log:
                result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=log)
            (session / (target + '_exit.json')).write_text(json.dumps({'returncode': result.returncode, 'time': time.time()}) + '\n')
            if result.returncode:
                raise RuntimeError('Comparison client failed: ' + target)
            print('Completed ' + target, flush=True)
finally:
    (session / 'resource_after.json').write_text(json.dumps({
        'qwen_healthy': ready(8000, 'qwen3.8-27b'),
        'worker': matching('curation.image_preannotate run --run ' + str(ANNOT)),
        'stop_exists': (ANNOT / 'STOP').exists(), 'time': time.time(),
    }, indent=2) + '\n')
print('All comparisons finished; original resources restored', flush=True)
