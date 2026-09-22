"""Lance input/version helpers for the notebook. No business stages or Dataset dispatch."""
import inspect
import json
import types
from pathlib import Path

from demiflow.standalone import local_data
from curation.preparation.contracts import ROOT, digest, immutable, source_code, runtime_version, snapshot
from curation.preparation.config import check_run_location


def preparation_source_code():
    """Freeze knowledge code without coupling it to independent downstream work."""
    return {name: text for name, text in source_code().items()
            if not name.startswith(('benchmark/', 'training/', 'evaluation/'))
            and name not in {'benchmark/pipeline.py', 'training/pipeline.py', 'evaluation/pipeline.py'}}


def read_saved(run, name):
    """Read one explicit Lance checkpoint as a Dataset; never executes a stage."""
    from curation.preparation.stages import read_stage
    return read_stage(run, name)


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


def _source_unchanged(source):
    """Only a fixed Lance reference can be a live knowledge source."""
    from demiflow.lance.refs import DatasetRef
    from project import resolve_root
    ref=DatasetRef.from_dict(source['dataset_ref'])
    ref.open(resolve_root())
    return True


def freeze_run(run, dataset, sources, config, pipeline, project=ROOT):
    """Freeze input/code/config before execution; return checkpoint version only."""
    run=Path(run).resolve();dataset=Path(dataset).resolve()
    check_run_location(run,Path(project).resolve(),dataset)
    if not 0<=config['sample_rate']<=1:raise ValueError('invalid sample rate')
    if config['group_size']<1 or (config['max_records_per_source'] is not None and config['max_records_per_source']<1):
        raise ValueError('invalid size')
    for source in sources:
        if not _source_unchanged(source):raise ValueError('Source changed; use a new run')
    try:plan_source=inspect.getsource(pipeline)
    except (OSError,TypeError):
        # Dynamically compiled functions still freeze executable bytecode below.
        plan_source=None
    manifest={'schema':'demiflow-explicit-notebook/1','store_id':'local','sources':sources,
              'config':config,'source_code':preparation_source_code(),'runtime':runtime_version(),
              'pipeline_code':_code_record(pipeline.__code__)}
    from curation.preparation.records import run_records
    run_records(run).put('manifest', manifest)
    if plan_source is not None:run_records(run).put('pipeline_source', {'source':plan_source})
    return digest(manifest)
