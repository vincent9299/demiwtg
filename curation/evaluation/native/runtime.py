"""Notebook-owned answer + judge evaluation; default prepare makes no model calls."""
import argparse
from pathlib import Path

from curation.preparation.records import digest, read
from curation.evaluation.native.contracts import BATCH, default_config

NOTEBOOK = Path(__file__).resolve().parents[2] / 'evaluation/debug.ipynb'


def graph_source():
    import nbformat
    note = nbformat.read(NOTEBOOK, as_version=4)
    return '\n\n'.join(c.source for c in note.cells
                       if c.cell_type == 'code' and 'pipeline' in c.metadata.get('tags', []))


def graph_version():
    return digest(graph_source())


def load_graph():
    namespace = {}
    exec(compile(graph_source(), str(NOTEBOOK), 'exec'), namespace)
    return namespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--questions', type=Path)
    parser.add_argument('--knowledge-runs', nargs='+')
    parser.add_argument('--visual-runs', nargs='*', default=[])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--through', choices=['prepare', 'rubrics', 'generate', 'judge'], default='prepare')
    parser.add_argument('--judge-only', action='store_true', help='Score frozen answers without image-model execution')
    parser.add_argument('--backend', choices=['bagel', 'qwen2511', 'qwen2512', 'gemini'])
    args = parser.parse_args()
    graph = load_graph()
    if args.judge_only:
        if args.backend or args.questions or args.knowledge_runs or args.config:
            parser.error('--judge-only uses only the frozen run')
        print(graph['run_judging'](args.run))
    elif args.backend:
        if args.through != 'generate':
            parser.error('--backend requires explicit --through generate')
        print(graph['run_backend'](args.run, args.backend))
    else:
        from curation.preparation.records import run_records
        frozen = run_records(args.run).get('manifest')
        config = read(args.config) if args.config else frozen['config'] if frozen else default_config()
        questions = read(args.questions) if args.questions else frozen['questions'] if frozen else None
        runs = args.knowledge_runs or (frozen['knowledge'] if frozen else [])
        if questions is None or not runs:
            parser.error('Supply --questions (DatasetRef JSON configuration) and --knowledge-runs (release IDs)')
        print(graph['run_pipeline'](args.run, questions, runs, config, through=args.through, visual_runs=args.visual_runs))
