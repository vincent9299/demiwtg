#!/usr/bin/env bash
# focus1000 生图循环：反复跑 images 阶段直到 prompts 全部出完且无待生图任务
set -u
PY=/tank/demiwtg/.venv/bin/python
DIR=/tank/demiwtg/benchmark/t2i/data/focus1000
while true; do
  $PY /tank/demiwtg/benchmark/t2i/focus1000_genimg.py images --conc 8 2>&1
  NP=$(wc -l < "$DIR/gen_prompts.jsonl" 2>/dev/null || echo 0)
  if [ "$NP" -ge 2000 ]; then
    NJ=$($PY - <<'EOF'
import json
from pathlib import Path
d = Path('/tank/demiwtg/benchmark/t2i/data/focus1000')
rows = [json.loads(l) for l in (d / 'gen_prompts.jsonl').read_text().splitlines() if l.strip()]
done_ok = set()
rf = d / 'gen_results.jsonl'
if rf.exists():
    for l in rf.read_text().splitlines():
        try:
            r = json.loads(l)
            if not r.get('error'):
                done_ok.add(r['prompt_id'])
        except Exception:
            pass
print(sum(1 for r in rows if r.get('gen_prompt') and r['prompt_id'] not in done_ok))
EOF
)
    if [ "$NJ" -eq 0 ]; then
      echo "[loop] ALL DONE"
      break
    fi
  fi
  sleep 120
done
