"""T2I Lance run bindings, input freezing and sample publication."""
from pathlib import Path
import os

from demiflow.execution.artifacts import digest
from preparation.articles.operators.run_state import MaterialRunState
from types import SimpleNamespace
from demiflow.lance.refs import DatasetRef
from preparation.articles.operators import runfiles as storage
from preparation.articles.operators.inputs import freeze_material_source, iter_material_rows, resolve_source, SplitGuard
from project import resolve_root

def source_snapshot():
    """Freeze this graph's business dependencies and platform, not other pipelines."""
    import demiflow
    root = Path(__file__).resolve().parents[3]
    files = {}
    excluded = {'tests', 'reviews', 'runs', 'datasets', 'archive', '__pycache__', '.git'}
    for relative in ('curation/t2i_training_samples', 'preparation'):
        # 先剪枝，避免把退役说明冻结为活动依赖或遍历生产对象库。
        for directory, dirs, names in os.walk(root / relative):
            dirs[:] = sorted(name for name in dirs if name not in excluded)
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix in {'.py', '.yaml', '.md', '.json'}:
                    files[str(path.relative_to(root))] = path.read_text()
    for name in ('assets.py', 'material_schema.py', 'schemas.py', 'concepts.py', 'materials.py', 'material_writer.py'):
        path = root / 'collect' / name
        files[str(path.relative_to(root))] = path.read_text()
    files['project.py'] = (root / 'project.py').read_text()
    platform = Path(demiflow.__file__).parent
    return {'business': files, 'demiflow_sha256': digest({str(p.relative_to(platform)): digest(p.read_bytes())
             for p in sorted(platform.rglob('*.py'))})}


class TrainingRun:
    def __init__(self, run, article_sources, visual_sources, cfg):
        self.run = Path(run).resolve()
        storage.check_run_location(self.run, storage.ROOT, resolve_root())
        relative = storage.run_relative(self.run)
        if not relative.startswith('demiwtg/curation/t2i_training_samples/datasets/'):
            raise ValueError('Use curation/t2i_training_samples/datasets/<run_id>')
        self.branch, self.config = 'training_t2i', cfg
        def frozen(sources):
            unique = {digest(s): s for s in map(freeze_material_source, sources)}
            return [unique[k] for k in sorted(unique)]
        self.article_sources, self.visual_sources = frozen(article_sources), frozen(visual_sources)
        if not self.article_sources and not self.visual_sources:
            raise ValueError('At least one explicit material source is required')
        for source in self.article_sources + self.visual_sources:
            iter_material_rows(source)  # validate fixed source/version before writing
        self.knowledge_version = digest([self.article_sources, self.visual_sources])
        self.guard = SplitGuard(cfg['split_registry'] or {
            'schema': 'v4-split-registry/1', 'scope': 'development_only',
            'formal_test': {'concepts': [], 'rule_families': [], 'images': []}})
        manifest = {'schema': 't2i-training-run/1', 'pipeline_version': 'V2', 'branch': self.branch,
            'article_sources': self.article_sources, 'visual_sources': self.visual_sources,
            'config': cfg, 'split_registry': self.guard.registry,
            'graph': digest((Path(__file__).resolve().parents[1] / 't2i_training_samples_pipeline.py').read_bytes()), 'implementation': source_snapshot()}
        self.storage_root, self.relative, self.manifest = resolve_root(), relative, manifest
        self.schema_name, self.schema_version = 't2i_training_audit', 'v1'
        self.records = storage.run_records(self.run)
        self.records.save_manifest(manifest)
        self.previous = self.version = digest(manifest)
        self.stages, self.reused, self.new = {}, [], []

    def attempt_files(self, attempt):
        relative = self.relative + '__' + attempt['task_id']
        manifest = {'parent': self.version, 'attempt_sha256': digest(attempt)}
        records = MaterialRunState(self.storage_root, relative)
        records.legacy_uri = str(Path(relative).parent / ('attempt_metadata__' + Path(relative).name + '.lance'))
        records.save_manifest(manifest)
        return SimpleNamespace(storage_root=self.storage_root, relative=relative, manifest=manifest,
            records=records, previous=digest(manifest), schema_name=self.schema_name,
            schema_version=self.schema_version, stages={}, new=[], reused=[])

    def finish(self):
        state = {'branch': self.branch, 'stages': self.stages,
                 'reused_stages': self.reused, 'new_stages': self.new}
        self.records.finish(state)
        return state
