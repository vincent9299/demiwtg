"""Execute the visible notebook cell by cell, preserving outputs and execution events."""
import argparse, hashlib, json, shutil, time
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from .contracts import digest, immutable, snapshot


def reuse_completed_inputs(parent, run):
    """Only unchanged pre-model inputs; no failed, partial or model result import."""
    parent,run=Path(parent),Path(run)
    old=json.loads((parent/'manifest.json').read_text());new=json.loads((run/'manifest.json').read_text())
    for key in ['sources','ids','group_size','config']:
        if old[key]!=new[key]:raise ValueError('Input/config changed: '+key)
    for s in old['sources']:
        if snapshot(s['path'])!={k:s[k] for k in ['path','exists','size','mtime_ns','inode'] if k in s}:
            raise ValueError('Source changed')
    for name in ['ops/dataset_operators.py','ops/operators.py']:
        if old['code'][name]!=new['code'][name]:raise ValueError('Input operator changed')
    version=digest(new);copied=[]
    for srcname,dstname in [('selected_concepts','selected_concepts'),('document_links','document_links'),
                            ('selected_documents','selected_documents'),('raw_documents_async_v1','raw_documents')]:
        src=parent/'datasets'/(srcname+'.jsonl')
        if not src.exists():continue
        meta=json.loads(src.with_suffix('.jsonl.meta.json').read_text())
        expected=digest({'input_version':digest(old),'execution':'checkpoint-in-thread-v1'}) if srcname=='raw_documents_async_v1' else digest(old)
        if meta['version']!=expected:raise ValueError('Parent checkpoint version mismatch')
        if dstname=='raw_documents':
            for r in map(json.loads,src.open()):
                if r['read_status']!='readable':raise ValueError('Unsuccessful original document read')
                if digest((Path(new['sources'][0]['path']).parent.parent/r['path']).read_bytes())!=r['raw_sha256']:
                    raise ValueError('Raw document bytes changed')
        dst=run/'datasets'/(dstname+'.jsonl');dst.parent.mkdir(parents=True,exist_ok=True)
        sha=hashlib.sha256(src.read_bytes()).hexdigest()
        immutable(dst.with_suffix('.jsonl.meta.json'),{'version':version})
        if dst.exists():
            if hashlib.sha256(dst.read_bytes()).hexdigest()!=sha:raise ValueError('Existing output differs')
        else:shutil.copyfile(src,dst)
        copied.append({'input':str(src.resolve()),'output':str(dst.resolve()),'sha256':sha})
    immutable(run/'reused_inputs.json',{'parent':str(parent.resolve()),'files':copied})


def execute(notebook,run,reuse_inputs=None):
    notebook,run=Path(notebook).resolve(),Path(run).resolve();run.mkdir(parents=True,exist_ok=True)
    n=nbformat.read(notebook,as_version=4)
    sources=[c.source for c in n.cells]
    def save():
        latest=nbformat.read(notebook,as_version=4)
        if [c.source for c in latest.cells]!=sources:raise RuntimeError('Notebook edited during execution; refusing overwrite')
        for old,new in zip(latest.cells,n.cells):
            if old.cell_type=='code':old.outputs=new.outputs;old.execution_count=new.execution_count
        tmp=notebook.with_suffix('.ipynb.tmp');nbformat.write(latest,tmp);tmp.replace(notebook)
    def event(**kw):
        with (run/'notebook_events.jsonl').open('a') as f:f.write(json.dumps({'time':time.time(),**kw},ensure_ascii=False)+'\n')
        print(json.dumps(kw,ensure_ascii=False),flush=True)
    client=NotebookClient(n,timeout=3600,kernel_name='demiwtg',resources={'metadata':{'path':str(notebook.parents[2])}})
    with client.setup_kernel():
        title=''
        for i,c in enumerate(n.cells):
            if c.cell_type=='markdown':title=c.source.splitlines()[0];continue
            event(cell=i,title=title,status='started')
            try:
                client.execute_cell(c,i)
                if 'immutable(RUN /' in c.source and "'manifest.json'" in c.source and reuse_inputs:
                    reuse_completed_inputs(reuse_inputs,run)
                save();event(cell=i,title=title,status='completed')
            except BaseException as error:
                save();event(cell=i,title=title,status='failed',error=str(error));raise
    event(status='finished')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--notebook',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--reuse-inputs',type=Path)
    a=p.parse_args();execute(a.notebook,a.run,a.reuse_inputs)
