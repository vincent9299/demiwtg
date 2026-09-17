"""Reusable native demiflow subgraphs for topic verification, repair and relation review.

Row business operators live in ops/. These functions compose Dataset interfaces;
no custom execution engine, model transport, or persistence implementation.
"""
from .ops.topic_quality import PrepareTopicVerification,PrepareTopicRepairs,ApplyTopicRepairs,RetainReviewedTopics
from .ops.paragraphs import ApplyParagraphs,ApplyParagraphReview,SelectRetainedParagraphs
from .ops.paragraph_similarity import ParagraphRows,EmbedParagraphBatch
from .ops.cross_batch import PlanCrossBatchReview,ApplyCrossBatchReview

def verify_topics(rows,*,run,version,stage):
    """Explicit native Prepare → prompt → Apply → checkpoint; actual pixels included."""
    requests=rows.map(PrepareTopicVerification()).checkpoint(run/'datasets'/f'{stage}_verify_inputs.jsonl',version=version)
    verified=(requests.map_prompt_async('verify_paragraphs',config='knowledge.yaml',inputs={'payload':'verify_payload','images':'pixel_images'},
        output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
        .map_cached(ApplyParagraphReview(),cache_dir=run/'cache'/stage,version=version)
        .checkpoint(run/'datasets'/f'{stage}_verified.jsonl',version=version))
    return verified.join(requests.select_columns(['batch_id','pixel_images']),on='batch_id',how='left')


def repair_topics(rows,*,run,version,stage):
    requests=rows.flat_map(PrepareTopicRepairs()).checkpoint(run/'datasets'/f'{stage}_repair_requests.jsonl',version=version)
    extracted=(requests.map_prompt_async('repair_topics',config='knowledge.yaml',inputs={'payload':'repair_payload','images':'pixel_images'},
        output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
        .map_cached(ApplyParagraphs(),cache_dir=run/'cache'/f'{stage}_repair_extract',version=version)
        .checkpoint(run/'datasets'/f'{stage}_repair_extract.jsonl',version=version))
    checked=verify_topics(extracted,run=run,version=version,stage=stage+'_repair')
    results=checked.reduce_by_key('parent_batch_id',lambda a,r:{'batch_id':r['parent_batch_id'],'repairs':a['repairs']+[r]},initial={'repairs':[]})
    planned=requests.reduce_by_key('parent_batch_id',lambda a,r:{'batch_id':r['parent_batch_id'],'repair_requests':a['repair_requests']+[{'parent_topic_index':r['parent_topic_index']}]},initial={'repair_requests':[]})
    assembled=(rows.join(results,on='batch_id',how='left').join(planned,on='batch_id',how='left')
        .map(ApplyTopicRepairs()).checkpoint(run/'datasets'/f'{stage}_repair_assembly.jsonl',version=version)
        .flat_map(lambda r:r['rows']).map(RetainReviewedTopics())
        .checkpoint(run/'datasets'/f'{stage}_topics.jsonl',version=version))
    return assembled,requests


def plan_topics(rows,*,run,version,stage,embedding_model):
    public=rows.map(SelectRetainedParagraphs()).map(lambda r:r['content'])
    embeddings=(public.flat_map(ParagraphRows()).group_batches('embedding_bucket',max_rows=2,output='items')
        .map_cached(EmbedParagraphBatch(embedding_model),cache_dir=run/'cache'/f'{stage}_embedding',version=version)
        .checkpoint(run/'datasets'/f'{stage}_embeddings.jsonl',version=version).flat_map(lambda r:r['items']))
    groups=embeddings.reduce_by_key('concept',lambda a,r:{'concept':r['concept'],'items':a['items']+[r]},initial={'items':[]})
    plans=groups.map(PlanCrossBatchReview(include_same_batch=True)).checkpoint(run/'datasets'/f'{stage}_plans.jsonl',version=version)
    reviews=(plans.flat_map(lambda r:r['review_requests']).map(lambda r:{**r,'review_payload':{'items':r['items'],'reasons':r['reasons']}})
        .map_prompt_async('review_cross_batch',config='knowledge.yaml',inputs={'payload':'review_payload'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
        .map_cached(ApplyCrossBatchReview(),cache_dir=run/'cache'/f'{stage}_relations',version=version)
        .checkpoint(run/'datasets'/f'{stage}_relations.jsonl',version=version))
    return groups,plans,reviews

