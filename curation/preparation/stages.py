"""V2 stage table layout and business row schema. Execution stays in demiflow."""
from pathlib import Path
from demiflow.lance.checkpoint import read_checkpoint_record
from demiflow.lance.refs import DatasetRef
from demiflow.lance.storage import schema_hash
from demiflow.standalone import local_data
from project import resolve_root
from curation.preparation.schemas import PIPELINE_STAGE_ROWS, SCHEMA_VERSION
from curation.preparation.records import to_stage_row, from_stage_row


def stage_uri(run, name):
    # The run name is the project's stable execution identity, independent of the lake root.
    from curation.preparation.records import identifier
    from curation.preparation.records import run_relative
    return str(resolve_root() / run_relative(run) / 'stages' / (identifier(name) + '.lance'))


class EncodeStage:
    def __init__(self, name): self.name = name
    def __call__(self, row): return to_stage_row(row, self.name, None, 0)


def stage_ref(run, name):
    uri = stage_uri(run, name)
    record = read_checkpoint_record(uri)
    if record is None:
        raise ValueError(f'Missing Lance stage: {name}')
    relative = str(Path(uri).relative_to(resolve_root()))
    ref = DatasetRef(relative[:-6], relative, record['committed_version'],
                      'pipeline_stage_rows', SCHEMA_VERSION, schema_hash(PIPELINE_STAGE_ROWS), record['row_count'])
    from demiflow.lance.registry import Catalog
    Catalog(resolve_root()).register(ref)
    return ref


def read_stage(run, name, data=None):
    ref = stage_ref(run, name)
    return (data or local_data()).read_lance(ref.resolve(resolve_root()), version=ref.lance_version).map(from_stage_row)
