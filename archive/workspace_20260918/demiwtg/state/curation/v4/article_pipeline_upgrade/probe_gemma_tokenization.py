"""Read-only tokenization probe for the frozen thinking-mode comparison."""
import hashlib
import json
from pathlib import Path
import time
import httpx

base = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/v4')
report = []
with httpx.Client(base_url='http://127.0.0.1:8001', timeout=180, trust_env=False) as client:
    model = client.get('/v1/models').json()
    assert [m['id'] for m in model['data']] == ['gemma-4-31b-it']
    for suffix in ['sample5', 'population', 'basic_flowchart']:
        run = base / ('article_gemma_review_' + suffix + '_v2')
        for line in (run / 'datasets/final_review.jsonl').read_text().splitlines():
            row = json.loads(line)
            req = json.loads(Path(row['article_call']['request_path']).read_text())['payload']
            body = {'model': req['model'], 'messages': req['messages'], 'chat_template_kwargs': {'enable_thinking': True}}
            response = client.post('/tokenize', json=body)
            response.raise_for_status()
            value = response.json()
            report.append({'concept': row['concept'], 'suffix': suffix, 'count': value['count'],
                           'max_model_len': value['max_model_len'], 'enable_thinking': True,
                           'messages_sha256': hashlib.sha256(json.dumps(body['messages'], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                           'time': time.time(), 'inference_calls': 0})
            print(row['concept'], value['count'], 'tokens; context', value['max_model_len'], flush=True)
out = base / 'article_gemma_review_comparison_v3/tokenization_probe.json'
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
