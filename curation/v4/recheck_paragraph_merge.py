"""Repair accounting and missing output reviews without regenerating prose."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code
from .ops.paragraph_merge import ApplyParagraphMerge
from .ops.paragraphs import ApplyParagraphReview
from .paragraph_results import export_results
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config


class RecheckMerge:
    def __call__(self,row):
        old=row['saved'];req=row['request']
        parsed=ApplyParagraphMerge()({**req,'prompt_result':old['raw_merge'],
                                      'prompt_call':old['extraction_call'],'prompt_error':old['extraction_error']})
        # Assert that only machine status/accounting changes; review reuse must
        # never conceal a prose, citation, placement or image-observation change.
        keys=['block_id','type','text','citations','image_refs','image_id','caption','region','limitations','related_block_ids','input_ids']
        def content(ts):return [(t['title'],[{k:b[k] for k in keys if k in b} for b in t['blocks']]) for t in ts]
        import copy
        aligned_old=copy.deepcopy(old['topics'])
        for repair in parsed['quote_alignment_repairs']:
            for t in aligned_old:
                for b in t['blocks']:
                    if b['block_id']==repair['block_id']:
                        for c in b.get('citations',[]):
                            if c['source_id']==repair['source_id'] and c['quote']==repair['before']:c['quote']=repair['after']
        if content(parsed['topics'])!=content(aligned_old):raise ValueError('Content changed: cannot reuse semantic review')
        reviews=old['raw_verification'].get('reviews',[])
        ids=[b['block_id'] for t in parsed['topics'] for b in t['blocks']]
        if any(sum(r.get('block_id')==bid for r in reviews)!=1 for bid in ids):
            return {**parsed,'needs_new_review':True,'review_repair_reason':'Saved review omitted or duplicated output blocks; corrected payload disambiguates output IDs'}
        result=ApplyParagraphReview()({**parsed,'prompt_result':old['raw_verification'],
                                      'prompt_call':old['verification_call'],'prompt_error':old['verification_error']})
        result['review_reuse_reason']='Exact unchanged prose, citations, image observations and placement; only inverse accounting and unique original quote formatting reconstructed'
        return {**result,'needs_new_review':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-run',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--source-file',type=Path,required=True);a=p.parse_args()
    files=[a.source_run/'requests.jsonl',a.source_run/'paragraphs.jsonl',a.source_file]
    config={**DEFAULT,'max_calls':None,'max_output_tokens':12000,'timeout_s':600,'temperature':0}
    m={'config':config,'inputs':{str(f.resolve()):digest(f.read_bytes()) for f in files},'code':source_code(),'scope':'Deterministic accounting/quote-format repair; unchanged complete reviews reused, missing output reviews rerun with corrected payload'}
    immutable(a.run/'manifest.json',m);version=digest(m)
    pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(a.run,config);save_prompt_config(a.run,text,options)
    data=local_data(prompt_packs={'knowledge.yaml':pack},prompt_options=options)
    requests=data.read_json(str(files[0])).checkpoint(a.run/'requests.jsonl',version=version).take_all()
    req={r['batch_id']:r for r in requests}
    prepared=(data.read_json(str(files[1])).map(lambda r:{'saved':r,'request':req[r['batch_id']]})
              .map(RecheckMerge()).checkpoint(a.run/'rechecked.jsonl',version=version))
    reused=prepared.filter(lambda r:not r['needs_new_review']).checkpoint(a.run/'reused.jsonl',version=version)
    renewed=(prepared.filter(lambda r:r['needs_new_review'])
             .map_prompt_async('verify_merged_paragraphs',config='knowledge.yaml',inputs={'payload':'verify_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
             .map_cached(ApplyParagraphReview(),cache_dir=a.run/'cache/verify',version=version).checkpoint(a.run/'renewed.jsonl',version=version))
    data.from_iter(lambda:iter(reused.take_all()+renewed.take_all())).checkpoint(a.run/'paragraphs.jsonl',version=version)
    export_results([a.run],a.run/'published',a.source_file)
    print(a.run/'published/preview.html')

if __name__=='__main__':main()
