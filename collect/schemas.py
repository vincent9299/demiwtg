"""Project business schemas; field types preserve existing Lance contracts."""
import pyarrow as pa

SCHEMA_VERSION = "v1"

PROVENANCE_FIELDS = [
    pa.field("source_file", pa.string(), nullable=False),
    pa.field("source_row", pa.int64(), nullable=False),
    pa.field("raw_payload", pa.large_string(), nullable=False),
    pa.field("migrated_at_us", pa.timestamp("us", tz="UTC"), nullable=False),
]

def _with_provenance(fields):
    return pa.schema(list(fields) + PROVENANCE_FIELDS)

# taxonomy 继续保存 JSON 路径列表，供现有固定版本消费者读取。
REFERENCE_CONCEPTS = _with_provenance([
    pa.field("name", pa.string(), nullable=False),
    pa.field("aliases", pa.list_(pa.string()), nullable=True),
    pa.field("carriers", pa.list_(pa.string()), nullable=True),
    pa.field("taxonomy", pa.large_string(), nullable=True),
])

# 一项对应 taxonomy 中的一条路径；保存退役关系表的排序与固定来源，
# taxonomy_node_key 保留含 demiwtg 根节点的原始键，不改变来源语义。
TAXONOMY_METADATA = pa.field("taxonomy_metadata", pa.list_(pa.struct([
    pa.field("taxonomy_node_key", pa.string(), nullable=False),
    pa.field("ordinal", pa.int32(), nullable=False),
    pa.field("source_ref", pa.string(), nullable=False),
    pa.field("source_record_key", pa.int64(), nullable=False),
    pa.field("migrated_at_us", pa.timestamp("us", tz="UTC"), nullable=False),
])), nullable=True)
MASTER_CONCEPTS = REFERENCE_CONCEPTS.append(TAXONOMY_METADATA)

VERBATIM_RECORDS = _with_provenance([
    pa.field("asset_name", pa.string(), nullable=False),   # 来源文件名
    pa.field("record_key", pa.string(), nullable=False),   # 行号或 'doc'
    pa.field("payload", pa.large_string(), nullable=False),# 原样行/全文
])
