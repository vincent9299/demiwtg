"""File/version helpers for the notebook. No business stages or Dataset dispatch."""
import inspect
import json
import types
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import ROOT, digest, immutable, source_code, runtime_version, snapshot
from .flow import check_run_location


def read_saved(run, name):
    """Read one explicit JSONL checkpoint as a Dataset; never executes a stage."""
    path=Path(run)/'datasets'/f'{name}.jsonl'
    if not path.exists():raise ValueError(f'Missing checkpoint: {path}')
    return local_data().read_json(str(path))


def _code_record(code):
    """Stable executable description; excludes interpreter interning/cache state."""
    def constant(value):
        if isinstance(value,types.CodeType):return {'code':_code_record(value)}
        if isinstance(value,tuple):return {'tuple':[constant(v) for v in value]}
        if isinstance(value,frozenset):return {'frozenset':sorted((constant(v) for v in value),key=lambda v:json.dumps(v,sort_keys=True))}
        return {'type':type(value).__name__,'value':repr(value)}
    return {'bytecode':code.co_code.hex(),'constants':[constant(c) for c in code.co_consts],
            'names':list(code.co_names),'varnames':list(code.co_varnames),
            'freevars':list(code.co_freevars),'cellvars':list(code.co_cellvars),
            'argcount':code.co_argcount,'posonlyargcount':code.co_posonlyargcount,
            'kwonlyargcount':code.co_kwonlyargcount,'flags':code.co_flags,
            'stacksize':code.co_stacksize,'exceptiontable':code.co_exceptiontable.hex()}


def freeze_run(run, dataset, sources, config, pipeline, project=ROOT):
    """Freeze input/code/config before execution; return checkpoint version only."""
    run=Path(run).resolve();dataset=Path(dataset).resolve()
    check_run_location(run,Path(project).resolve(),dataset)
    if not 0<=config['sample_rate']<=1:raise ValueError('invalid sample rate')
    if config['group_size']<1 or (config['max_records_per_source'] is not None and config['max_records_per_source']<1):
        raise ValueError('invalid size')
    for source in sources:
        expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}
        if snapshot(source['path'])!=expected:raise ValueError('Source changed; use a new run')
    try:plan_source=inspect.getsource(pipeline)
    except (OSError,TypeError):
        # Dynamically compiled functions still freeze executable bytecode below.
        plan_source=None
    manifest={'schema':'demiflow-explicit-notebook/1','dataset':str(dataset),'sources':sources,
              'config':config,'source_code':source_code(),'runtime':runtime_version(),
              'pipeline_code':_code_record(pipeline.__code__)}
    immutable(run/'dataset_manifest.json',manifest)
    if plan_source is not None:immutable(run/'pipeline_source.json',{'source':plan_source})
    return digest(manifest)


def reuse_preprocessing(parent,run,version,settings):
    """Import only complete preprocessing checkpoints with unchanged business code.

    Used when execution plumbing changes after raw scanning; model outputs and
    partial files are never imported. Content hashes and parent version persist.
    """
    import hashlib,os
    parent=Path(parent).resolve();run=Path(run).resolve()
    old=json.loads((parent/'dataset_manifest.json').read_text())
    config={k:v for k,v in settings.items() if k!='reuse_preprocessing'}
    if old['config']!=config:raise ValueError('Preprocessing configuration differs')
    for source in old['sources']:
        expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode') if k in source}
        if snapshot(source['path'])!=expected:raise ValueError('Original source changed')
    current=source_code()
    for name,text in old['source_code'].items():
        if name in {'notebook_io.py','run_notebook_pipeline.py','check_image_selection.py'}:continue
        if current.get(name)!=text:raise ValueError('Business code changed: '+name)
    names=['read_legacy_concepts','input_legacy_concepts','read_qid_concepts','input_qid_concepts',
           'read_collected_documents','input_collected_documents','read_collected_images','input_collected_images',
           'read_wiki_pages','input_wiki_pages','concepts','images','page_refs','documents','concepts_selected',
           'missing_concepts','documents_links','images_links','documents_selected','images_selected',
           'documents_unmatched','images_unmatched','documents_processed','images_processed']
    manifest=[]
    for name in names:
        src=parent/'datasets'/f'{name}.jsonl';meta=src.with_suffix('.jsonl.meta.json')
        if not src.exists():continue
        if not meta.exists() or json.loads(meta.read_text())['version']!=digest(old):raise ValueError('Invalid parent checkpoint: '+name)
        with src.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        manifest.append({'name':name,'source':str(src),'sha256':sha,'size':src.stat().st_size})
    immutable(run/'preprocessing_reuse.json',{'parent':str(parent),'parent_version':digest(old),'new_version':version,
        'scope':'Completed preprocessing only; no model calls or partial files; runtime feeder Context fix', 'files':manifest})
    for item in manifest:
        dst=run/'datasets'/(item['name']+'.jsonl');dst.parent.mkdir(parents=True,exist_ok=True)
        immutable(dst.with_suffix('.jsonl.meta.json'),{'version':version})
        if not dst.exists():os.link(item['source'],dst)


def frozen_material_inputs(parent):
    """Explicitly adopt completed, previously selected materials as new run inputs."""
    import hashlib
    parent=Path(parent).resolve()
    manifest=json.loads((parent/'dataset_manifest.json').read_text())
    parent_version=digest(manifest)
    files=[]
    for name in ['related_materials','routing_materials','material_text_embeddings','material_image_embeddings']:
        path=parent/'datasets'/f'{name}.jsonl'
        meta=path.with_suffix('.jsonl.meta.json')
        if not path.exists() or not meta.exists() or json.loads(meta.read_text()).get('version')!=parent_version:
            raise ValueError('Incomplete or mismatched parent checkpoint: '+name)
        with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        files.append({'name':name,'path':str(path),'sha256':sha,'size':path.stat().st_size})
    return {'parent':str(parent),'parent_version':parent_version,'files':files,
            'scope':'Frozen cleaned, identity/related-material selections and embeddings; no old extraction or verification reused'}


def import_frozen_materials(record,run,version):
    import hashlib,shutil
    run=Path(run);immutable(run/'material_reuse.json',record)
    for item in record['files']:
        source=Path(item['path']);target=run/'datasets'/source.name
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            with target.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
            if actual!=item['sha256']:raise ValueError('Imported checkpoint changed')
        else:
            partial=target.with_suffix('.importing')
            shutil.copyfile(source,partial)
            with partial.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
            if actual!=item['sha256']:raise ValueError('Parent changed during import')
            partial.replace(target)
        immutable(target.with_suffix('.jsonl.meta.json'),{'version':version})
