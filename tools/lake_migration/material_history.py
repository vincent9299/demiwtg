"""Schemas for retained source evidence, separate from current material pools."""
import pyarrow as pa
from collect.material_schema import SOURCE

KNOWLEDGE_DRAFTS = pa.schema([
    ('concept', pa.string()), ('draft_kind', pa.string()), ('text', pa.large_string()),
    ('review_status', pa.string()), ('source_file', pa.string()), ('source_row', pa.int64()),
])
IMAGE_OBSERVATIONS = pa.schema([
    ('sha256', pa.string()), ('ext', pa.string()), ('concepts', pa.list_(pa.string())),
    ('source', SOURCE), ('source_payload_sha256', pa.string()),
])
CONCEPT_SELECTIONS = pa.schema([
    ('selection_name', pa.string()), ('ordinal', pa.int64()), ('concept', pa.string()),
    ('aliases', pa.list_(pa.string())), ('carriers', pa.list_(pa.string())),
    ('taxonomy_paths', pa.list_(pa.string())), ('source_file', pa.string()),
])
TAXONOMY_HISTORY = pa.schema([
    ('node_path', pa.string()), ('node_path_en', pa.string()),
    ('instances_text', pa.large_string()), ('instances_en_text', pa.large_string()),
    ('source_names_text', pa.large_string()), ('source_details_text', pa.large_string()),
    ('origins', pa.list_(pa.struct([('source_file', pa.string()), ('source_row', pa.int64())]))),
])
ANNOTATION_PROTOCOLS = pa.schema([
    ('protocol_id', pa.string()), ('version', pa.int64()),
    ('describe_prompt', pa.large_string()), ('match_prompt', pa.large_string()),
    ('model', pa.string()), ('author_type', pa.string()), ('human_reviewed', pa.bool_()),
    ('configuration_json', pa.large_string()), ('source_file', pa.string()),
])
