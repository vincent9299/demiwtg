"""An imported carrier string must not become an Arrow list of characters."""
import json
import pyarrow as pa
import pytest
from tools.lake_migration.schemas import REFERENCE_CONCEPTS
from tools.lake_migration.ingest_concepts import normalize_carriers
from tools.lake_migration.ingest_concepts import iter_concept_rows


def test_source_carriers_keep_semantics_through_arrow(tmp_path):
    (tmp_path/'meta').mkdir()
    (tmp_path/'meta/concepts.json').write_text(json.dumps({'concepts': [
        {'name': 'both', 'carriers': 'image+text'},
        {'name': 'textual', 'carriers': 'text'},
        {'name': 'image_only', 'carriers': ['image']},
    ]}))
    stats = {'declared': 0, 'rows': 0, 'nameless': 0}
    table = pa.Table.from_pylist(list(iter_concept_rows(tmp_path, 0, stats)), schema=REFERENCE_CONCEPTS)
    assert table['carriers'].to_pylist() == [['image', 'text'], ['text'], ['image']]
    assert normalize_carriers(list('image+text')) == ['image', 'text']
    with pytest.raises(ValueError): normalize_carriers('unrecognized')
