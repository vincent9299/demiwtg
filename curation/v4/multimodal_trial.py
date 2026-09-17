"""Run the explicit multimodal graph on a frozen, declared identity boundary."""
import argparse,json,ast
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest,immutable,source_code,runtime_version,run_lock
from .pipeline import DEFAULT
from .ops.prompt_config import knowledge_prompt_pack,prompt_execution_options,save_prompt_config
from .ops.source_blocks import BuildSourceBlocks,BatchSourceBlocks,ApplyBlockSelection,merge_block_decisions
from .ops.prompt_operators import BuildCandidateRecords
from .final_results import BuildKnowledgeRecord
from curation.v4.ops.multimodal import (SelectAvailableImages, BatchImageSelection, ApplyImageSelection,
    merge_image_decisions, SelectRelatedMaterials, BatchJointMaterials, ApplyJointExtraction,
    ApplyJointVerification, merge_joint_batches, merge_joint_scope, PrepareJointMerge, ApplyJointMerge)

def run_trial(source,run,config,through='export',reuse_selection=None):
    with run_lock(run):
        manifest={'source':str(source),'source_sha256':digest(source.read_bytes()),'code':source_code(),
                  'runtime':runtime_version(),'config':config,'scope':'Explicit frozen identity boundary; not a fresh full-dataset run'}
        if reuse_selection:
            parent=json.loads((reuse_selection/'manifest.json').read_text())
            if parent['source_sha256']!=manifest['source_sha256'] or {k:v for k,v in parent['config'].items() if k not in {'joint_batch_limit','joint_text_chars'}}!={k:v for k,v in config.items() if k not in {'joint_batch_limit','joint_text_chars'}}:
                raise ValueError('Selection reuse requires identical source/config')
            current=manifest['code'];old=parent['code']
            for path in ['ops/source_blocks.py','ops/body_text.py','ops/prompts/relevance.yaml','ops/prompts/select_images.yaml','ops/prompt_config.py']:
                if old.get(path)!=current.get(path):raise ValueError('Selection implementation/prompt changed: '+path)
            names={'pixels','SelectAvailableImages','BatchImageSelection','ApplyImageSelection','merge_image_decisions','SelectRelatedMaterials'}
            def selection_ast(code):
                return [ast.dump(n) for n in ast.parse(code).body if getattr(n,'name',None) in names]
            if selection_ast(old['ops/multimodal.py'])!=selection_ast(current['ops/multimodal.py']):
                raise ValueError('Image selection changed')
            manifest['selection_reuse']={'parent':str(reuse_selection),'related_sha256':digest((reuse_selection/'datasets/related_materials.jsonl').read_bytes())}
        immutable(run/'manifest.json',manifest);version=digest(manifest)
        tables=run/'datasets';knowledge_run=run/'knowledge'
        pack,text=knowledge_prompt_pack(config);options=prompt_execution_options(run,config)
        save_prompt_config(run,text,options)
        data=local_data(prompt_packs={'knowledge.yaml':pack},max_prompt_requests=config.get('max_calls'),prompt_options=options)
        identified=data.read_json(str(source))
        # 10M. 相关材料筛选 → 图文联合提炼 → 引用/像素核验 → 跨批候选去重与冲突处理。
        # 以下分批只控制单次输入；文字组×图片组完整遍历，不按已有知识找图片。
        if config.get('text_mode') == 'multimodal':
            if reuse_selection:
                related=data.read_json(str(reuse_selection/'datasets/related_materials.jsonl')).checkpoint(tables/'related_materials.jsonl',version=version)
            else:
                blocks = (identified.map(BuildSourceBlocks(config.get('block_unit_chars',1800), body_only=True))
                    .map(SelectAvailableImages()).checkpoint(tables/'multimodal_materials.jsonl', version=version))
                text_requests = blocks.flat_map(BatchSourceBlocks(config.get('block_batch_chars',8000)))
                text_decisions = (text_requests.map_prompt_async('select_blocks', config='knowledge.yaml',
                    inputs={'payload':'block_prompt'}, output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                    .map_cached(ApplyBlockSelection(relevance_only=True),cache_dir=knowledge_run/'cache/text_relevance',version=version)
                    .checkpoint(tables/'text_relevance.jsonl',version=version)
                    .reduce_by_key('case_id',merge_block_decisions))
                image_requests = blocks.flat_map(BatchImageSelection(config.get('image_batch_size',4)))
                image_decisions = (image_requests.map_prompt_async('select_images',config='knowledge.yaml',
                    inputs={'payload':'image_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                    .map_cached(ApplyImageSelection(),cache_dir=knowledge_run/'cache/image_relevance',version=version)
                    .checkpoint(tables/'image_relevance.jsonl',version=version)
                    .reduce_by_key('case_id',merge_image_decisions))
                related = (blocks.join(text_decisions,on='case_id',how='left')
                    .join(image_decisions,on='case_id',how='left').map(SelectRelatedMaterials())
                    .checkpoint(tables/'related_materials.jsonl',version=version))
            if through=='organize':return related
            joint_requests = (related.flat_map(BatchJointMaterials(config.get('joint_text_chars',2500),config.get('image_batch_size',4)))
                .checkpoint(tables/'joint_requests.jsonl',version=version))
            # 可选工程调用预算：先保存完整请求清单；未处理组保留为pending，默认不限制。
            joint_scope = joint_requests.map(lambda r: {'case_id':r['case_id'],'planned_joint_batch_ids':[r['batch_id']]}).reduce_by_key('case_id',merge_joint_scope)
            related = related.join(joint_scope,on='case_id',how='left').map(lambda r: {**r, 'joint_batch_limit': config.get('joint_batch_limit')})
            if config.get('joint_batch_limit') is not None:
                joint_requests = joint_requests.filter(lambda r:r['batch_index'] < config['joint_batch_limit'])
            extracted = (joint_requests.map_prompt_async('joint_extract',config='knowledge.yaml',
                inputs={'payload':'joint_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyJointExtraction(),cache_dir=knowledge_run/'cache/joint_extract',version=version)
                .checkpoint(tables/'joint_extract.jsonl',version=version))
            if through=='extract':return extracted
            verified = (extracted.map_prompt_async('joint_verify',config='knowledge.yaml',
                inputs={'payload':'verify_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',
                when=lambda r:bool(r.get('verify_prompt',{}).get('facts')),concurrency=1,queue_depth=1)
                .map_cached(ApplyJointVerification(),cache_dir=knowledge_run/'cache/joint_verify',version=version)
                .checkpoint(tables/'joint_verify.jsonl',version=version)
                .reduce_by_key('case_id',merge_joint_batches))
            merge_requests = related.join(verified,on='case_id',how='left').map(PrepareJointMerge())
            reviewed = (merge_requests.map_prompt_async('merge_joint',config='knowledge.yaml',inputs={'payload':'merge_prompt'},
                output='prompt_result',call_output='prompt_call',error_output='prompt_error',
                when=lambda r:bool(r.get('merge_prompt',{}).get('facts')),concurrency=1,queue_depth=1)
                .map_cached(ApplyJointMerge(),cache_dir=knowledge_run/'cache/joint_merge',version=version)
                .checkpoint(tables/'joint_knowledge.jsonl',version=version))
            if through in {'consolidate','fidelity','evidence'}:return reviewed
            exported = (reviewed.map_cached(BuildCandidateRecords(knowledge_run,config),cache_dir=knowledge_run/'cache/joint_export',version=version)
                .checkpoint(tables/'knowledge_export.jsonl',version=version))
            return exported.map(BuildKnowledgeRecord()).checkpoint(run/'knowledge_base.jsonl',version=version)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--joint-batch-limit',type=int);p.add_argument('--through',default='export');p.add_argument('--reuse-selection',type=Path);a=p.parse_args()
    config={**DEFAULT,'text_mode':'multimodal','body_only':True,'max_calls':None,'max_output_tokens':12000,'timeout_s':600,'temperature':0,'joint_batch_limit':a.joint_batch_limit}
    run_trial(a.source,a.run,config,a.through,a.reuse_selection)
if __name__=='__main__':main()
