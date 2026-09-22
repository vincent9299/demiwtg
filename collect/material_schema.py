"""Raw image/document entities owned by collection."""
import pyarrow as pa
import lance

IMAGES_URI = 'raw/images.lance'
DOCUMENTS_URI = 'raw/documents.lance'
SOURCE = pa.struct([
    ('source_record_id', pa.string()), ('source_system', pa.string()),
    ('source_file', pa.string()), ('source_row', pa.int64()),
    ('url', pa.string()), ('content_url', pa.string()), ('landing_url', pa.string()),
    ('concepts', pa.list_(pa.string())), ('query', pa.string()),
    ('queries', pa.map_(pa.string(), pa.string())),
    ('query_languages', pa.map_(pa.string(), pa.string())),
    ('fetched_at', pa.string()), ('title', pa.string()), ('caption', pa.large_string()),
    ('license', pa.string()), ('author', pa.string()),
    ('declared_width', pa.int32()), ('declared_height', pa.int32()),
    ('original_width', pa.int32()), ('original_height', pa.int32()),
    ('declared_bytes', pa.int64()), ('declared_mime', pa.string()),
    ('kb_match', pa.float64()), ('richness', pa.float64()), ('identity', pa.bool_()),
    ('attributes_json', pa.large_string()),
])
RESOLUTION = pa.struct([('width', pa.int32()), ('height', pa.int32()),
    ('stored_width', pa.int32()), ('stored_height', pa.int32()),
    ('megapixels', pa.float64()), ('aspect_ratio', pa.float64()),
    ('orientation_basis', pa.string()), ('measurement_source', pa.string())])
IMAGE_METADATA = pa.schema([
    ('concepts', pa.list_(pa.string())), ('sources', pa.list_(SOURCE)),
    ('availability', pa.string()), ('resolution', RESOLUTION),
])
IMAGES = pa.schema([pa.field('sha256', pa.string(), nullable=False),
    pa.field('ext', pa.string(), nullable=False),
    pa.field('byte_size', pa.int64(), nullable=False),
    pa.field('storage_mode', pa.string(), nullable=False), lance.blob_field('data'),
    *IMAGE_METADATA])
SECTION = pa.struct([('title', pa.string()), ('text', pa.large_string()),
                     ('attributes_json', pa.large_string())])
DOCUMENT_IMAGE = pa.struct([('sha256',pa.string()),('url',pa.string()),
    ('position',pa.int64()),('caption',pa.string()),('attributes_json',pa.large_string())])
DOCUMENTS = pa.schema([
    pa.field('document_id',pa.string(),nullable=False),
    ('document_type',pa.string()),('language',pa.string()),('title',pa.string()),
    ('source_identity',pa.string()),('revision_id',pa.string()),
    ('content_sha256',pa.string()),('source_content_sha256',pa.string()),
    ('text',pa.large_string()),('sections',pa.list_(SECTION)),
    ('concepts',pa.list_(pa.string())),('sources',pa.list_(SOURCE)),
    ('images',pa.list_(DOCUMENT_IMAGE)),('content_status',pa.string()),
    ('page_id',pa.string()),('qid',pa.string()),('parser_version',pa.string()),
    ('is_redirect',pa.bool_()),('redirect_target',pa.string()),('is_disambig',pa.bool_()),
    ('categories',pa.list_(pa.string())),('links_json',pa.large_string()),('link_count',pa.int64()),
])
