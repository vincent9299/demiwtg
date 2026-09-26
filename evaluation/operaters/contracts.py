"""Frozen inputs only; Dataset orchestration lives in evaluation/evaluation_pipeline.py."""
from preparation.operaters.runfiles import run_state, run_manifest
from pathlib import Path

from project import PROJECT_ROOT as ROOT
from preparation.operaters.runfiles import RunFiles
from demiflow.execution.artifacts import digest, file_record, immutable, read


def default_config():
    return {'seed': 20260919, 'scope': 'all_complete_development_drafts',
            'backends': {'t2i': ['qwen2512'], 'edit': ['qwen2511']},
            'rubric_policy': 'author_exact_v1',
            'text_limit': 3, 'image_limit': 2,
            'qwen2512_reference': 'text_only', 'other_reference': 'text_and_images',
            'gemini_conditions': ['without_knowledge'],
            'judge': {'mode': 'offline', 'model': 'gpt-6-astra', 'reasoning_effort': 'medium',
                      'base_url': 'http://127.0.0.1:4001/v1',
                      'api_key_env': 'CURATION_JUDGE_API_KEY', 'timeout_s': 300,
                      'max_output_tokens': 16000, 'max_calls': 200,
                      'max_context_chars': 180000, 'max_images': 20},
            'endpoint': 'http://127.0.0.1:4001/v1/chat/completions',
            'python': {'bagel': str(ROOT.parent / 'env-bagel/bin/python'),
                       'qwen2511': str(ROOT.parent / 'env/bin/python'),
                       'qwen2512': str(ROOT.parent / 'env/bin/python'),
                       'gemini': str(ROOT.parent / 'env/bin/python')},
            # Must be assigned to an available device before actual generation.
            # No service shutdown, GPU borrowing, or preannotation restoration.
            'cuda': {'bagel': None, 'qwen2511': None, 'qwen2512': None},
            'split_registry': {'schema': 'v4-split-registry/1',
                               'scope': 'development_only', 'formal_test': {}}}


def implementation():
    import demiflow
    own = list(Path(__file__).parent.glob('*.py'))
    own.append(Path(__file__).resolve().parents[2] / 'evaluation/evaluation_pipeline.py')
    own += list((Path(__file__).parent.parent / 'prompts').glob('*.md'))
    own += list((Path(__file__).parent.parent / 'prompts').glob('*.yaml'))
    own.append(Path(__file__).resolve().parents[2] / 'preparation/prompts/responses.py')
    shared = ['preparation/preparation_pipeline.py', 'preparation/operaters/runfiles.py', 'preparation/operaters/inputs.py',
              'evaluation/operaters/retrieval.py',
              'evaluation/operaters/requests.py', 'evaluation/operaters/models.py', 'evaluation/bagel/adapter.py']
    own += [Path(__file__).resolve().parents[2] / p for p in shared]
    platform = Path(demiflow.__file__).parent
    return {'source': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(own)},
            'demiflow': digest({str(p.relative_to(platform)): digest(p.read_bytes())
                               for p in sorted(platform.rglob('*.py'))})}


class EvaluationFiles(RunFiles):
    def __init__(self, run, questions, knowledge_runs, config, graph, visual_runs=()):
        policy = default_config()
        for key in ('scope', 'qwen2512_reference', 'other_reference', 'gemini_conditions'):
            if config[key] != policy[key]:
                raise ValueError('Unsupported evaluation reference/scope policy: ' + key)
        for kind, allowed in [('t2i', {'qwen2512', 'bagel', 'gemini'}),
                              ('edit', {'qwen2511', 'bagel', 'gemini'})]:
            selected = config.get('backends', policy['backends'])[kind]
            if not selected or len(set(selected)) != len(selected) or set(selected) - allowed:
                raise ValueError('Invalid backend selection for ' + kind)
        self.run = Path(run).resolve()
        from preparation.operaters.runfiles import check_run_location
        from project import resolve_root
        check_run_location(self.run, ROOT, resolve_root())
        if config['judge']['mode'] not in {'offline', 'http'}:
            raise ValueError('Judge mode must be offline or http')
        self.branch, self.config = 'evaluation', config
        from demiflow.lance.refs import DatasetRef
        from project import resolve_root
        from preparation.operaters.inputs import freeze_material_source
        # 当前入口直接保存 uri/version；历史 DatasetRef 继续按原格式读取。
        if 'uri' in questions:
            self.questions = freeze_material_source(questions)
        else:
            ref = DatasetRef.from_dict(questions)
            ref.open(resolve_root())
            self.questions = ref.to_dict()
        self.knowledge_files = [freeze_material_source(spec) for spec in knowledge_runs]
        self.visual_files = [freeze_material_source(spec) for spec in visual_runs]
        self.manifest = {'schema': 'v2-development-evaluation/3', 'pipeline_version': 'V2',
                         'questions': self.questions, 'knowledge': self.knowledge_files, 'visual': self.visual_files,
                         'config': config, 'graph': graph, 'implementation': implementation()}
        self.initialize_business()
        self.records.put('split_registry', config['split_registry'])
        self.previous = self.version = digest(self.manifest)
        self.stages, self.reused, self.new = {}, [], []


def verify_run(run):
    run = Path(run)
    manifest = run_manifest(run)
    if manifest['implementation'] != implementation() or manifest['graph'] != graph_version():
        raise ValueError('Implementation changed; use a new evaluation run')
    from demiflow.lance.refs import DatasetRef
    from project import resolve_root
    for entry in [manifest['questions'], *manifest['knowledge'], *manifest['visual']]:
        if 'uri' in entry:
            import lance
            lance.dataset(str(resolve_root() / entry['uri']), version=entry['version'])
        else:
            DatasetRef.from_dict(entry['dataset_ref'] if 'dataset_ref' in entry else entry).open(resolve_root())
    return manifest


def frozen_stage(run, name):
    stage = run_state(run)['stages'][name]
    from demiflow.lance.refs import DatasetRef
    from project import resolve_root
    ref = DatasetRef.from_dict(stage['dataset_ref'])
    ref.open(resolve_root())
    return stage


def graph_version():
    return digest((Path(__file__).resolve().parents[1] / "evaluation_pipeline.py").read_bytes())

from demiflow.execution.artifacts import file_record
from preparation.operaters.runfiles import run_records


class PartitionFiles(RunFiles):
    def __init__(self, parent, backend, source, graph):
        self.run = Path(parent) / 'partition_evaluations' / backend
        self.branch = 'completed_partition_judge'
        self.manifest = {
            'schema': 'v4-completed-partition-judge/1', 'backend': backend,
            'parent': run_records(parent).reference('manifest').to_dict(),
            'source': source, 'graph_source': graph,
            'entrypoint': file_record(Path(__file__).resolve().parents[1] / 'evaluation_pipeline.py'),
            'scope': 'Partial results only; not a completed multi-model evaluation',
        }
        self.initialize_business()
        self.previous = self.version = digest(self.manifest)
        self.stages, self.reused, self.new = {}, [], []
