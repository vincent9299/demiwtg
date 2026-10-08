"""Read the fixed concept table selected by a published release."""
import json
import os

from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import ReleaseRegistry
from demiflow.lance.storage import resolve_local_uri
from project import resolve_root

CONCEPTS_URI = 'demiwtg/collect/datasets/concepts.lance'
# 合并挂载信息后的固定发布，主表为 v3；历史实验仍按各自显式版本读取。
DEFAULT_CONCEPT_RELEASE = 'master_data_merged_20260927'


def resolve_concept_release(root=None, release_id=None):
    root = resolve_root(root)
    chosen = release_id or os.environ.get('DEMIWTG_MASTER_RELEASE') or DEFAULT_CONCEPT_RELEASE
    record = ReleaseRegistry(root).get(chosen)
    if record is None or record['status'] != 'registered' or record['release_kind'] != 'master_data':
        raise ValueError('Concept release is not registered: ' + chosen)
    refs = [DatasetRef.from_dict(item) for item in json.loads(record['table_refs'])
            if DatasetRef.from_dict(item).resolve(root) == str(resolve_local_uri(root / CONCEPTS_URI))]
    if len(refs) != 1:
        raise ValueError('Concept release must select exactly one concept table: ' + chosen)
    refs[0].open(root)
    return {'release_id': chosen, 'dataset_ref': refs[0]}
