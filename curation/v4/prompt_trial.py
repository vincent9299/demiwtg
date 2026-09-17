"""Frozen joint-prompt experiment on existing requests using native demiflow."""
import argparse,json,hashlib,time
from pathlib import Path
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.paragraphs import ApplyParagraphs,ApplyParagraphReview,SelectRetainedParagraphs
from .ops.paragraph_pipeline import PrepareVerifiedParagraphs,SourceCatalog,FormatTopicArticle,FinalKnowledgeRecord
from .ops.topic_articles import TopicRows
from .quality_pipeline import repair_topics
from .pipeline_comparison import summarize_run
from demiflow.standalone import local_data
from demiflow.operator_llm.parser import parse_prompt_pack
import yaml

def main():
 p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--parent',type=Path,required=True);p.add_argument('--prompt',type=Path,required=True);p.add_argument('--reuse-extraction',type=Path);p.add_argument('--through',choices=['extract','export'],default='extract');a=p.parse_args()
 cfg={'model':'qwen3.8-27b','base_url':'http://127.0.0.1:8000/v1','timeout_s':900,'max_output_tokens':16384,'temperature':0,'max_calls':None}
 _,text=knowledge_prompt_pack(cfg);pack=yaml.safe_load(text)
 new=yaml.safe_load(a.prompt.read_text())
 from .ops.prompt_loader import load_instruction
 spec=pack['prompts']['joint_paragraphs'];spec['template']=spec['template'].replace(load_instruction('joint_paragraphs'),new['instruction']);spec['version']=new['version']
 text=yaml.safe_dump(pack,allow_unicode=True,sort_keys=False)
 inputs={name:str((a.parent/name).resolve()) for name in ['datasets/joint_requests.jsonl','datasets/related_materials.jsonl']}
 hashes={k:hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in inputs.items()}
 if a.reuse_extraction:
  frozen=json.loads((a.reuse_extraction/'trial_manifest.json').read_text())
  if frozen['sha256']!=hashes or frozen['prompt']!=text:raise ValueError('Extraction inputs/prompt differ')
  inputs['extraction']=str((a.reuse_extraction/'datasets/paragraph_extract.jsonl').resolve())
  hashes['extraction']=hashlib.sha256(Path(inputs['extraction']).read_bytes()).hexdigest()
 manifest={'inputs':inputs,'sha256':hashes,'config':cfg,'prompt':text,'code':source_code(),'runtime':runtime_version()};version=digest(manifest)
 started=time.time()
 with run_lock(a.run):
  immutable(a.run/'trial_manifest.json',manifest)
  options=prompt_execution_options(a.run,cfg);save_prompt_config(a.run,text,options)
  data=local_data(prompt_packs={'knowledge.yaml':parse_prompt_pack(text)},prompt_options=options)
  tables=a.run/'datasets'
  requests=data.read_json(inputs['datasets/joint_requests.jsonl']).checkpoint(tables/'joint_requests.jsonl',version=version)
  if a.reuse_extraction:
   extracted=data.read_json(inputs['extraction']).checkpoint(tables/'paragraph_extract.jsonl',version=version)
  else:
   extracted=(requests.map_prompt_async('joint_paragraphs',config='knowledge.yaml',inputs={'payload':'joint_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
       .map_cached(ApplyParagraphs(),cache_dir=a.run/'cache/extract',version=version).checkpoint(tables/'paragraph_extract.jsonl',version=version))
  if a.through=='export':
   verified=(extracted.map_prompt_async('verify_paragraphs',config='knowledge.yaml',inputs={'payload':'verify_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
      .map_cached(ApplyParagraphReview(),cache_dir=a.run/'cache/verify',version=version).checkpoint(tables/'paragraph_verify.jsonl',version=version))
   originals=verified.join(requests.select_columns(['batch_id','pixel_images']),on='batch_id',how='left').map(PrepareVerifiedParagraphs(a.run))
   final,repairs=repair_topics(originals,run=a.run,version=version,stage='initial')
   final.checkpoint(a.run/'paragraphs.jsonl',version=version);requests.union(repairs).checkpoint(a.run/'requests.jsonl',version=version)
   related=data.read_json(inputs['datasets/related_materials.jsonl'])
   catalogs=related.map(SourceCatalog())
   articles=(final.map(SelectRetainedParagraphs()).map(lambda r:r['content']).flat_map(TopicRows()).join(catalogs,on='concept',how='left').map(FormatTopicArticle()).checkpoint(tables/'articles.jsonl',version=version))
   grouped=articles.reduce_by_key('concept',lambda a,r:{'concept':r['concept'],'articles':a['articles']+[r['article']]},initial={'articles':[]})
   related.map(lambda r:{**r,'concept':r['identity']['target_label']}).join(grouped,on='concept',how='left').map(FinalKnowledgeRecord()).checkpoint(a.run/'knowledge_base.jsonl',version=version)
   (a.run/'result_summary.json').write_text(json.dumps(summarize_run(a.run),ensure_ascii=False,indent=2))
  (a.run/f'{a.through}_timing.json').write_text(json.dumps({'elapsed_seconds':time.time()-started}))
 print(a.run,a.through,'COMPLETE',flush=True)
if __name__=='__main__':main()
