"""Run identity and explicit historical-input boundaries for V1 T2I authoring.

The historical bench200 batches stay read-only evidence. A new run consumes a
samples JSONL through an explicit recorded import; it never falls back between
files and the lake based on what happens to exist.
"""
import json
from pathlib import Path

from preparation.operaters import runfiles as storage
from demiflow.execution.artifacts import digest, file_record
from demiflow.lance.artifacts import import_jsonl


class T2IAuthoringRunFiles(storage.RunFiles):
    NAMESPACE = 'demiwtg/benchmark/t2i/v1/datasets/'

    def __init__(self, run, config, graph, samples):
        if not storage.run_relative(run).startswith(self.NAMESPACE):
            raise ValueError('Use benchmark/t2i/v1/datasets/<run>')
        if config.get('schema') != 'v6.0':
            raise ValueError('benchmark.t2i.v1 authors with the v6.0 protocol only')
        self.run = Path(run).resolve()
        from preparation.operaters.runfiles import check_run_location
        from project import resolve_root
        check_run_location(self.run, storage.ROOT, resolve_root())
        self.branch = 'benchmark'
        self.config = config
        imported = import_jsonl(resolve_root(), samples)
        # 运行冻结直接记录 YAML，样本导入不再夹带固定 prompt 字节。
        prompt = Path(__file__).resolve().parents[1] / 'prompts/tasks.yaml'
        self.manifest = {
            'schema': 'benchmark-t2i-v1-synth/1', 'pipeline_version': 'V1',
            'pipeline_module': 'benchmark.t2i.v1', 'branch': 'benchmark',
            'protocol': {'authoring': 'v6.0-entry-items',
                         'prompt_sha256': digest(prompt.read_bytes())},
            'config': config, 'graph': graph,
            'inputs': {'samples': imported['source']},
            'implementation': storage.code_version()}
        self.samples = imported
        self.initialize_business()
        self.version = digest(self.manifest)
        self.previous = self.version


def fail(row, status, reason):
    return {**row, 'status': status, 'fail_reason': reason}



def graph_digest():
    """Freeze the executable Python pipeline, independently of notebook views."""
    return digest((Path(__file__).resolve().parents[1] / "t2i_v1_benchmark_pipeline.py").read_bytes())
