import json,hashlib
from pathlib import Path
from PIL import Image
base=Path(__file__).resolve().parent/'bench200/scores_qib_v22_astra_medium_20260908'
sets=[]
for g in ['a','b']:
 rows=[json.loads(s) for s in (base/g/'blind_manifest.jsonl').read_text().splitlines()];idx={r['qid']:r for r in [json.loads(s) for s in (base/g/'prompts/index.jsonl').read_text().splitlines()]}
 assert len(rows)==len({r['qid'] for r in rows})==200
 for r in rows:
  for k,hk in [('before','source_sha256'),('after','output_sha256')]:
   assert hashlib.sha256(Path(r[k]).read_bytes()).hexdigest()==r['inputs'][hk]
   with Image.open(r[k]) as im:im.verify()
  assert hashlib.sha256((base/g/'prompts'/(r['qid']+'.txt')).read_bytes()).hexdigest()==idx[r['qid']]['prompt_sha256']
 sets.append({r['qid']:r for r in rows})
assert sets[0].keys()==sets[1].keys()
for q in sets[0]:
 for k in ['source_sha256','instruction_sha256']:assert sets[0][q]['inputs'][k]==sets[1][q]['inputs'][k]
 assert (base/'a/prompts'/(q+'.txt')).read_bytes()==(base/'b/prompts'/(q+'.txt')).read_bytes()
report={'candidates':400,'questions':200,'images_checked':800,'decode_and_hashes':'passed','paired_sources_instructions_prompts':'identical','template_sha256':hashlib.sha256((base.parents[1]/'prompts/judge_prompt_edit_qib_v2.2.md').read_bytes()).hexdigest()}
(base/'runtime/input_validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
