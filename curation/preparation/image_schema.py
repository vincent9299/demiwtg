"""Image curation attributes. All lists belong to one SHA-addressed image."""
import pyarrow as pa

PROVENANCE = pa.struct([
    ('run_id', pa.string()), ('config_id', pa.string()), ('config_path', pa.string()),
    ('model', pa.string()), ('source_file', pa.string()), ('source_row', pa.int64()),
    ('record_sha256', pa.string()), ('details_json', pa.large_string()),
])
DESCRIPTION = pa.struct([
    ('caption', pa.large_string()), ('representation', pa.string()),
    ('view_tags', pa.list_(pa.string())),
    ('objects', pa.list_(pa.struct([('name',pa.string()),('location',pa.string()),('visible_features',pa.large_string())]))),
    ('text_regions', pa.list_(pa.struct([('text',pa.large_string()),('location',pa.string()),('readability',pa.string())]))),
    ('observability_issues', pa.list_(pa.struct([('type',pa.string()),('location',pa.string()),('detail',pa.large_string())]))),
    ('uncertainties', pa.list_(pa.string())),
])
DESCRIPTION_RECORD = pa.struct([
    ('annotation_id', pa.string()), ('config_id',pa.string()), ('status',pa.string()),
    ('description', DESCRIPTION), ('attempts',pa.int64()), ('provenance', PROVENANCE),
])
CONCEPT_MATCH = pa.struct([
    ('annotation_id',pa.string()), ('concept',pa.string()), ('config_id',pa.string()),
    ('status',pa.string()), ('reason',pa.large_string()), ('provenance',PROVENANCE),
])
SUPPORT = pa.struct([('supports',pa.large_string()),('region',pa.string()),('limitations',pa.large_string())])
ASSESSMENT = pa.struct([
    ('assessment_id',pa.string()), ('concept',pa.string()), ('image_id',pa.string()),
    ('review_status',pa.string()), ('published',pa.bool_()), ('release_ids',pa.list_(pa.string())),
    ('identity_relation',pa.string()), ('reason',pa.large_string()),
    ('visual_support',SUPPORT), ('description',DESCRIPTION),
    ('human_reviewed',pa.bool_()), ('provenance',PROVENANCE),
    # Full model reviews/call traces remain execution evidence, not query keys.
    ('review_json',pa.large_string()), ('observation_json',pa.large_string()),
])
IMAGE_CURATION = pa.schema([
    ('descriptions',pa.list_(DESCRIPTION_RECORD)),
    ('concept_matches',pa.list_(CONCEPT_MATCH)),
    ('concept_assessments',pa.list_(ASSESSMENT)),
    ('published_concepts',pa.list_(pa.string())),
    ('release_ids',pa.list_(pa.string())),
])

# No source bytes, acquisition metadata or duplicate raw concepts in this table.
IMAGES_URI = 'curated/images.lance'
IMAGES = pa.schema([
    pa.field('sha256', pa.string(), nullable=False),
    ('source_refs', pa.list_(pa.large_string())),
    ('concepts', pa.list_(pa.string())),
    *IMAGE_CURATION,
])
