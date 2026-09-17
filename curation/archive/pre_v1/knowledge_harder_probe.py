"""Scoped-knowledge development experiment, with identical compiled inputs across models."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import argparse
import json
from pathlib import Path
import httpx
from curation.rag_diagnostic import REPO, sha, write_json
from curation.knowledge_probe import prepare
from curation.knowledge_compiler_probe import SYSTEM as BASE_SYSTEM, SCHEMA

SYSTEM = BASE_SYSTEM + " CRITICAL: Put the applicable visible attribute explicitly in the opening visual description (for example the color of the requested band or liquid). Selecting a fact ID without expressing its visible consequence is invalid. Do not simply repeat the task. Preserve its factual scope and exclusions; shorten the bibliographic wording if needed."

RUN=REPO/'state/curation/knowledge_harder_probe_v1'


def compile_cases(run):
    run=Path(run).resolve();spec=json.loads((run/'drafts.json').read_text())
    manifest={'draft_sha256':sha(run/'drafts.json'),'system':SYSTEM,'schema':SCHEMA,'model':'qwen3.8-27b'}
    target=run/'compiler_manifest_v2.json'
    if target.exists(): assert json.loads(target.read_text())==manifest
    else: write_json(target,manifest)
    with httpx.Client(timeout=180,trust_env=False) as client:
        for c in spec['cases']:
            out=run/'compiler_v2'/c['id'];out.mkdir(parents=True,exist_ok=True)
            if (out/'result.json').exists():continue
            assert not (out/'attempt.json').exists(), 'Uncertain attempt retained; no automatic retry'
            payload={'task':c['prompt'],'records':c['facts']}
            req={'model':'qwen3.8-27b','temperature':0,'max_tokens':700,'chat_template_kwargs':{'enable_thinking':False},'response_format':{'type':'json_schema','json_schema':{'name':'caption','strict':True,'schema':SCHEMA}},'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(payload)}]}
            write_json(out/'request.json',req);write_json(out/'attempt.json',{'attempt':1,'request_sha256':sha(out/'request.json')})
            r=client.post('http://127.0.0.1:8000/v1/chat/completions',json=req)
            (out/'response.raw').write_text(r.text);r.raise_for_status()
            obj=json.loads(r.json()['choices'][0]['message']['content'])
            assert obj['caption'].strip() and len(obj['caption'].split())<=120
            assert set(obj['used_fact_ids'])<=set(f['id'] for f in c['facts'])
            write_json(out/'result.json',obj);print(c['id'],obj,flush=True)


def freeze(run):
    run=Path(run).resolve();spec=json.loads((run/'drafts.json').read_text())
    audit=json.loads((run/'compiler_review.json').read_text())
    assert audit['accepted_for_development'] and not audit['human_gold']
    encoding=json.loads((run/'visual_encoding.json').read_text())
    for c in spec['cases']:
        selected=json.loads((run/'compiler_v2'/c['id']/'result.json').read_text())['used_fact_ids']
        assert len(selected)==1 and selected[0] in encoding['fact_attributes']
        attribute=encoding['fact_attributes'][selected[0]]
        opening=encoding['templates'][c['seed_family']].format(color=attribute)
        c['condition_prompts']={'knowledge':opening+' '+c['prompt']}
        c['compiled_fact_id']=selected[0]
    spec['protocol']['arm_labels']['knowledge']='Qwen选择资料条目＋固定视觉模板展开'
    spec['protocol']['compiler']='Qwen selects fact ID; deterministic family template inserts its source-supported color and retains the entire original task. Free-form Qwen captions are retained but not used because some omit the target attribute.'
    write_json(run/'cases.json',spec);prepare(run)
    rows=[]
    for c in spec['cases']:
        for arm in ['baseline','knowledge']:
            for repeat in range(1,spec['repeats']+1):
                rows.append({'qid':f"{c['id']}_{arm}_g{repeat}",'task':'t2i','parent_id':c['id'],'condition':arm,'repeat':repeat,'gen_prompt':c['condition_prompts'].get(arm,c['prompt'])})
    q=run/'gemini_questions.jsonl';q.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    plan=json.loads((run/'plan.json').read_text())
    for p in [run/'drafts.json',run/'compiler_manifest_v2.json',run/'compiler_review.json',run/'visual_encoding.json',q,*sorted((run/'compiler_v2').glob('*/result.json'))]:plan['frozen_files'][str(p)]=sha(p)
    write_json(run/'plan.json',plan)
    print('Frozen Gemini requests',len(rows))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['compile','freeze']);p.add_argument('--run',type=Path,default=RUN);a=p.parse_args()
    (compile_cases if a.command=='compile' else freeze)(a.run)
