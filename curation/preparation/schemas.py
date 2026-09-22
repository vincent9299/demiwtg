"""Runtime checkpoint row schema; entity schemas live with their business modules."""
import pyarrow as pa
SCHEMA_VERSION = "v1"

PIPELINE_STAGE_ROWS = pa.schema([
    pa.field("row_id", pa.string(), nullable=False),
    pa.field("stage", pa.string(), nullable=False),
    pa.field("concept", pa.string(), nullable=True),
    pa.field("status", pa.string(), nullable=True),
    pa.field("upstream_ref", pa.large_string(), nullable=True),
    pa.field("asset_shas", pa.list_(pa.string()), nullable=True),
    pa.field("review_json", pa.large_string(), nullable=True),
    pa.field("payload", pa.large_string(), nullable=False),
    pa.field("migrated_at_us", pa.timestamp("us", tz="UTC"), nullable=False),
])
