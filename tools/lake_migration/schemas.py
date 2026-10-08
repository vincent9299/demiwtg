import pyarrow as pa

SCHEMA_VERSION = "v1"
from collect.schemas import _with_provenance, REFERENCE_CONCEPTS

TAXONOMY_NODES = _with_provenance([
    pa.field("path", pa.string(), nullable=False),       # 节点主键（'/' 分层路径）
    pa.field("parent_path", pa.string(), nullable=True),
    pa.field("name", pa.string(), nullable=False),
    pa.field("depth", pa.int32(), nullable=False),
    pa.field("instances", pa.list_(pa.string()), nullable=True),  # 挂载概念名单
    pa.field("aliases", pa.list_(pa.string()), nullable=True),
    pa.field("knowledge_intro", pa.large_string(), nullable=True),
    pa.field("related_tags", pa.large_string(), nullable=True),
    pa.field("representative_cases", pa.large_string(), nullable=True),
])

TAXONOMY_EDGES = _with_provenance([
    pa.field("parent_path", pa.string(), nullable=False),
    pa.field("child_path", pa.string(), nullable=False),
    pa.field("ordinal", pa.int32(), nullable=False),      # 兄弟序（0 起）
])

"""Schema lookup for one-off import tools; runtime code imports domain schemas directly."""
from collect.schemas import VERBATIM_RECORDS
from .legacy_schemas import CONCEPT_MATCHES, IMAGE_DESCRIPTIONS, KNOWLEDGE_RELEASE, KNOWLEDGE_RUN, VISUAL_MATERIALS_RELEASE
from preparation.articles.operators.results import PIPELINE_STAGE_ROWS

SCHEMA_VERSION = "v1"
ALL_SCHEMAS = {
    "reference_concepts": REFERENCE_CONCEPTS,
    "image_descriptions": IMAGE_DESCRIPTIONS,
    "concept_matches": CONCEPT_MATCHES,
    "visual_materials_release": VISUAL_MATERIALS_RELEASE,
    "knowledge_release": KNOWLEDGE_RELEASE,
    "knowledge_run": KNOWLEDGE_RUN,
    "taxonomy_nodes": TAXONOMY_NODES,
    "taxonomy_edges": TAXONOMY_EDGES,
    "verbatim_records": VERBATIM_RECORDS,
    "pipeline_stage_rows": PIPELINE_STAGE_ROWS,
}
