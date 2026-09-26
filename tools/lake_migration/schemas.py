import pyarrow as pa

SCHEMA_VERSION = "v1"
from collect.schemas import _with_provenance

REFERENCE_CONCEPTS = _with_provenance([
    pa.field("name", pa.string(), nullable=False),
    pa.field("aliases", pa.list_(pa.string()), nullable=True),
    pa.field("carriers", pa.list_(pa.string()), nullable=True),
    pa.field("taxonomy", pa.large_string(), nullable=True),
])

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

CONCEPT_TAXONOMY_MEMBERSHIPS = pa.schema([
    pa.field("concept_key", pa.string(), nullable=False),        # instances 原文；指向 concepts.name
    pa.field("taxonomy_node_key", pa.string(), nullable=False),  # 节点主键（path）
    pa.field("ordinal", pa.int32(), nullable=False),             # 节点 instances 列表内位置（0 起）
    pa.field("source_ref", pa.string(), nullable=False),         # 提取来源固定引用 '<relative_uri>@<version>'
    pa.field("source_record_key", pa.int64(), nullable=False),   # 来源节点行在固定版本内行位置（0 起）
    pa.field("migrated_at_us", pa.timestamp("us", tz="UTC"), nullable=False),
])

"""Schema lookup for one-off import tools; runtime code imports domain schemas directly."""
from collect.schemas import VERBATIM_RECORDS
from .legacy_schemas import CONCEPT_MATCHES, IMAGE_DESCRIPTIONS, KNOWLEDGE_RELEASE, KNOWLEDGE_RUN, VISUAL_MATERIALS_RELEASE
from preparation.operaters.results import PIPELINE_STAGE_ROWS

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
    "concept_taxonomy_memberships": CONCEPT_TAXONOMY_MEMBERSHIPS,
    "verbatim_records": VERBATIM_RECORDS,
    "pipeline_stage_rows": PIPELINE_STAGE_ROWS,
}
