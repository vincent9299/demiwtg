"""Legacy prepared-query-bundle runner; retains historical status/inspect.

Current record-first entry: python -m curation.v4.record_flow.
Knowledge operators remain shared with that entry.

run --input-run PREPARED_RUN --run NEW_RUN [--config JSON] [--through STAGE]
status --run RUN; inspect --run RUN --stage STAGE [--case legacy:芦笙]
No notebook generation, permanent identity merge, benchmark generation or scoring.
"""
import argparse
import json
from pathlib import Path
from .contracts import ROOT,code_fingerprint,source_code,runtime_version,digest,read,immutable,run_lock
from .flow import execute,check_run_location
from .ops.operators import CollectRows
from .local_model import LocalModel
from .ops.knowledge_stages import CleanMaterials,ResolveIdentity,OrganizeMaterials,ExtractKnowledge,ConsolidateKnowledge,CheckImageSupport,ExportCandidates

STAGES=[CleanMaterials,ResolveIdentity,OrganizeMaterials,ExtractKnowledge,ConsolidateKnowledge,CheckImageSupport,ExportCandidates]
DEFAULT={'base_url':'http://127.0.0.1:8000/v1','model':'qwen3.8-27b','max_calls':16,'max_output_tokens':3500,
         'timeout_s':240,'max_cases':4,'identity_docs':12,'identity_images':12,'max_docs':2,'max_chars_per_doc':6500,'max_input_chars':13000,
         'max_images':2,'max_image_bytes':8*1024*1024,'cos_base_url':None}


def run(input_run,run_dir,config,through='export',model_factory=LocalModel):
    input_run=Path(input_run).resolve();run_dir=Path(run_dir).resolve()
    prepared=read(input_run/'manifest.json');report=read(input_run/'report.json')
    check_run_location(run_dir,prepared['project'],prepared['dataset'])
    if input_run==run_dir:raise ValueError('Knowledge stages need a new run, not the historical material directory')
    unknown=set(config)-set(DEFAULT)
    if unknown:raise ValueError(f'Unknown config keys: {unknown}')
    config={**DEFAULT,**config}
    for key in ['max_calls','max_output_tokens','max_cases','identity_docs','identity_images','max_docs','max_chars_per_doc','max_input_chars','max_images','max_image_bytes']:
        if not isinstance(config[key],int) or config[key]<0:raise ValueError(f'Invalid limit: {key}')
    if not 0<len(report['bundles'])<=config['max_cases']:raise ValueError('Input batch exceeds the configured small-batch limit')
    if through not in [s.label for s in STAGES]:raise ValueError('Unknown stage')
    inputs=[];hashes={}
    for item in report['bundles']:
        p=Path(item['path']).resolve()
        if not p.is_relative_to(input_run/'bundles'):raise ValueError('Bundle reference escapes input run')
        raw=p.read_bytes();bundle=json.loads(raw);hashes[str(p)]=digest(raw)
        if any(m['kind']=='clean_docs' for m in bundle['materials']):
            raise ValueError('Historical clean_docs inputs are retired; prepare a new run from datasets. Use inspect for old results.')
        inputs.append({'case_id':digest({'concept_id':bundle['concept_id'],'bundle_hash':digest(raw)})[:20],'bundle':bundle})
    with run_lock(run_dir):
        manifest={'phase':'small_batch_knowledge_candidates','input_run':str(input_run),'input_manifest_sha256':digest((input_run/'manifest.json').read_bytes()),
                  'input_report_sha256':digest((input_run/'report.json').read_bytes()),'bundle_hashes':hashes,
                  'config':config,'code_hash':code_fingerprint(),'runtime':runtime_version(),
                  'stages':[s.label for s in STAGES],'scope':'Engineering/development candidates, not formal sampling or human gold.'}
        immutable(run_dir/'pipeline_manifest.json',manifest);immutable(run_dir/'code_snapshot.json',source_code())
        model=model_factory(run_dir,config);ops=[]
        for cls in STAGES:
            ops.append(cls(run_dir,config,model))
            if cls.label==through:break
        collector=CollectRows();execute(inputs,*ops,collector)
        return {'through':through,'cases':len(inputs),'calls_executed_this_invocation':model.calls,
                'stage_tasks_executed':{s.label:s.executed for s in ops},'stage_tasks_reused':{s.label:s.reused for s in ops},
                'results':[{'case_id':r['case_id'],'request':r['bundle']['request'],'blocked':r.get('blocked'),
                            'facts':len(r.get('knowledge',{}).get('facts',[])),'image_support_rows':len(r.get('image_evidence',{}).get('result',{}).get('support',[]))} for r in collector.results]}


def status(run_dir):
    run_dir=Path(run_dir);manifest=read(run_dir/'pipeline_manifest.json');stages={}
    for stage in manifest['stages']:
        rows=[read(p)['output'] for p in sorted((run_dir/'stages'/stage).glob('*.json'))]
        stages[stage]={'saved':len(rows),'blocked':sum(bool(r.get('blocked')) for r in rows)}
    usage={};successful=0;failed=0
    for p in (run_dir/'calls').glob('*.response.json'):
        r=read(p)
        if r['status_code']==200:
            successful+=1
            for k,v in r['body'].get('usage',{}).items():
                if isinstance(v,(int,float)):usage[k]=usage.get(k,0)+v
        else:failed+=1
    return {'stages':stages,'call_requests':len(list((run_dir/'calls').glob('*.request.json'))),
            'http_successes':successful,'http_failures':failed,'usage':usage,'model':manifest['config']['model'],
            'note':'HTTP success and schema/quote checks are not semantic approval.'}


def inspect(run_dir,stage,case=None):
    outputs=[]
    for p in sorted((Path(run_dir)/'stages'/stage).glob('*.json')):
        row=read(p)['output'];r=row['bundle']['request'];label=r['kind']+':'+r['value']
        if case and case not in (label,row['case_id']):continue
        out={'case':label,'stage':stage,'blocked':row.get('blocked'),'record_path':str(p.resolve())}
        if stage=='clean':out['result']=row.get('cleaning_summary')
        elif stage=='identity':out['result']=row.get('identity');out['unexamined_materials']=len(row.get('identity_unexamined',[]))
        elif stage=='organize':
            pack=row.get('material_pack',{});out['passages']=[{k:p[k] for k in ('source_id','source_family','start','end','original_chars')} for p in pack.get('passages',[])]
            out['images']=[{'image_id':i['image_id'],'bytes':i['bytes']} for i in pack.get('images',[])]
            out['duplicates']=pack.get('duplicates',[]);out['omissions']=pack.get('omissions',[])
            out['image_gaps']=[{'material_id':i['material_id'],'bytes':i['bytes']} for i in pack.get('image_gaps',[])]
        elif stage=='extract':out['result']=row.get('extraction')
        elif stage=='consolidate':out['result']=row.get('knowledge')
        elif stage=='evidence':out['result']=row.get('image_evidence')
        else:out['result']=row.get('export')
        outputs.append(out)
    return outputs


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['run','status','inspect'])
    p.add_argument('--run',required=True,type=Path);p.add_argument('--input-run',type=Path);p.add_argument('--config',type=Path)
    p.add_argument('--through',choices=[s.label for s in STAGES],default='export')
    p.add_argument('--stage',choices=[s.label for s in STAGES],default='identity');p.add_argument('--case')
    a=p.parse_args()
    if a.action=='run':
        if not a.input_run:p.error('run needs --input-run')
        result=run(a.input_run,a.run,read(a.config) if a.config else {},a.through)
    elif a.action=='status':result=status(a.run)
    else:result=inspect(a.run,a.stage,a.case)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
