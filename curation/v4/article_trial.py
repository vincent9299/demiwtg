"""Checkpoint-only article prompt trials. Formal graph remains in the notebook."""
import argparse,hashlib,json,time
from pathlib import Path
import yaml
from demiflow.standalone import local_data
from demiflow.operator_llm.parser import parse_prompt_pack
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config,article_prompt_template
from .ops.article import PrepareArticleInput,ApplyArticle,PrepareFinalReview,PublishArticle,ArticleTokenBudget,legacy_draft


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',type=Path,required=True);p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--mode',choices=['old-extraction','new-extraction','reuse-extraction'],default='old-extraction')
    p.add_argument('--thinking',action='store_true');p.add_argument('--reasoning-effort',choices=['low','medium','xhigh'],default='low');p.add_argument('--concepts',nargs='+');p.add_argument('--review-prompt',type=Path)
    p.add_argument('--max-output-tokens',type=int,default=16384);p.add_argument('--timeout-s',type=float,default=900)
    p.add_argument('--batches',nargs='+');p.add_argument('--joint-prompt',type=Path);p.add_argument('--through',choices=['extract','export'],default='export')
    p.add_argument('--identity-definitions',type=Path,help='Explicit concept scope for an isolated input diagnostic')
    p.add_argument('--review-model',choices=['qwen3.8-27b','gemma-4-31b-it'],default='qwen3.8-27b',
                   help='Bounded final-review comparison; Gemma must already be managed on local 8001')
    a=p.parse_args()
    if a.review_model != 'qwen3.8-27b' and (a.mode != 'reuse-extraction' or a.through != 'export' or a.reasoning_effort != 'low'):
        p.error('Gemma comparison only reviews frozen extraction; reasoning-effort is a Qwen option')
    if a.batches and (a.mode!='new-extraction' or a.through!='extract'):p.error('--batches is an extraction-only diagnostic scope')
    root=Path(__file__).resolve().parents[2]
    definitions={'legacy:白花芍药':'植物学物种Paeonia sterniana；泛称白色芍药花或其他栽培品种不等于该物种。',
                 'legacy:OK手势':'拇指与食指相触成环，其余三指伸展或放松的手势。'}
    if a.identity_definitions:
        supplied=json.loads(a.identity_definitions.read_text())
        if not isinstance(supplied,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in supplied.items()):
            p.error('--identity-definitions must contain a JSON string mapping')
        definitions.update(supplied)
    cfg={'model':'qwen3.8-27b','base_url':'http://127.0.0.1:8000/v1','temperature':0,'timeout_s':a.timeout_s,'max_output_tokens':a.max_output_tokens,
         'max_calls':None,'enable_thinking':a.thinking,'reasoning_effort':a.reasoning_effort,'article_mode':True,'image_identity_definitions':definitions}
    if a.review_model != 'qwen3.8-27b':
        cfg.update(model=a.review_model,base_url='http://127.0.0.1:8001/v1',local_model_comparison=True)
        cfg.pop('reasoning_effort')
    review_spec=yaml.safe_load((a.review_prompt or Path(__file__).parent/'ops/prompts/final_review.yaml').read_text())
    cfg['final_image_selection_only']=bool(review_spec.get('image_selection_only',False))
    cfg['final_review_notes_required']=bool(review_spec.get('review_notes_required',False))
    cfg['final_draft_outline_only']=bool(review_spec.get('draft_outline_only',False))
    _,text=knowledge_prompt_pack(cfg);pack=yaml.safe_load(text)
    for name,file in [('joint_paragraphs',a.joint_prompt),('final_review',a.review_prompt)]:
        if file:
            spec=yaml.safe_load(file.read_text());pack['prompts'][name]['version']=spec['version']
            pack['prompts'][name]['template']=article_prompt_template(spec)
    text=yaml.safe_dump(pack,allow_unicode=True,sort_keys=False)
    names=['datasets/related_materials.jsonl','datasets/joint_requests.jsonl']
    if a.mode in ['old-extraction','reuse-extraction']:names.append('datasets/paragraph_extract.jsonl')
    inputs={name:{'path':str((a.parent/name).resolve()),'sha256':hashlib.sha256((a.parent/name).read_bytes()).hexdigest()} for name in names}
    manifest={'inputs':inputs,'config':cfg,'prompts':text,'concepts':a.concepts,'batches':a.batches,'mode':a.mode,'code':source_code(),'runtime':runtime_version()}
    version=digest(manifest);started=time.time()
    with run_lock(a.run):
        immutable(a.run/'trial_manifest.json',manifest)
        options=prompt_execution_options(a.run,cfg)
        save_prompt_config(a.run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':parse_prompt_pack(text)},prompt_options=options)
        tables=a.run/'datasets';allowed=set(a.concepts) if a.concepts else None
        requests=data.read_json(inputs['datasets/joint_requests.jsonl']['path']).filter(lambda r:(allowed is None or r['joint_prompt']['concept'] in allowed) and (not a.batches or r['batch_id'] in a.batches)).checkpoint(tables/'joint_requests.jsonl',version=version)
        if a.mode=='new-extraction':
            prepared=requests.map(PrepareArticleInput(definitions)).checkpoint(tables/'article_inputs.jsonl',version=version)
            extracted=(prepared.map_prompt_async('joint_paragraphs',config='knowledge.yaml',inputs={'payload':'article_input','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                       .map_cached(ApplyArticle(),cache_dir=a.run/'cache/extract',version=version).checkpoint(tables/'paragraph_extract.jsonl',version=version))
        else:
            extracted=data.read_json(inputs['datasets/paragraph_extract.jsonl']['path']).filter(lambda r:allowed is None or r.get('concept',r['joint_prompt']['concept']) in allowed)
            if a.mode=='old-extraction':extracted=extracted.map(lambda r:legacy_draft(r,definitions))
            extracted=extracted.checkpoint(tables/'paragraph_extract.jsonl',version=version)
        related=data.read_json(inputs['datasets/related_materials.jsonl']['path']).filter(lambda r:allowed is None or r['identity']['target_label'] in allowed).checkpoint(tables/'related_materials.jsonl',version=version)
        if a.through=='export':
            counter=ArticleTokenBudget(root.parent/'models/Qwen3.8-27B',cfg) if a.review_model=='qwen3.8-27b' else None
            # Freeze actual variant template in the counter too.
            if counter is not None:
                from demiflow.operator_llm import compile_template
                counter.template=pack['prompts']['final_review']['template']
                counter.compiled_template=compile_template(counter.template)
            # This isolated Gemma trial does not reuse Qwen token estimates.
            # The local server rejects over-capacity input; no truncation is requested.
            if counter is None:immutable(a.run/'capacity_policy.json',{'model':a.review_model,'checked_by':'local_server_rejects_overflow','truncate_prompt_tokens':None,'not_formal_token_budget_validation':True})
            grouped=extracted.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'drafts':acc['drafts']+[r]},initial={'drafts':[]})
            prepared=grouped.map(PrepareFinalReview(definitions,counter,131072,draft_outline_only=cfg['final_draft_outline_only'])).checkpoint(tables/'final_review_requests.jsonl',version=version)
            reviewed=(prepared.map_prompt_async('final_review',config='knowledge.yaml',inputs={'payload':'article_input','images':'pixel_images'},when=lambda r:not r['preflight_error'],output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                      .map_cached(ApplyArticle(final=True,image_selection_only=cfg['final_image_selection_only'],review_notes_required=cfg['final_review_notes_required']),cache_dir=a.run/'cache/final',version=version).checkpoint(tables/'final_review.jsonl',version=version))
            context=related.map(lambda r:{'concept':r['identity']['target_label'],'material':r}).reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'materials':acc['materials']+[r['material']]},initial={'materials':[]})
            (context.join(reviewed.map(lambda r:{'concept':r['concept'],'review':r}),on='concept',how='left').map(PublishArticle()).checkpoint(a.run/'knowledge_base.jsonl',version=version))
        (a.run/'timing.json').write_text(json.dumps({'elapsed_seconds':time.time()-started,'mode':a.mode}))
        print('COMPLETE',a.run,flush=True)

if __name__=='__main__':main()
