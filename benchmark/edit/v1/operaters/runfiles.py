"""Run identity and explicit input boundaries for V1 Edit authoring.

The historical authoring driver (plan → dispatch → model call → strict
validation) is not in the tree; only its protocol, plan inputs and frozen
question batches survived. This pipeline rebuilds that chain as framework
operators on the recorded contracts. focus200/ and the frozen batches stay
read-only evidence.
"""
import json
from pathlib import Path

from preparation.operaters import runfiles as storage
from demiflow.execution.artifacts import digest, file_record

BENCH_DIR = Path(__file__).resolve().parents[1]  # benchmark/edit/v1/（focus200 与活素材所在层）


class EditAuthoringRunFiles(storage.RunFiles):
    NAMESPACE = 'demiwtg/benchmark/edit/v1/datasets/'

    def __init__(self, run, config, graph, plan):
        if not storage.run_relative(run).startswith(self.NAMESPACE):
            raise ValueError('Use benchmark/edit/v1/datasets/<run>')
        self.run = Path(run).resolve()
        from preparation.operaters.runfiles import check_run_location
        from project import resolve_root
        check_run_location(self.run, storage.ROOT, resolve_root())
        self.branch = 'benchmark'
        self.config = config
        self.plan = import_plan(plan)
        prompt = Path(__file__).resolve().parents[1] / 'prompts/tasks.yaml'
        self.manifest = {
            'schema': 'benchmark-edit-v1-authoring/1', 'pipeline_version': 'V1',
            'pipeline_module': 'benchmark.edit.v1', 'branch': 'benchmark',
            'protocol': {'authoring': 'v6.1-image-first',
                         'prompt_sha256': digest(prompt.read_bytes())},
            'config': config, 'graph': graph,
            'inputs': {'plan': self.plan['source']},
            'implementation': storage.code_version()}
        self.initialize_business()
        self.version = digest(self.manifest)
        self.previous = self.version


def import_plan(plan):
    from demiflow.lance.artifacts import import_jsonl
    from project import resolve_root
    imported = import_jsonl(resolve_root(), plan)
    if isinstance(plan, dict):
        from demiflow.lance.artifacts import ArtifactSet
        assets=ArtifactSet(resolve_root(), plan['artifact_set'])
        for row in imported['rows']:
            row['images']=[{**im, 'blob_ref':assets.blob_ref(
                'benchmark/edit/v1/focus200/'+im['file']).to_dict()} for im in row.get('images',[])]
    return imported


def resolve_source_image(entry, plan_path):
    """Locate a plan image under focus200/, verify its recorded SHA, ingest to the lake."""
    if entry.get('blob_ref'):
        from demiflow.lance.blobs import BlobRef
        from project import resolve_root
        raw=BlobRef(**entry['blob_ref']).read(resolve_root())
        actual=digest(raw)
        if entry.get('sha256') and actual != entry['sha256']:
            raise ValueError('Plan source Blob differs')
        return {**entry,'path':entry['file'],'sha256':actual}
    candidates = [BENCH_DIR / 'focus200' / entry['file'],
                  Path(plan_path).parent / entry['file']]
    for candidate in candidates:
        if candidate.is_file():
            data = candidate.read_bytes()
            actual = digest(data)
            if entry.get('sha256') and actual != entry['sha256']:
                raise ValueError(f"plan image bytes changed: {candidate}")
            _ingest_source_image(data, actual, candidate.suffix)
            return {'file': entry['file'], 'path': str(candidate), 'sha256': actual,
                    'batch': entry.get('batch'), 'generator': entry.get('generator')}
    raise FileNotFoundError(f"plan source image missing: {entry['file']}")


def _ingest_source_image(data, sha, suffix):
    """Explicit lake import: SHA is identity, the focus200 path stays provenance."""
    from project import resolve_root
    from collect.material_writer import write_images
    write_images(resolve_root(), [{'sha256': sha, 'ext': suffix.lstrip('.').lower() or 'bin',
                                   'byte_size': len(data), 'storage_mode': 'lance_blob', 'data': data,
                                   'concepts': [], 'sources': [], 'availability': 'available',
                                   'resolution': None}])



def graph_digest():
    """Freeze the executable Python pipeline, independently of notebook views."""
    return digest((Path(__file__).resolve().parents[1] / "edit_v1_benchmark_pipeline.py").read_bytes())
