"""Execute the actual notebook graph from CLI; no duplicate business orchestration."""
import argparse
import ast
import json
from pathlib import Path


def load_pipeline():
    notebook=Path(__file__).with_name('knowledge_debug.ipynb')
    cells=json.loads(notebook.read_text())['cells']
    scope={}
    setup=next(''.join(c['source']) for c in cells if c['cell_type']=='code' and 'from pathlib import Path' in ''.join(c['source']))
    exec(compile(setup,str(notebook)+':imports','exec'),scope)
    source=next(''.join(c['source']) for c in cells if ''.join(c['source']).startswith('def run_pipeline'))
    tree=ast.parse(source)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_pipeline')
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(notebook)+':run_pipeline','exec'),scope)
    return scope['run_pipeline']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--ids',nargs='+',required=True)
    p.add_argument('--dataset',type=Path,default=Path(__file__).resolve().parents[2]/'datasets/demiwtg')
    p.add_argument('--source-scope',choices=['all','collected'],default='all')
    p.add_argument('--reuse-materials',type=Path);p.add_argument('--reuse-preprocessing',type=Path);p.add_argument('--through',default='export');p.add_argument('--group-size',type=int,default=256)
    a=p.parse_args()
    config={'text_mode':'multimodal','body_only':True,'max_calls':None,'max_output_tokens':16384,
            'timeout_s':900,'temperature':0,'joint_batch_limit':None,
            'text_embedding_model':str(Path(__file__).resolve().parents[3]/'models/Qwen3-Embedding-0.6B'),
            'image_embedding_model':str(Path(__file__).resolve().parents[3]/'models/siglip2-base-patch16-224')}
    import time, shutil
    a.run.mkdir(parents=True,exist_ok=True)
    source_snapshot=a.run/'knowledge_debug_source.ipynb'
    if not source_snapshot.exists():shutil.copyfile(Path(__file__).with_name('knowledge_debug.ipynb'),source_snapshot)
    started=time.time()
    pipeline=load_pipeline()
    config={**pipeline.__globals__.get('MODEL_CONFIG',{}),**config}
    result=pipeline(a.run,a.dataset,ids=a.ids,group_size=a.group_size,
                          through=a.through,model_config=config,source_scope=a.source_scope,reuse_preprocessing=a.reuse_preprocessing,reuse_materials=a.reuse_materials)
    if a.through=='export':
        from .pipeline_comparison import summarize_run
        (a.run/'result_summary.json').write_text(json.dumps(summarize_run(a.run),ensure_ascii=False,indent=2))
    (a.run/'timing.json').write_text(json.dumps({'start':started,'end':time.time(),'elapsed_seconds':time.time()-started}))
    print(json.dumps({'run':str(a.run),'through':a.through,'output_type':type(result).__name__}),flush=True)

if __name__=='__main__':main()
