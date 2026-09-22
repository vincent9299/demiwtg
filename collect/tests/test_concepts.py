"""Published concept inputs do not require taxonomy tables or follow table head."""
import lance
import pyarrow as pa
import pytest

from collect.concepts import CONCEPTS_URI, resolve_concept_release
from demiflow.lance.registry import ReleaseRegistry, write_registered_table


def test_concept_only_release_pins_the_input_version(tmp_path):
    schema = pa.schema([('name', pa.string())])
    ref, _, _ = write_registered_table(tmp_path, CONCEPTS_URI, schema=schema,
        schema_name='concepts', schema_version='v1', fingerprint='fixture',
        rows_factory=lambda: iter([{'name': 'published'}]))
    ReleaseRegistry(tmp_path).register('fixture', release_kind='master_data', table_refs=[ref])
    lance.write_dataset(pa.Table.from_pylist([{'name': 'unpublished'}], schema=schema),
                        str(tmp_path / CONCEPTS_URI), mode='overwrite')
    resolved = resolve_concept_release(tmp_path, 'fixture')
    assert resolved['dataset_ref'].open(tmp_path).to_table()['name'].to_pylist() == ['published']


def test_missing_release_does_not_fall_back_to_current_table(tmp_path):
    with pytest.raises(ValueError, match='not registered'):
        resolve_concept_release(tmp_path, 'missing')
