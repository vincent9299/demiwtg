"""Run identity and explicit input boundaries for the V1 T2I evaluation.

Historical bench200 batches and model outputs stay read-only evidence. A new
run imports a questions JSONL explicitly; historical generations may be
imported as an explicit responses file instead of calling image models.
"""
import json
import re
from pathlib import Path

from preparation.operaters import runfiles as storage
from demiflow.execution.artifacts import digest, file_record


class T2IEvaluationRunFiles(storage.RunFiles):
    NAMESPACE = 'demiwtg/evaluation/t2i/v1/datasets/'

    def __init__(self, run, config, graph, questions, responses=None):
        if not storage.run_relative(run).startswith(self.NAMESPACE):
            raise ValueError('Use evaluation/t2i/v1/datasets/<run>')
        self.run = Path(run).resolve()
        from preparation.operaters.runfiles import check_run_location
        from project import resolve_root
        check_run_location(self.run, storage.ROOT, resolve_root())
        self.branch = 'evaluation'
        self.config = config
        self.questions = import_questions(questions)
        self.responses = import_responses(responses) if responses else None
        judge = Path(__file__).resolve().parents[1] / 'prompts/tasks.yaml'
        self.manifest = {
            'schema': 'evaluation-t2i-v1/1', 'pipeline_version': 'V1',
            'pipeline_module': 'evaluation.t2i.v1', 'branch': 'evaluation',
            'protocol': {'judge': 'v6.0-V2', 'judge_prompt_sha256': digest(judge.read_bytes())},
            'config': config, 'graph': graph,
            'inputs': {'questions': self.questions['source'],
                       'responses': self.responses['source'] if self.responses else None},
            'implementation': storage.code_version()}
        self.initialize_business()
        self.version = digest(self.manifest)
        self.previous = self.version


def import_questions(questions):
    from demiflow.lance.artifacts import import_jsonl
    from project import resolve_root
    imported = import_jsonl(resolve_root(), questions)
    imported['rows'] = [q for q in imported['rows'] if q.get('task', 't2i') == 't2i']
    return imported


def import_responses(responses):
    from demiflow.lance.artifacts import import_jsonl
    from project import resolve_root
    imported = import_jsonl(resolve_root(), responses)
    if isinstance(responses, dict):
        from demiflow.lance.artifacts import ArtifactSet
        from project import evidence_key
        assets=ArtifactSet(resolve_root(), responses['artifact_set'])
        for row in imported['rows']:
            if row.get('ok') and row.get('image'):
                path=Path(row['image'])
                key=evidence_key(path) if path.is_absolute() else str(Path(responses['path']).parent/path)
                row['_image_blob_ref']=assets.blob_ref(key).to_dict()
    return imported


def short_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model.split("/")[-1])



def graph_digest():
    """Freeze the executable Python pipeline, independently of notebook views."""
    return digest((Path(__file__).resolve().parents[1] / "t2i_v1_eval_pipeline.py").read_bytes())
