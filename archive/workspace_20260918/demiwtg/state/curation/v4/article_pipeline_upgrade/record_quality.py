"""Append an already-written human/agent content review to the experiment ledger."""
from collections import Counter
import json
from pathlib import Path
import sys
u = Path(__file__).resolve().parent
ledger_path = u / 'experiments.json'
ledger = json.loads(ledger_path.read_text())
for arg in sys.argv[1:]:
    run = Path(arg).resolve()
    q = json.loads((run / 'quality_review.json').read_text())
    assert isinstance(q['accepted'], bool)
    assert not any(e['run'] == run.name for e in ledger['experiments']), run.name
    stages, usage = Counter(), Counter()
    responses = list(run.glob('**/calls/*.response.json'))
    for p in run.glob('**/calls/*.request.json'):
        stages[json.loads(p.read_text())['stage']] += 1
    for p in responses:
        usage.update({k: v for k, v in json.loads(p.read_text()).get('body', {}).get('usage', {}).items() if isinstance(v, int)})
    statuses = Counter(json.loads(line)['status'] for line in (run / 'knowledge_base.jsonl').read_text().splitlines())
    ledger['experiments'].append({
        'run': run.name, 'observations': q['observations'],
        'actual_request_stages': dict(stages), 'responses': len(responses),
        'usage': dict(usage), 'prompt_versions': q['prompt_versions'],
        'quality_accepted': q['accepted'], 'stored_final_status': dict(statuses),
        'model': q.get('model'), 'reasoning_effort': q.get('reasoning_effort'),
    })
ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
print('ledger_entries', len(ledger['experiments']))
