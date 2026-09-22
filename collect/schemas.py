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

VERBATIM_RECORDS = _with_provenance([
    pa.field("asset_name", pa.string(), nullable=False),   # 来源文件名
    pa.field("record_key", pa.string(), nullable=False),   # 行号或 'doc'
    pa.field("payload", pa.large_string(), nullable=False),# 原样行/全文
])
