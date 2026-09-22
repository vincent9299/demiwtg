"""Judge a completed native backend partition without waiting for other GPUs.

The operator chain lives in the evaluation notebook's partition-judge cell.
Its own manifest freezes this additive execution branch. The parent run, its
pre-answer packets, prompts and implementation remain unchanged and verified.
"""
from curation.preparation.records import run_state, run_manifest, run_records
import argparse
from pathlib import Path

from curation.preparation.records import RunFiles, digest, file_record, immutable
from curation.evaluation.native.runtime import NOTEBOOK, load_graph


def partition_source():
    import nbformat
    note = nbformat.read(NOTEBOOK, as_version=4)
    cells = [c.source for c in note.cells if c.cell_type == 'code'
             and 'partition-judge' in c.metadata.get('tags', [])]
    if len(cells) != 1:
        raise ValueError('Expected one explicit partition-judge operator cell')
    return cells[0]


class PartitionFiles(RunFiles):
    def __init__(self, parent, backend, source, graph):
        self.run = Path(parent) / 'partition_evaluations' / backend
        self.branch = 'completed_partition_judge'
        self.manifest = {
            'schema': 'v4-completed-partition-judge/1', 'backend': backend,
            'parent': run_records(parent).reference('manifest').to_dict(),
            'source': source, 'graph_source': graph,
            'entrypoint': file_record(__file__),
            'scope': 'Partial results only; not a completed multi-model evaluation',
        }
        self.initialize_business()
        self.previous = self.version = digest(self.manifest)
        self.stages, self.reused, self.new = {}, [], []


def load_partition_graph():
    namespace = load_graph()
    source = partition_source()
    namespace.update(PartitionFiles=PartitionFiles, partition_source=lambda: source)
    exec(compile(source, str(NOTEBOOK) + ':partition-judge', 'exec'), namespace)
    return namespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--backend', required=True, choices=['bagel', 'qwen2511', 'qwen2512', 'gemini'])
    args = parser.parse_args()
    print(load_partition_graph()['run_partition_judging'](args.run, args.backend))


if __name__ == '__main__':
    main()
