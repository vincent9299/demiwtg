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


def frozen_material_inputs(parent, ids, extraction_config=None):
    """Explicitly adopt completed, previously selected materials as new run inputs."""
    import hashlib
    parent=Path(parent).resolve()
    manifest=json.loads((parent/'dataset_manifest.json').read_text())
    if set(manifest['config'].get('ids') or []) != set(ids or []):
        raise ValueError('Selected-material checkpoint concept scope differs')
    parent_version=digest(manifest)
    names=['related_materials','routing_materials','material_text_embeddings','material_image_embeddings']
    if manifest['source_code'].get('ops/material_routing.py') != source_code().get('ops/material_routing.py'):
        # Rebuild routing metadata, including identity boundaries. The selected
        # passages/pixels are unchanged, so their complete embeddings are reusable.
        names.remove('routing_materials')
    if extraction_config is not None:
        import yaml
        from .ops.prompt_config import knowledge_prompt_pack
        saved=json.loads((parent/'joint_extraction/knowledge/prompt_config.json').read_text())
        old=yaml.safe_load(saved['yaml'])['prompts']['joint_paragraphs']
        _,text=knowledge_prompt_pack(extraction_config)
        new=yaml.safe_load(text)['prompts']['joint_paragraphs']
        if old!=new:raise ValueError('Joint extraction prompt/model changed; cannot reuse drafts')
        if manifest['source_code']['ops/article.py']!=source_code()['ops/article.py']:
            validate_frozen_article_rows(parent/'datasets/paragraph_extract.jsonl', extraction_config.get('image_identity_definitions',{}))
        previous=manifest['config']['model_config']
        for key,default in [('joint_thinking',True),('joint_reasoning_effort','low'),('temperature',0),
                            ('max_output_tokens',16384),('joint_input_tokens',32768),('joint_image_target',None),('image_identity_definitions',{})]:
            if previous.get(key,default)!=extraction_config.get(key,default):raise ValueError('Joint extraction configuration changed: '+key)
        names=['related_materials','joint_requests','paragraph_extract']
    files=[]
    for name in names:
        path=parent/'datasets'/f'{name}.jsonl'
        if extraction_config is None and name != 'related_materials' and not path.exists():continue
        meta=path.with_suffix('.jsonl.meta.json')
        if not path.exists() or not meta.exists() or json.loads(meta.read_text()).get('version')!=parent_version:
            raise ValueError('Incomplete or mismatched parent checkpoint: '+name)
        with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        files.append({'name':name,'path':str(path),'sha256':sha,'size':path.stat().st_size})
    return {'parent':str(parent),'parent_version':parent_version,'files':files,
            'scope':('Frozen completed joint extraction and selected materials; final review reruns' if extraction_config is not None else
                     'Frozen cleaned, identity/related-material selections and embeddings; no old extraction or verification reused')}


def validate_frozen_article_rows(path, definitions):
    """An operator change may reuse drafts only if their exact inputs and parsing agree."""
    from .ops.article import PrepareArticleInput, ApplyArticle
    keys=['concept','identity_scope','source_catalog','article_image_ids','pixel_images','article_input',
          'article_topics','article_status','validation_issues','review_required_issues','empty_reason']
    with Path(path).open() as stream:
        for line in stream:
            old=json.loads(line)
            current=ApplyArticle()(PrepareArticleInput(definitions)(old))
            if any(digest(old.get(k))!=digest(current.get(k)) for k in keys):
                raise ValueError('Article operators changed frozen draft inputs or interpretation; cannot reuse: '+old['batch_id'])


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


def frozen_filter_inputs(parent, ids, config=None):
    """Reuse frozen cleaning/identity; always rerun text and both image selections."""
    import hashlib
    parent=Path(parent).resolve()
    manifest=json.loads((parent/'dataset_manifest.json').read_text())
    if set(manifest['config'].get('ids') or []) != set(ids or []):
        raise ValueError('Filter checkpoint concept scope differs')
    current=source_code()
    for name in ['ops/knowledge_stages.py','ops/prompts/identity.yaml','ops/filter_document_blocks.py','ops/dataset_operators.py']:
        if manifest['source_code'].get(name)!=current.get(name):raise ValueError('Upstream dependency changed: '+name)
    names=['knowledge_identity']
    blocks=parent/'datasets/multimodal_materials.jsonl'
    if blocks.exists():
        if manifest['source_code']['ops/source_blocks.py']!=current['ops/source_blocks.py']:
            raise ValueError('Source block operator changed; reuse identity before building blocks')
        names.append('multimodal_materials')
    if config and config.get('reuse_text_selection'):
        if not blocks.exists():raise ValueError('Text selection reuse requires its completed source blocks')
        from .image_filter_runtime import validate_text_selection_reuse
        validate_text_selection_reuse(parent,config)
        if manifest['source_code']['ops/source_blocks.py']!=current['ops/source_blocks.py']:
            raise ValueError('Text selection operator changed')
        if manifest['config']['model_config'].get('image_identity_definitions')!=config.get('image_identity_definitions'):
            raise ValueError('Text selection identity scope changed')
        names.append('text_relevance')
    files=[];parent_version=digest(manifest)
    for name in names:
        path=parent/'datasets'/f'{name}.jsonl';meta=path.with_suffix('.jsonl.meta.json')
        if not path.exists() or not meta.exists() or json.loads(meta.read_text()).get('version')!=parent_version:
            raise ValueError('Invalid completed filter input: '+name)
        with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        files.append({'name':name,'path':str(path),'sha256':sha,'size':path.stat().st_size})
    return {'parent':str(parent),'parent_version':parent_version,'files':files,
            'scope':'Frozen cleaning, identity'+(' and unchanged text selection' if 'text_relevance' in names else '')+'; image selection and all article stages rerun.'}
