"""Notebook adapter for real demiflow operators, immutable step outputs and bounded views.

Execution remains flow.execute -> demiflow.map_async; no alternate execution engine.
"""
import asyncio
import html
import json
import random
from pathlib import Path
from .contracts import code_fingerprint, digest, immutable, read, run_lock, runtime_version
from .flow import check_run_location, execute
from .ops.operators import CollectRows


def notebook_source(path):
    doc=read(path)
    return [''.join(c['source']) for c in doc['cells'] if c['cell_type']=='code']


class DebugSession:
    def __init__(self, run, project, dataset, config, notebook):
        self.run=Path(run).resolve()
        check_run_location(self.run, project, dataset)
        self.notebook=Path(notebook)
        self.code=code_fingerprint()
        self.cells=notebook_source(self.notebook)
        self.config=config
        with run_lock(self.run):
            immutable(self.run/'debug_manifest.json', {
                'code_hash':self.code, 'notebook_code':self.cells,
                'runtime':runtime_version(), 'config':config,
                'project':str(project), 'dataset':str(dataset),
                'scope':'Operator debugging; not approved knowledge or end-to-end acceptance.'})

    async def step(self, name, rows, factory):
        """Run one real operator in a worker thread (Jupyter already owns an event loop)."""
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in name):
            raise ValueError('Step name must be lowercase ASCII letters, digits or underscores')
        rows=list(rows)
        def work():
            if code_fingerprint()!=self.code or notebook_source(self.notebook)!=self.cells:
                raise ValueError('Code changed: save notebook, restart kernel and choose a new RUN_NAME')
            with run_lock(self.run):
                key=digest({'rows':rows,'name':name,'config':self.config,'code':self.code})
                path=self.run/'debug_steps'/f'{name}.json'
                if path.exists():
                    saved=read(path)
                    if saved['input_hash']!=key:
                        raise ValueError('Step inputs changed: choose a new RUN_NAME')
                    return saved['rows'],True
                collector=CollectRows()
                execute(rows,factory(),collector)
                immutable(path,{'input_hash':key,'rows':collector.results})
                return collector.results,False
        result,reused=await asyncio.to_thread(work)
        print(f'{name}: input={len(rows)}, output={len(result)}, reused={reused}')
        return result

    async def knowledge(self, cls, rows):
        from .local_model import LocalModel
        return await self.step(cls.label, rows,
            lambda:cls(self.run,self.config['model'],LocalModel(self.run,self.config['model'])))


def select(rows, limit=100, sample=False, seed=42):
    if limit<0:raise ValueError('limit must be nonnegative')
    rows=list(rows)
    return random.Random(seed).sample(rows,min(limit,len(rows))) if sample else rows[:limit]


def _value_html(value, chars=None):
    text=json.dumps(value,ensure_ascii=False,indent=2) if isinstance(value,(dict,list)) else str(value)
    full='<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto;margin:0">'+html.escape(text)+'</pre>'
    if chars is not None and len(text)>chars:
        return '<details><summary style="white-space:pre-wrap;overflow-wrap:anywhere">'+html.escape(text[:chars])+f'…（共{len(text)}字符，展开全文）</summary>'+full+'</details>'
    return full


def show(rows, limit=100, sample=False, seed=42, columns=None, chars=180):
    """Expandable full values; chars only controls collapsed previews, never payload."""
    from IPython.display import HTML,display
    rows=list(rows);selected=select(rows,limit,sample,seed)
    if chars is not None and chars<0:raise ValueError('chars must be nonnegative or None')
    keys=list(columns) if columns is not None else list(dict.fromkeys(k for row in selected for k in row))
    print(f'Display {len(selected)}/{len(rows)} rows; sample={sample}, seed={seed}. Click long fields for full text.')
    header='<tr><th>#</th>'+''.join('<th>'+html.escape(k)+'</th>' for k in keys)+'</tr>'
    body=''.join('<tr><td>'+str(i)+'</td>'+''.join('<td style="vertical-align:top;min-width:100px;max-width:650px;padding:8px;border:1px solid #ddd">'+_value_html(row.get(k,''),chars)+'</td>' for k in keys)+'</tr>' for i,row in enumerate(selected))
    display(HTML('<div style="overflow:auto;max-height:850px"><table style="border-collapse:collapse;text-align:left">'+header+body+'</table></div>'))


def detail(row):
    from IPython.display import HTML,display
    if isinstance(row,dict):
        content=''.join('<h4>'+html.escape(str(k))+'</h4>'+_value_html(v) for k,v in row.items())
    else:content=_value_html(row)
    display(HTML(content))


class SavedDebugSession:
    """Read-only notebook review; never instantiate operators or enforce current code hashes."""
    def __init__(self,run):
        self.run=Path(run).resolve()
        manifest=read(self.run/'debug_manifest.json')
        self.code=manifest['code_hash']
    async def step(self,name,rows,factory):
        saved=read(self.run/'debug_steps'/f'{name}.json')
        print(f'{name}: read saved output ({len(saved["rows"])} rows); no execution')
        return saved['rows']
    async def knowledge(self,cls,rows):
        path=self.run/'debug_steps'/f'{cls.label}.json'
        if not path.exists():
            print(f'{cls.label}: no saved output; view mode will not call a model')
            return []
        return await self.step(cls.label,rows,None)


def materials(bundles):
    return [{'case':b['request']['kind']+':'+b['request']['value'],
             'kind':m['kind'],'title':m['record'].get('title'),
             'text':m.get('cleaning',{}).get('text',m.get('document',{}).get('text') or m['record'].get('text') or m['record'].get('sections')),
             'cleaning':m.get('cleaning',{}).get('status'),
             'bytes':m.get('bytes'), 'record':m} for b in bundles for m in b['materials']]
