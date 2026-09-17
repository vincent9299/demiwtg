"""Integrate retained cross-batch paragraphs using native demiflow prompt operators."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.paragraph_merge import PrepareParagraphMerge,ApplyParagraphMerge
from .ops.paragraphs import ApplyParagraphReview
from .paragraph_results import export_results


def read_rows(p):return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-run',type=Path,action='append',required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--exclusions-file',type=Path,required=True);p.add_argument('--source-file',type=Path,required=True)
    p.add_argument('--max-payload-chars',type=int,default=120000);a=p.parse_args()
    config={**DEFAULT,'max_calls':None,'max_output_tokens':24000,'timeout_s':600,'temperature':0}
    with run_lock(a.run):
        files=[r/f for r in a.input_run for f in ['requests.jsonl','paragraphs.jsonl']]+[a.exclusions_file,a.source_file]
        manifest={'inputs':{str(f.resolve()):digest(f.read_bytes()) for f in files},'code':source_code(),'runtime':runtime_version(),'config':config,'max_payload_chars':a.max_payload_chars,'scope':'Bounded per-concept integration; existing image observations immutable; new text verified against original passages'}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest)
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(a.run,config);save_prompt_config(a.run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},prompt_options=options)
        rows=[]
        for r in a.input_run:
            requests={x['batch_id']:x for x in read_rows(r/'requests.jsonl')}
            for x in read_rows(r/'paragraphs.jsonl'):
                rows.append({**requests[x['batch_id']],**x,'run':str(r.resolve()),'concept':x['joint_prompt']['concept']})
        requests=(data.from_iter(lambda:iter(rows))
                  .reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'rows':acc['rows']+[r]},initial={'rows':[]})
                  .map(PrepareParagraphMerge(json.loads(a.exclusions_file.read_text()),a.max_payload_chars))
                  .checkpoint(a.run/'requests.jsonl',version=version))
        print('Merge inputs frozen',flush=True)
        extracted=(requests.map_prompt_async('merge_paragraphs',config='knowledge.yaml',inputs={'payload':'merge_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                   .map_cached(ApplyParagraphMerge(),cache_dir=a.run/'cache/merge',version=version)
                   .checkpoint(a.run/'extracted.jsonl',version=version))
        print('Merge complete',flush=True)
        (extracted.map_prompt_async('verify_merged_paragraphs',config='knowledge.yaml',inputs={'payload':'verify_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
         .map_cached(ApplyParagraphReview(),cache_dir=a.run/'cache/verify',version=version).checkpoint(a.run/'paragraphs.jsonl',version=version))
        public=export_results([a.run],a.run/'published',a.source_file)
        before=export_results(a.input_run,a.run/'before',a.source_file,a.exclusions_file)
        def stats(content):
            concepts={}
            for r in content:
                v=concepts.setdefault(r['concept'],{'topics':0,'text_blocks':0,'image_blocks':0})
                v['topics']+=len(r['topics'])
                for t in r['topics']:
                    for b in t['blocks']:v['text_blocks' if b['type']=='text' else 'image_blocks']+=1
            return concepts
        results=read_rows(a.run/'paragraphs.jsonl')
        calls=[r[k] for r in results for k in ['extraction_call','verification_call']]
        immutable(a.run/'report.json',{'before':stats(before),'after':stats(public),
                  'model_calls':len(calls),'tokens':sum(c['usage']['total_tokens'] for c in calls),
                  'sum_call_elapsed_s':sum(c['elapsed_s'] for c in calls),
                  'issues':{r['case_id']:r['merge_validation_issues'] for r in results},
                  'note':'Model-integrated sample, not independently fact-verified. Existing image descriptions preserved without new pixel claims.'})
        print(a.run/'published/preview.html',flush=True)

if __name__=='__main__':main()
