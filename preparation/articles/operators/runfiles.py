"""文章材料运行契约：阶段固定引用、源码清单及来源绑定。

图片审核与下游复用已有材料阶段格式和 run 命名契约；不在这里编排它们的阶段。
"""
import json
import os
from project import resolve_root
from pathlib import Path


from project import PROJECT_ROOT as ROOT
PIPELINE_DIRS = ('preparation', 'collect/wiki_documents', 'evaluation', 'benchmark/t2i/v2', 'benchmark/edit/v2',
                 'benchmark/t2i/v1', 'benchmark/edit/v1', 'evaluation/t2i/v1',
                 'evaluation/edit/v1', 'curation/t2i_training_samples', 'curation/edit_training_pairs',
                 'curation/concept_image_tasks', 'curation/t2i_positive_pairs', 'curation/image_facts')
# V1 赛道目录同时保存历史脚本与历史批次；源码快照只登记新活动实现
# （带前缀的 pipeline/operators/prompts），不递归纳入历史数据与冻结证据。
V1_ACTIVE_ROOTS = {'benchmark/t2i/v1', 'benchmark/edit/v1',
                   'evaluation/t2i/v1', 'evaluation/edit/v1'}
V1_ACTIVE_SUBDIRS = ('operators', 'prompts')
SCHEMA='curation-v4-materials/1'

from demiflow.execution.artifacts import encoded, digest, immutable, run_lock, snapshot


def pipeline_source_files():
    """Current pipeline sources, excluding legacy evaluators and vendored suites."""
    excluded = {'__pycache__', 'tests', 'validation', 'reviews', 'runs', 'datasets', 'runtime', '.git', 'archive'}
    for branch in PIPELINE_DIRS:
        relative = (ROOT / branch).relative_to(ROOT).as_posix()
        if relative in V1_ACTIVE_ROOTS:
            # 入口文件已有赛道、版本前缀，只纳入现役标准入口。
            for path in [ROOT / branch / '__init__.py', *sorted((ROOT / branch).glob('*_pipeline.py'))]:
                if path.is_file():
                    yield path
            for sub in V1_ACTIVE_SUBDIRS:
                for path in sorted((ROOT / branch / sub).rglob('*')):
                    if path.is_file() and not {'__pycache__', 'tests'} & set(path.parts):
                        yield path
            continue
        for directory, dirs, names in os.walk(ROOT / branch):
            branch_relative = Path(directory).relative_to(ROOT).as_posix()
            skip = excluded
            if branch_relative == 'evaluation':
                skip = excluded | {'t2i', 'edit'}
            elif branch_relative == 'evaluation/bagel':
                skip = excluded | {'gen', 'vlm', 'data'}
            dirs[:] = sorted(d for d in dirs if d not in skip)
            for name in sorted(names):
                yield Path(directory) / name


def source_code():
    root = ROOT
    files = {str(p.relative_to(root)):p.read_text() for p in pipeline_source_files()
            if p.suffix in {'.py', '.yaml', '.txt'} and not p.name.startswith('test_') and not {'__pycache__','tests','reviews'} & set(p.parts)}
    for relative in ("project.py", "collect/assets.py", "collect/schemas.py",
                     "collect/material_schema.py", "collect/materials.py", "collect/material_writer.py",
                     "collect/concepts.py"):
        files[relative] = (ROOT / relative).read_text()
    return files


def runtime_version():
    import importlib.metadata
    import demiflow
    directory=Path(demiflow.__file__).parent
    return {'trafilatura':importlib.metadata.version('trafilatura'), 'mwparserfromhell':importlib.metadata.version('mwparserfromhell'),
            'demiflow':importlib.metadata.version('demiflow'),
            'demiflow_python_sha256':digest({str(p.relative_to(directory)):digest(p.read_bytes()) for p in sorted(directory.rglob('*.py'))})}


DEFAULT={'base_url':'http://127.0.0.1:8000/v1','model':'qwen3.8-27b','max_calls':16,'max_output_tokens':3500,
         'timeout_s':240,'max_cases':4,'identity_docs':12,'identity_images':12,'max_docs':2,'max_chars_per_doc':6500,'max_input_chars':13000,
         'max_images':2,'max_image_bytes':8*1024*1024,'cos_base_url':None}


def check_run_location(run, project, dataset):
    run, project, dataset = Path(run).resolve(), Path(project).resolve(), Path(dataset).resolve()
    run_relative(run)
    if not run.is_relative_to(project):
        raise ValueError('run identity must belong to a project pipeline')


import inspect
from demiflow.execution.artifacts import code_record as _code_record, read

