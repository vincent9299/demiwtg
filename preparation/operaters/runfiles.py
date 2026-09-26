"""Business run identities, fixed Lance references and preparation provenance."""
import json
import os
from project import resolve_root
from pathlib import Path


from project import PROJECT_ROOT as ROOT
PIPELINE_DIRS = ('preparation', 'evaluation', 'benchmark/t2i/v2', 'benchmark/edit/v2',
                 'benchmark/t2i/v1', 'benchmark/edit/v1', 'evaluation/t2i/v1',
                 'evaluation/edit/v1', 'curation/t2i', 'curation/edit')
# V1 赛道目录同时保存历史脚本与历史批次；源码快照只登记新活动实现
# （带前缀的 pipeline/operaters/prompts），不递归纳入历史数据与冻结证据。
V1_ACTIVE_ROOTS = {'benchmark/t2i/v1', 'benchmark/edit/v1',
                   'evaluation/t2i/v1', 'evaluation/edit/v1'}
V1_ACTIVE_SUBDIRS = ('operaters', 'prompts')
SCHEMA='curation-v4-materials/1'

from demiflow.execution.artifacts import encoded, digest, immutable, run_lock, snapshot


def pipeline_source_files():
    """Current pipeline sources, excluding legacy evaluators and vendored suites."""
    excluded = {'__pycache__', 'tests', 'validation', 'reviews', 'runs', 'datasets', 'runtime', '.git'}
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
    root = Path(__file__).resolve().parents[2]
    files = {str(p.relative_to(root)):p.read_text() for p in pipeline_source_files()
            if p.suffix in {'.py', '.yaml', '.txt'} and not p.name.startswith('test_') and not {'__pycache__','tests','reviews'} & set(p.parts)}
    for relative in ("project.py", "collect/assets.py", "collect/schemas.py",
                     "collect/material_schema.py", "collect/materials.py", "collect/material_writer.py",
                     "collect/concepts.py", "preparation/prompts/image_annotation_v2.json"):
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
            if not name.startswith(('benchmark/', 'curation/t2i/',
                                    'curation/edit/', 'evaluation/'))}


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
    run_records(run).put('manifest', manifest)
    if plan_source is not None:run_records(run).put('pipeline_source', {'source':plan_source})
    return digest(manifest)


def _observed_bytes_valid(record) -> bool:
    """Revalidate content identity in Lance; old file paths are provenance only."""
    from preparation.operaters.images import bytes_unchanged
    expected = record.get('actual_sha256')
    return bool(expected) and bytes_unchanged(record.get('path'), expected)

def freeze_visual_run(run, input_path, dataset, config, project=ROOT):
    # Freeze the Python visual pipeline, independently of the debug notebook.
    graph_hash = digest((Path(__file__).resolve().parents[1] / 'preparation_pipeline.py').read_bytes())
    check_run_location(run, project, dataset)
    from demiflow.lance.refs import DatasetRef
    ref = DatasetRef.from_dict(input_path)
    if ref.schema_name not in {'raw_images','articles','pipeline_stage_rows'}:
        raise ValueError('Visual input must be an image/article entity or concept/images stage reference')
    ref.open(dataset)
    source = ref.to_dict()
    records = run_records(run)
    for key, record in records.items().items():
        if key.startswith('observed_asset/') and not _observed_bytes_valid(record):
            raise ValueError('Previously inspected visual bytes changed: ' + key)
    # Pin the byte/source table before review; saved results reuse this exact ref.
    from preparation.operaters.inputs import resolve_source
    prior = records.get('manifest')
    saved_raw = (prior or {}).get('config',{}).get('raw_images_ref')
    raw_ref = (DatasetRef.from_dict(saved_raw) if saved_raw else
        ref if ref.schema_name == 'raw_images' else resolve_source(dataset, 'legacy_images')[0])
    raw_ref.open(dataset)
    config = {**config, 'raw_images_ref': raw_ref.to_dict()}
    manifest = {'pipeline_version':'V2', 'branch':'preparation.visual_materials',
                'source':source, 'store_id':'local', 'config':config,
                'graph_sha256':graph_hash, 'code':preparation_source_code(),
                'runtime':runtime_version()}
    records.put('manifest', manifest)
    return digest(manifest), source

def freeze_visual_replay(run, parent, project=ROOT):
    # Freeze the Python visual pipeline, independently of the debug notebook.
    graph_hash = digest((Path(__file__).resolve().parents[1] / 'preparation_pipeline.py').read_bytes())
    original = run_manifest(parent)
    check_run_location(run, project, resolve_root())
    if Path(run).resolve() == Path(parent).resolve(): raise ValueError('Replay must use a new run')
    # Explicitly verify model-visible prompt identity before parser-only replay.
    from preparation.prompts import material_prompt_pack
    import yaml
    for role, cfg in [('image_primary', original['config']), ('image_review', {
        **original['config'], 'model':original['config']['image_review_model'],
        'base_url':original['config']['image_review_base_url'], 'local_model_comparison':True})]:
        saved = run_records(Path(parent)/role).get('prompt_config')
        _, active = material_prompt_pack(cfg)
        if yaml.safe_load(saved['yaml'])['prompts']['select_images'] != yaml.safe_load(active)['prompts']['select_images']:
            raise ValueError('Replay prompt/model differs')
    refs = {name:stage_ref(parent,name).to_dict() for name in
            ('visual_inputs','image_primary','image_review_responses')}
    for key, record in run_records(parent).items().items():
        if key.startswith('observed_asset/') and not _observed_bytes_valid(record):
            raise ValueError('Recorded visual pixels changed before replay')
    manifest = {'pipeline_version':'V2', 'branch':'preparation.visual_response_revalidation',
                'parent':run_records(parent).reference('manifest').to_dict(), 'inputs':refs,
                'config':original['config'], 'code':preparation_source_code(),
                'graph_sha256':graph_hash, 'new_model_calls':0}
    run_records(run).put('manifest',manifest)
    return digest(manifest)


