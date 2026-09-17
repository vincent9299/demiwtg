"""Small, resumable local knowledge-to-caption development probe (12 calls)."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import argparse
import copy
import json
from pathlib import Path

import httpx
from curation.rag_diagnostic import REPO, sha, write_json
from curation.knowledge_probe import prepare

RUN = REPO / 'state/curation/knowledge_compiler_probe_v1'
SOURCE = REPO / 'state/curation/knowledge_color_probe_v1'
SYSTEM = '''Convert the task and supplied reference records into one natural English image-generation caption. Preserve the task's subject, scene, number, viewpoint, and exclusions. When a reference applies to the requested entity and conditions, explicitly describe its visible consequence in the caption; do not include unrelated records. Treat supplied records as the evidence for this controlled rendering task. If records are empty, produce the best caption using your own knowledge. Keep the caption under 120 words. Do not mention sources, record IDs, reasoning, or references inside the caption. Return JSON with caption and used_fact_ids. The latter lists exactly the applicable supplied record IDs; empty when no records were supplied.'''
SCHEMA = {'type': 'object', 'properties': {'caption': {'type': 'string'}, 'used_fact_ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['caption', 'used_fact_ids'], 'additionalProperties': False}


def compile_captions(run=RUN):
    run=Path(run); draft=json.loads((run/'scenario_drafts.json').read_text())
    manifest=run/'compiler_inputs.json'
    frozen={'draft_sha256':sha(run/'scenario_drafts.json'),'system':SYSTEM,'schema':SCHEMA,'model':'qwen3.8-27b','arms':['knowledge','irrelevant','wrong']}
    if manifest.exists():
        assert json.loads(manifest.read_text())==frozen, 'Compiler input changed'
    else: write_json(manifest,frozen)
    with httpx.Client(timeout=180,trust_env=False) as client:
        models=client.get('http://127.0.0.1:8000/v1/models').raise_for_status().json()
        assert [m['id'] for m in models['data']]==['qwen3.8-27b']
        for scenario in draft['scenarios']:
            for arm in frozen['arms']:
                out=run/'compiler'/scenario['id']/arm;out.mkdir(parents=True,exist_ok=True)
                if (out/'result.json').exists(): continue
                if (out/'attempt.json').exists(): raise RuntimeError(f'Ambiguous prior attempt: {out}')
                facts=[] if arm=='irrelevant' else draft['facts_correct' if arm=='knowledge' else 'facts_swapped']
                # Hide experimental labels and citation URLs; only the provided content differs.
                records=[{'id':str(i),'entity':f['entity'],'conditions':f['conditions'],'visible_fact':f['visible_fact']} for i,f in enumerate(facts)]
                request={'model':'qwen3.8-27b','temperature':0,'max_tokens':600,'chat_template_kwargs':{'enable_thinking':False},'response_format':{'type':'json_schema','json_schema':{'name':'caption','strict':True,'schema':SCHEMA}},'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps({'task':scenario['prompt'],'records':records})}]}
                write_json(out/'request.json',request);write_json(out/'attempt.json',{'attempt':1,'request_sha256':sha(out/'request.json')})
                response=client.post('http://127.0.0.1:8000/v1/chat/completions',json=request)
                (out/'response.raw').write_text(response.text);response.raise_for_status()
                result=json.loads(response.json()['choices'][0]['message']['content'])
                assert set(result)=={'caption','used_fact_ids'} and isinstance(result['caption'],str) and result['caption'].strip()
                assert len(result['caption'].split())<=120
                assert set(result['used_fact_ids'])<=set(map(str,range(len(records))))
                write_json(out/'result.json',result)
                print(scenario['id'],arm,json.dumps(result,ensure_ascii=False),flush=True)


def freeze(run=RUN):
    run=Path(run);draft=json.loads((run/'scenario_drafts.json').read_text())
    audit=json.loads((run/'compiler_review.json').read_text())
    assert audit['status']=='accepted_for_controlled_development' and audit['human_gold'] is False
    base=json.loads((SOURCE/'cases.json').read_text());old={c['id']:c for c in base['cases']};cases=[]
    for s in draft['scenarios']:
        c=copy.deepcopy(old[s['parent_id']]);c.update(id=s['id'],prompt=s['prompt'],seed_family=s['seed_family'])
        c.pop('explicit',None);c['active_conditions']=['baseline','knowledge','irrelevant','wrong']
        c['condition_prompts']={'baseline':s['prompt']}
        for arm in ['knowledge','irrelevant','wrong']:
            c['condition_prompts'][arm]=json.loads((run/'compiler'/s['id']/arm/'result.json').read_text())['caption']
        c['knowledge']=json.dumps(draft['facts_correct'],ensure_ascii=False)
        c['irrelevant']='No evidence; same compiler may use its own knowledge.'
        c['wrong']='Deliberately swapped colors; negative control, not trustworthy knowledge.'
        c['control_type']='same_automatic_compiler_correct_empty_swapped_evidence'
        cases.append(c)
    spec={'schema':'knowledge-compiler-probe-v1','repeats':2,'combined':True,'arms':['baseline','knowledge','irrelevant','wrong'],'image_size':1024,'inference_settings':{'cfg_renorm_min':0},'runner':str(REPO/'curation/archive/pre_v1/probe_bagel.py'),'cases':cases,
          'protocol':{'stage':'frozen_before_generation','role':'same_concept_new_scene_development_replication','arm_labels':{'baseline':'原题','knowledge':'Qwen＋正确材料编译','irrelevant':'Qwen＋空材料编译','wrong':'Qwen＋故意错配材料编译'},'compiler':'qwen3.8-27b, temperature0, no thinking, one caption/condition, no manual caption edits','primary':'Frozen visual criteria; knowledge and identity/composition separate. Same seeds across four arms and paired entities.','limitations':['Oracle evidence supplied; no retriever, image evidence, or finetuning.','Empty-evidence compiler may know the facts; it is a strong planning-only control.','Wrong-evidence arm is an intervention, not an asserted fact or a source-supported claim.','New scene uses already explored concepts, not unseen-concept generalization.','Only two image seeds per condition, no new Gemini calls.','Compiled-caption gains do not establish an ImageRAG architecture advantage.']}}
    write_json(run/'cases.json',spec)
    prepare(run)
    plan=json.loads((run/'plan.json').read_text())
    for p in [run/'scenario_drafts.json',run/'compiler_inputs.json',run/'compiler_review.json',*sorted((run/'compiler').glob('*/*/result.json'))]:plan['frozen_files'][str(p)]=sha(p)
    write_json(run/'plan.json',plan)
    clar=json.loads((SOURCE/'rubric_clarifications.json').read_text())
    write_json(run/'rubric_clarifications.json',clar)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['compile','freeze']);args=parser.parse_args()
    (compile_captions if args.command=='compile' else freeze)()