from demiflow import data


def preparation_source_code():
    """Freeze material preparation without coupling it to downstream work."""
    return {name: text for name, text in source_code().items()
            if not name.startswith(('benchmark/', 'curation/t2i_training_samples/',
                                    'curation/edit_training_pairs/', 'curation/concept_image_tasks/',
                                    'curation/t2i_positive_pairs/', 'curation/image_facts/', 'evaluation/'))}


def _source_unchanged(source):
    """Only a fixed Lance reference can be a material source."""
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
    manifest={'schema':'demiflow-explicit-pipeline/1','store_id':'local','sources':sources,
              'config':config,'source_code':preparation_source_code(),'runtime':runtime_version(),
              'pipeline_code':_code_record(pipeline.__code__)}
    run_records(run).save_manifest(manifest)
    if plan_source is not None:run_records(run).save_configuration('pipeline_source', {'source': plan_source})
    return digest(manifest)




import re

def rows(path):
    from preparation.articles.operators.results import from_stage_row
    if isinstance(path, dict):
        from demiflow.lance.refs import DatasetRef
        from project import resolve_root
        ref = DatasetRef.from_dict(path.get('dataset_ref', path))
        for batch in ref.open(resolve_root()).to_batches():
            for row in batch.to_pylist():
                yield from_stage_row(row) if ref.schema_name == 'pipeline_stage_rows' else row
        return
    raise TypeError('Pipeline data requires a fixed Lance DatasetRef; import source files explicitly')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Use a nonempty filesystem-safe task ID")
    return value


def code_version():
    import demiflow
    directory = ROOT
    files = [p for p in pipeline_source_files() if (p.suffix in {".py", ".yaml"} or p.suffix == ".md" and p.parent.name == "prompts")
             and not {"tests", "validation", "reviews", "__pycache__"} & set(p.relative_to(directory).parts)]
    business_files = [ROOT / "project.py", ROOT / "collect/assets.py",
                      ROOT / "collect/schemas.py", ROOT / "collect/material_schema.py", ROOT / "collect/materials.py", ROOT / "collect/material_writer.py",
                      ROOT / "collect/concepts.py",
                      ROOT / "preparation/images/catalog/operators/records.py", ROOT / "preparation/articles/operators/results.py", ROOT / "preparation/articles/operators/article.py"]
    return {"code": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(files) if p.is_file()},
            "business": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in business_files},
            "demiflow": digest({str(p.relative_to(Path(demiflow.__file__).parent)): digest(p.read_bytes())
                                for p in sorted(Path(demiflow.__file__).parent.rglob("*.py")) if p.is_file()})}


def run_relative(run):
    """Run identity prefix; tables live directly in the owning datasets folder."""
    parts = Path(run).resolve().parts
    for at in range(len(parts)):
        for branch in PIPELINE_DIRS:
            location = tuple(branch.split('/'))
            start = at + len(location) + 1
            if (parts[at:at + len(location)] == location
                    and at + len(location) < len(parts)
                    and parts[at + len(location)] in {'datasets', 'runs'}
                    and start < len(parts)):
                return 'demiwtg/' + branch + '/datasets/' + '__'.join(parts[start:])
    raise ValueError('Use <pipeline>/datasets/<run_id> as the run identity; no run directory is created')


def run_records(run):
    """Typed material-stage state; model/control logs are ordinary files or SQLite."""
    from preparation.articles.operators.run_state import MaterialRunState
    return MaterialRunState(resolve_root(), run_relative(run))


def run_state(run):
    state = run_records(run).load()
    if state is None: raise ValueError('Run has no committed stages')
    return state


def run_manifest(run):
    manifest = run_records(run).load_manifest()
    if manifest is None: raise ValueError('Run manifest missing')
    return manifest


class RunFiles:
    """Business input/config identity and stage references; no Dataset execution."""
    def initialize_business(self):
        self.storage_root, self.relative = resolve_root(), run_relative(self.run)
        self.schema_name, self.schema_version = 'pipeline_stage_rows', 'v1'
        self.records = run_records(self.run)
        self.records.save_manifest(self.manifest)
        self.previous = self.version = digest(self.manifest)
        self.stages, self.reused, self.new = {}, [], []

    def finish(self):
        state = {'branch': self.branch, 'stages': self.stages,
                 'reused_stages': self.reused, 'new_stages': self.new}
        self.records.finish(state)
        return state

# ---------------------------------------------------------------- Lance 阶段


