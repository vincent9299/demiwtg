"""Frozen source schemas used only by historical import tools."""
import pyarrow as pa

SCHEMA_VERSION = "v1"
from collect.schemas import _with_provenance

IMAGE_DESCRIPTIONS = _with_provenance([
    pa.field("sha256", pa.string(), nullable=False),
    pa.field("status", pa.string(), nullable=False),
    pa.field("caption", pa.large_string(), nullable=True),
    pa.field("representation", pa.string(), nullable=True),
    pa.field("view_tags", pa.list_(pa.string()), nullable=True),
    pa.field("objects", pa.large_string(), nullable=True),
    pa.field("text_regions", pa.large_string(), nullable=True),
    pa.field("observability_issues", pa.large_string(), nullable=True),
    pa.field("uncertainties", pa.list_(pa.string()), nullable=True),
    pa.field("attempts", pa.int64(), nullable=True),
])

CONCEPT_MATCHES = _with_provenance([
    pa.field("sha256", pa.string(), nullable=False),
    pa.field("concept", pa.string(), nullable=False),
    pa.field("match_status", pa.string(), nullable=True),
    pa.field("reason", pa.large_string(), nullable=True),
    pa.field("raw_result", pa.large_string(), nullable=False),
])

VISUAL_MATERIALS_RELEASE = _with_provenance([
    pa.field("release_id", pa.string(), nullable=False),
    pa.field("concept", pa.string(), nullable=False),
    pa.field("sha256", pa.string(), nullable=False),
    pa.field("ext", pa.string(), nullable=False),
    pa.field("review_status", pa.string(), nullable=False),
    pa.field("published", pa.bool_(), nullable=False),
    pa.field("pixel_fingerprint_role", pa.string(), nullable=True),
    pa.field("labels_json", pa.large_string(), nullable=True),
    pa.field("exclude_reason", pa.large_string(), nullable=True),
    pa.field("annotation_ref_sha", pa.string(), nullable=True),
])

KNOWLEDGE_RELEASE = _with_provenance([
    pa.field("release_id", pa.string(), nullable=False),
    pa.field("concept", pa.string(), nullable=False),
    pa.field("case_id", pa.string(), nullable=True),
    pa.field("status", pa.string(), nullable=False),
    pa.field("knowledge_count", pa.int64(), nullable=False),
    pa.field("image_count", pa.int64(), nullable=False),
    pa.field("document_count", pa.int64(), nullable=False),
])

KNOWLEDGE_RUN = _with_provenance([
    pa.field("run_id", pa.string(), nullable=False),
    pa.field("concept", pa.string(), nullable=False),
    pa.field("case_id", pa.string(), nullable=True),
    pa.field("status", pa.string(), nullable=False),
    pa.field("status_reason", pa.large_string(), nullable=True),
    pa.field("knowledge_count", pa.int64(), nullable=False),
    pa.field("image_count", pa.int64(), nullable=False),
    pa.field("document_count", pa.int64(), nullable=False),
])

