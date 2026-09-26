"""T2I Lance run bindings, input freezing and sample publication."""
from pathlib import Path

from demiflow.execution.artifacts import digest
from demiflow.lance.records import LanceRecordStore
from types import SimpleNamespace
from demiflow.lance.refs import DatasetRef
from preparation.operaters import runfiles as storage
from preparation.operaters.inputs import freeze_material_source, iter_material_rows, resolve_source, SplitGuard
from project import resolve_root

def source_snapshot():
    """Freeze this graph's business dependencies and platform, not other pipelines."""
    import demiflow
    root = Path(__file__).resolve().parents[3]
    files = {}
    for relative in ('curation/t2i', 'preparation'):
        for path in sorted((root / relative).rglob('*')):
            if (path.is_file() and path.suffix in {'.py', '.yaml', '.md', '.json'}
                    and not {'tests', 'reviews', 'runs', 'datasets', '__pycache__'} & set(path.relative_to(root).parts)):
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
        if not relative.startswith('demiwtg/curation/t2i/datasets/'):
            raise ValueError('Use curation/t2i/datasets/<run_id>')
        self.branch, self.config = 'training_t2i', cfg
        def frozen(sources):
            unique = {digest(s): s for s in map(freeze_material_source, sources)}
            return [unique[k] for k in sorted(unique)]
        self.article_sources, self.visual_sources = frozen(article_sources), frozen(visual_sources)
        if not self.article_sources and not self.visual_sources:
            raise ValueError('At least one explicit material source is required')
        for source in self.article_sources + self.visual_sources:
            iter_material_rows(source)  # validate fixed source/version before writing
        prior = storage.run_records(run).get('manifest')
        self.raw_ref = (prior['raw_images_ref'] if prior else
                        resolve_source(resolve_root(), 'legacy_images')[0].to_dict())
        DatasetRef.from_dict(self.raw_ref).open(resolve_root())
        self.knowledge_version = digest([self.article_sources, self.visual_sources])
        self.guard = SplitGuard(cfg['split_registry'] or {
            'schema': 'v4-split-registry/1', 'scope': 'development_only',
            'formal_test': {'concepts': [], 'rule_families': [], 'images': []}})
        manifest = {'schema': 't2i-training-run/1', 'pipeline_version': 'V2', 'branch': self.branch,
            'article_sources': self.article_sources, 'visual_sources': self.visual_sources,
            'raw_images_ref': self.raw_ref, 'config': cfg, 'split_registry': self.guard.registry,
            'graph': digest((Path(__file__).resolve().parents[1] / 't2i_train_pipeline.py').read_bytes()), 'implementation': source_snapshot()}
        self.storage_root, self.relative, self.manifest = resolve_root(), relative, manifest
        self.schema_name, self.schema_version = 't2i_training_audit', 'v1'
        self.records = storage.run_records(self.run)
        self.records.put('manifest', manifest)
        self.previous = self.version = digest(manifest)
        self.stages, self.reused, self.new = {}, [], []

    def attempt_files(self, attempt):
        relative = self.relative + '__' + attempt['task_id']
        manifest = {'parent': self.version, 'attempt_sha256': digest(attempt)}
        records = LanceRecordStore(self.storage_root, str(Path(relative).parent / ('attempt_metadata__' + Path(relative).name + '.lance')))
        records.put('manifest', manifest)
        return SimpleNamespace(storage_root=self.storage_root, relative=relative, manifest=manifest,
            records=records, previous=digest(manifest), schema_name=self.schema_name,
            schema_version=self.schema_version, stages={}, new=[], reused=[])

    def finish(self):
        state = {'branch': self.branch, 'stages': self.stages,
                 'reused_stages': self.reused, 'new_stages': self.new}
        self.records.put('latest', state, immutable=False)
        return state