def read_stage_ref(run, name):
    """latest.json 里的阶段 DatasetRef（固定版本；不存在返回 None）。"""
    state = run_state(run)
    record = state["stages"].get(name) or {}
    ref = record.get("dataset_ref")
    return ref if isinstance(ref, dict) and ref.get("relative_uri") else None


def saved_stage(run, name):
    from preparation.articles.operators.results import from_stage_row
    state = run_state(run)
    record = state["stages"][name]
    ref = record.get("dataset_ref")
    if isinstance(ref, dict) and ref.get("relative_uri"):
        # Lance 阶段：固定版本读取，payload 还原业务行
        rows_out = []
        from project import resolve_root
        import lance as _lance

        from demiflow.lance.refs import DatasetRef
        ds = DatasetRef.from_dict(ref).open(resolve_root())
        for stage_row in ds.to_table().to_pylist():
            rows_out.append(from_stage_row(stage_row))
        return rows_out
    raise ValueError('Stage has no fixed Lance reference: ' + name)


def prompt_store(run, purpose='offline'):
    from project import resolve_root
    relative = Path(run_relative(run))
    from demiflow.operator_llm.call_ref import journal_options
    return journal_options(resolve_root(), str(relative.parent / ('model_' + purpose + '__' + relative.name + '.lance')))


def read_record(ref):
    from demiflow.operator_llm.call_ref import read_call
    from demiflow.execution.file_ref import JsonArtifactRef
    if 'artifact_path' in ref:
        return JsonArtifactRef(**ref).read()
    if 'dataset_id' in ref:
        from demiflow.lance.refs import DatasetRef
        dataset_ref = DatasetRef.from_dict(ref)
        row = data.read_lance(dataset_ref.resolve(resolve_root()), version=dataset_ref.lance_version).take(1)[0]
        if dataset_ref.schema_name == 't2i_training_audit':
            return json.loads(row['payload'])
        if dataset_ref.schema_name == 'evaluation_rubric_packet':
            # Historical packets remain readable after retiring their producer.
            fields = ('question', 'rubric', 'rubric_policy', 'author_criteria', 'evidence',
                      'evidence_images', 'material_issues', 'input_binding', 'provenance')
            return {**{name: row[name] for name in ('schema', 'question_sha256')},
                    **{name: json.loads(row[name + '_json']) for name in fields}}
        return row
    return read_call(ref, resolve_root())


def response_records(run, stage, task_prefix=None):
    from demiflow.operator_llm.sqlite_journal import SQLitePromptJournal
    from demiflow.operator_llm.call_ref import PromptRecordRef
    store = SQLitePromptJournal(**prompt_store(run))
    try:
        result = []
        for request in run_records(run).request_bindings(stage, task_prefix):
            binding = request.get('native_offline')
            if not binding:
                continue
            key = read_record(binding['request_ref'])['request_sha256']
            response = store.read(key)
            if response is not None:
                result.append({'record_ref': PromptRecordRef(str(store.path), key, 'response').to_dict(), 'sha256': digest(response)})
        return result
    finally:
        store.close()


def store_blob(run, data):
    from demiflow.objects import LocalObjectStore
    from project import resolve_root
    return LocalObjectStore(resolve_root() / 'objects').put(data)


from demiflow.lance.refs import DatasetRef
from demiflow.lance.storage import schema_hash


def stage_uri(run, name):
    # The run name is the project's stable execution identity, independent of the lake root.
    relative = Path(run_relative(run))
    return str(resolve_root() / relative.parent / (identifier(name) + '__' + relative.name + '.lance'))


def stage_ref(run, name):
    from preparation.articles.operators.results import PIPELINE_STAGE_ROWS, SCHEMA_VERSION
    import lance
    entry = run_records(run).stage(name)
    if entry is not None:
        ref = DatasetRef.from_dict(entry['dataset_ref'])
        ref.open(resolve_root())
        return ref
    # Historical preparation stages were immutable one-commit tables.
    # Keep reading their original version without checkpoint sidecars.
    uri = stage_uri(run, name)
    if not Path(uri).exists():
        raise ValueError(f'Missing Lance stage: {name}')
    table = lance.dataset(uri, version=1)
    relative = str(Path(uri).relative_to(resolve_root()))
    ref = DatasetRef(relative[:-6], relative, table.version,
                      'pipeline_stage_rows', SCHEMA_VERSION, schema_hash(table.schema), table.count_rows())
    from demiflow.lance.registry import Catalog
    Catalog(resolve_root()).register(ref)
    return ref


def read_stage(run, name):
    from preparation.articles.operators.results import from_stage_row
    ref = stage_ref(run, name)
    return data.read_lance(ref.resolve(resolve_root()), version=ref.lance_version).map(from_stage_row)
