"""Read the fixed concept table selected by a published release."""
import json
import os

from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import ReleaseRegistry
from project import resolve_root

CONCEPTS_URI = 'datasets/master_concepts.lance'
DEFAULT_CONCEPT_RELEASE = 'master_data_current_20260921'


def resolve_concept_release(root=None, release_id=None):
    root = resolve_root(root)
    chosen = release_id or os.environ.get('DEMIWTG_MASTER_RELEASE') or DEFAULT_CONCEPT_RELEASE
    record = ReleaseRegistry(root).get(chosen)
    if record is None or record['status'] != 'registered' or record['release_kind'] != 'master_data':
        raise ValueError('Concept release is not registered: ' + chosen)
    refs = [DatasetRef.from_dict(item) for item in json.loads(record['table_refs'])
            if DatasetRef.from_dict(item).resolve(root) == str(root / CONCEPTS_URI)]
    if len(refs) != 1:
        raise ValueError('Concept release must select exactly one concept table: ' + chosen)
    refs[0].open(root)
    return {'release_id': chosen, 'dataset_ref': refs[0]}
