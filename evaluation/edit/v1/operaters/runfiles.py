"""Run identity and explicit input boundaries for the V1 Edit evaluation.

The frozen Codex/QIB judging chain (eval_codex_score.py + judge template,
dispatch and freeze scripts) keeps its original bytes and stays outside this
pipeline. This graph covers the LLM-judge contract of eval_score.py: per
edit_type three-perspective 1-5 scoring with the hard clamp rule.
"""
import json
import re
from pathlib import Path

from preparation.operaters import runfiles as storage
from demiflow.execution.artifacts import digest, file_record

BENCH_DIR = Path(__file__).resolve().parents[4] / "benchmark/edit/v1"
ALLOWED_SOURCE_ROOT = (BENCH_DIR / "focus200").resolve()


class EditEvaluationRunFiles(storage.RunFiles):
    NAMESPACE = 'demiwtg/evaluation/edit/v1/datasets/'

    def __init__(self, run, config, graph, questions, responses=None):
        if not storage.run_relative(run).startswith(self.NAMESPACE):
            raise ValueError('Use evaluation/edit/v1/datasets/<run>')
        self.run = Path(run).resolve()
        from preparation.operaters.runfiles import check_run_location
        from project import resolve_root
        check_run_location(self.run, storage.ROOT, resolve_root())
        self.branch = 'evaluation'
        self.config = config
        self.questions = import_questions(questions)
        self.responses = import_responses(responses) if responses else None
        contract = Path(__file__).resolve().parents[1] / 'prompts/edit_score_prompts.json'
        self.manifest = {
            'schema': 'evaluation-edit-v1/1', 'pipeline_version': 'V1',
            'pipeline_module': 'evaluation.edit.v1', 'branch': 'evaluation',
            'protocol': {'judge': 'edit-imgedit-three-perspective',
                         'contract_sha256': digest(contract.read_bytes())},
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
    imported['rows'] = [q for q in imported['rows'] if q.get('task', 't2i') == 'edit' and q.get('status','constructed') == 'constructed']
    if isinstance(questions, dict):
        from demiflow.lance.artifacts import ArtifactSet
        from project import evidence_key
        assets=ArtifactSet(resolve_root(), questions['artifact_set'])
        imported['rows']=[{**q, '_source_blob_ref':assets.blob_ref(
            'benchmark/edit/v1/'+q['_sample_image']).to_dict()} for q in imported['rows']]
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


def resolve_source(question):
    """Ported from eval_edit_gen.resolve_source（含 focus200 允许根约束）."""
    rel = question.get("_sample_image")
    if not rel and question.get("_file"):
        rel = f"focus200/{question['_file']}"
    if not rel:
        raise ValueError("题目缺 _sample_image/_file")
    src = Path(rel)
    candidates = [src] if src.is_absolute() else [
        BENCH_DIR / src, BENCH_DIR.parent / src, BENCH_DIR.parent.parent / src]
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(ALLOWED_SOURCE_ROOT):
            raise ValueError(f"源图必须是 {ALLOWED_SOURCE_ROOT} 内的普通文件：{rel}")
        return resolved
    raise FileNotFoundError(f"源图不存在：{rel}")


QID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")



def graph_digest():
    """Freeze the executable Python pipeline, independently of notebook views."""
    return digest((Path(__file__).resolve().parents[1] / "edit_v1_eval_pipeline.py").read_bytes())