import re

def rows(path):
    from preparation.operaters.results import from_stage_row
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
                      ROOT / "preparation/preparation_pipeline.py", ROOT / "preparation/operaters/images.py", ROOT / "preparation/operaters/results.py", ROOT / "preparation/prompts/image_annotation_v2.json", ROOT / "preparation/operaters/article.py"]
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
    from demiflow.lance.records import LanceRecordStore
    from project import resolve_root
    relative = Path(run_relative(run))
    return LanceRecordStore(resolve_root(), str(relative.parent / ('metadata__' + relative.name + '.lance')))


def run_state(run):
    state = run_records(run).get('latest')
    if state is None: raise ValueError('Run has no committed stages')
    return state


def run_manifest(run):
    manifest = run_records(run).get('manifest')
    if manifest is None: raise ValueError('Run manifest missing')
    return manifest


class RunFiles:
    """Business input/config identity and stage references; no Dataset execution."""
    def initialize_business(self):
        self.storage_root, self.relative = resolve_root(), run_relative(self.run)
        self.schema_name, self.schema_version = 'pipeline_stage_rows', 'v1'
        self.records = run_records(self.run)
        self.records.put('manifest', self.manifest)
        self.previous = self.version = digest(self.manifest)
        self.stages, self.reused, self.new = {}, [], []

    def finish(self):
        state = {'branch': self.branch, 'stages': self.stages,
                 'reused_stages': self.reused, 'new_stages': self.new}
        self.records.put('latest', state, immutable=False)
        return state

# ---------------------------------------------------------------- Lance 阶段


def read_stage_ref(run, name):
    """latest.json 里的阶段 DatasetRef（固定版本；不存在返回 None）。"""
    state = run_state(run)
    record = state["stages"].get(name) or {}
    ref = record.get("dataset_ref")
    return ref if isinstance(ref, dict) and ref.get("relative_uri") else None


def open_stage_dataset(run, name):
    """按 DatasetRef 固定版本打开阶段表（demiflow Dataset）。"""
    ref = read_stage_ref(run, name)
    if ref is None:
        raise ValueError("Stage has no dataset_ref: " + str(name))
    from project import resolve_root

    return data.read_lance(str(resolve_root() / ref["relative_uri"]),
                               version=ref["lance_version"])


def saved_stage(run, name):
    from preparation.operaters.results import from_stage_row
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
    return {'root': str(resolve_root()), 'relative_uri': str(relative.parent / ('model_' + purpose + '__' + relative.name + '.lance'))}


def read_record(ref):
    from demiflow.lance.records import RecordRef
    from project import resolve_root
    return RecordRef.from_dict(ref).read(resolve_root())


def response_records(run, stage, task_prefix=None):
    from demiflow.lance.records import LanceRecordStore
    store = LanceRecordStore(**prompt_store(run))
    result = []
    for key, request in sorted(run_records(run).items(prefix='request/' + stage + '/').items()):
        if not key.startswith('request/' + stage + '/'): continue
        if task_prefix is not None and not request['task_id'].startswith(task_prefix): continue
        binding = request.get('native_offline')
        if not binding: continue
        response_key = 'response/' + read_record(binding['request_ref'])['request_sha256']
        response = store.get(response_key)
        if response is not None:
            result.append({'record_ref': store.reference(response_key).to_dict(), 'sha256': digest(response)})
    return result


def store_blob(run, data):
    from demiflow.lance.blobs import LanceBlobStore
    from project import resolve_root
    relative = Path(run_relative(run))
    return LanceBlobStore(resolve_root(), str(relative.parent / ('assets__' + relative.name + '.lance'))).put(data)


from demiflow.lance.refs import DatasetRef
from demiflow.lance.storage import schema_hash


def stage_uri(run, name):
    # The run name is the project's stable execution identity, independent of the lake root.
    relative = Path(run_relative(run))
    return str(resolve_root() / relative.parent / (identifier(name) + '__' + relative.name + '.lance'))


def stage_ref(run, name):
    from preparation.operaters.results import PIPELINE_STAGE_ROWS, SCHEMA_VERSION
    import lance
    entry = run_records(run).get('stage/' + name)
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
    from preparation.operaters.results import from_stage_row
    ref = stage_ref(run, name)
    return data.read_lance(ref.resolve(resolve_root()), version=ref.lance_version).map(from_stage_row)
