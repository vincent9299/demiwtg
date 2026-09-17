"""Native demiflow quality trial on frozen three-concept outputs; no raw scan or model swap."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.paragraphs import ApplyParagraphs,ApplyParagraphReview,SelectRetainedParagraphs
from .ops.topic_quality import PrepareTopicVerification,PrepareTopicRepairs,ApplyTopicRepairs,RetainReviewedTopics
from .ops.paragraph_similarity import ParagraphRows,EmbedParagraphBatch
from .ops.cross_batch import PlanCrossBatchReview,ApplyCrossBatchReview
from .ops.paragraph_pipeline import BuildLocalMergeGroups,ApplyLocalIntegration,PrepareVerifiedParagraphs,FormatTopicArticle
from .ops.paragraph_merge import ApplyParagraphMerge
from .ops.topic_articles import TopicRows
from .publish_topic_articles import publish_pipeline


def read_rows(path):return [json.loads(l) for l in Path(path).open() if l.strip()]


from .quality_pipeline import verify_topics,repair_topics,plan_topics

def run_trial(source,run,rounds=2):
    source,run=Path(source),Path(run)
    if not 0<=rounds<=2:raise ValueError('Bounded experiment: at most two integration passes')
    config={**DEFAULT,'max_calls':None,'max_output_tokens':16000,'timeout_s':900,'temperature':0}
    model=Path(__file__).resolve().parents[3]/'models/Qwen3-Embedding-0.6B'
    with run_lock(run):
        files=[source/p for p in ['paragraphs.jsonl','requests.jsonl','knowledge_base.jsonl','datasets/source_catalog.jsonl']]
        manifest={'inputs':{str(f.resolve()):digest(f.read_bytes()) for f in files},'code':source_code(),'runtime':runtime_version(),'config':config,
          'integration_rounds':rounds,'scope':'Frozen-output operator trial, new verification/visual prose/topic repair/same-batch and post-merge duplicate checks. Not a raw-data E2E rerun.'}
        immutable(run/'manifest.json',manifest);version=digest(manifest)
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(run,config);save_prompt_config(run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},prompt_options=options)
        originals={r['batch_id']:r for r in read_rows(source/'requests.jsonl')}
        bases={r['concept']:r for r in read_rows(source/'knowledge_base.jsonl')}
        def restore(r):
            req=originals[r['batch_id']];identity=bases[r['joint_prompt']['concept']]['identity']
            return {**r,'source_run':str(source),'run':str(run),'concept':r['joint_prompt']['concept'],
              'pixel_images':req['pixel_images'],'pixel_roles':req['pixel_roles'],
              'joint_prompt':{**r['joint_prompt'],'concept_identity':{k:identity[k] for k in ['target_label','identity_groups'] if k in identity}}}
        rows=data.read_json(str(source/'paragraphs.jsonl')).map(restore).checkpoint(run/'datasets/imported.jsonl',version=version)
        all_requests=data.read_json(str(source/'requests.jsonl'))
        rows=verify_topics(rows,run=run,version=version,stage='initial')
        rows,repairs=repair_topics(rows,run=run,version=version,stage='initial');all_requests=all_requests.union(repairs)
        print('Initial verification and topic repair completed',flush=True)
        for index in range(rounds):
            stage=f'round_{index+1}'
            groups,plans,reviews=plan_topics(rows,run=run,version=version,stage=stage,embedding_model=model)
            review_groups=reviews.reduce_by_key('concept',lambda a,r:{'concept':r['concept'],'review_rows':a['review_rows']+[r]},initial={'review_rows':[]})
            source_groups=rows.reduce_by_key('concept',lambda a,r:{'concept':r['concept'],'source_rows':a['source_rows']+[r]},initial={'source_rows':[]})
            tasks=(groups.join(review_groups,on='concept',how='left').join(source_groups,on='concept',how='left')
                .map(BuildLocalMergeGroups()).checkpoint(run/'datasets'/f'{stage}_merge_plan.jsonl',version=version))
            req=tasks.flat_map(lambda r:r['requests']).map(lambda r:{**r,'topic_gate_required':True,'run':str(run)})
            req=req.checkpoint(run/'datasets'/f'{stage}_merge_requests.jsonl',version=version);all_requests=all_requests.union(req)
            extracted=(req.map_prompt_async('merge_paragraphs',config='knowledge.yaml',inputs={'payload':'merge_payload','images':'pixel_images'},
                output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyParagraphMerge(),cache_dir=run/'cache'/f'{stage}_merge',version=version)
                .checkpoint(run/'datasets'/f'{stage}_merge_extract.jsonl',version=version))
            checked=verify_topics(extracted,run=run,version=version,stage=stage+'_merge')
            merged=checked.reduce_by_key('concept',lambda a,r:{'concept':r['concept'],'local_results':a['local_results']+[r]},initial={'local_results':[]})
            rows=(source_groups.join(merged,on='concept',how='left').map(ApplyLocalIntegration())
                .checkpoint(run/'datasets'/f'{stage}_assembly.jsonl',version=version).flat_map(lambda r:r['rows']))
            rows,repairs=repair_topics(rows,run=run,version=version,stage=stage);all_requests=all_requests.union(repairs)
            print(stage,'integration and repair completed',flush=True)
        # Audit residual duplication after the last allowed pass; never claim similarity proves clean output.
        _,residual,relations=plan_topics(rows,run=run,version=version,stage='residual',embedding_model=model)
        rows.checkpoint(run/'paragraphs.jsonl',version=version)
        all_requests.checkpoint(run/'requests.jsonl',version=version)
        catalogs=data.read_json(str(source/'datasets/source_catalog.jsonl'))
        articles=(rows.map(SelectRetainedParagraphs()).map(lambda r:r['content']).flat_map(TopicRows())
            .join(catalogs,on='concept',how='left').map(FormatTopicArticle()).checkpoint(run/'datasets/articles.jsonl',version=version))
        grouped=articles.reduce_by_key('concept',lambda a,r:{'concept':r['concept'],'new_knowledge':a['new_knowledge']+[r['article']]},initial={'new_knowledge':[]})
        (data.read_json(str(source/'knowledge_base.jsonl')).join(grouped,on='concept',how='left')
            .map(lambda r:{**{k:v for k,v in r.items() if k!='new_knowledge'},'knowledge':r.get('new_knowledge',[]),
                           'quality_trial':{'source_run':str(source),'rounds':rounds,'residual_reviews':str(run/'datasets/residual_relations.jsonl')}})
            .checkpoint(run/'knowledge_base.jsonl',version=version))
        print(publish_pipeline(run),flush=True)
    return run


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--rounds',type=int,default=2)
    a=p.parse_args();run_trial(a.source,a.run,a.rounds)

if __name__=='__main__':main()
