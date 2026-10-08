"""候选 value 与生效字段同类型；原始记录使用生产者完整 schema 保存。"""
import pyarrow as pa

# These producers own the source contracts; no producer entry point is imported.
from preparation.qid_concepts.operators.schema import CONCEPTS as QID, DOCUMENT, CLASSIFICATION, XREF, RELATION
from preparation.qid_review.operators.schema import REVIEWS as QID_REVIEW
from preparation.concepts.operators.schema import ADOPTED, REVISED, FACT, TASK, PLACEMENTS
from preparation.concept_positive_images.operators.schema import (
    POSITIVE_IMAGES, RESULTS as IMAGE_REVIEWS, COVERAGE,
)

SCHEMA_VERSION = 'visual-concepts-v1'
RULES_VERSION = 'explicit-identity-candidates-v1'
STRINGS = pa.list_(pa.string())
REF = pa.struct([('uri', pa.string()), ('version', pa.int64())])
ROW_REF = pa.struct([('uri', pa.string()), ('version', pa.int64()),
                     ('record_key', pa.string()), ('source_kind', pa.string())])
ORIGIN = pa.struct([('record', ROW_REF), ('field', pa.string())])
LEGACY = pa.schema([*ADOPTED, REVISED.field('revision')])
RELATED = {'positive_images': POSITIVE_IMAGES, 'image_reviews': IMAGE_REVIEWS,
           'image_coverage': COVERAGE, 'placements': PLACEMENTS}
SOURCE_SCHEMAS = {'qid': QID, 'qid_review': QID_REVIEW, 'legacy': LEGACY, **RELATED}


def source_record(schema):
    return pa.struct([('source', ROW_REF), ('present_fields', STRINGS),
                      ('value', pa.struct(list(schema)))])


PRIMARY_DOCUMENT = pa.struct([('language', pa.string()), ('page_id', pa.int64()),
                              ('title', pa.string()), ('document', DOCUMENT)])
PRIMARY_IMAGE = pa.struct([('sha256', pa.string()), ('image_uri', pa.string()),
                           ('filename', pa.string()), ('basis', pa.string())])
VALUE_TYPES = {
    'qid': pa.string(), 'canonical_name': pa.string(),
    'name_en': pa.string(), 'name_zh': pa.string(), 'definition': pa.string(),
    'concept_kind': pa.string(), 'qualifiers': STRINGS, 'taxon_rank': pa.string(),
    'name_relation': pa.string(), 'identity_status': pa.string(),
    'task_status': pa.string(), 'task_sketch': TASK,
    'visual_value_decision': pa.string(), 'visual_value_reason': pa.string(),
    'primary_document': PRIMARY_DOCUMENT, 'primary_image': PRIMARY_IMAGE,
    'classification': CLASSIFICATION,
}


def candidates(value_type):
    return pa.list_(pa.struct([
        ('candidate_id', pa.string()), ('value', value_type),
        ('sources', pa.list_(ORIGIN)), ('priority', pa.int64()), ('rank', pa.int64()),
        ('eligible', pa.bool_()), ('selected', pa.bool_()),
        ('selection_reason', pa.string()),
    ]))


ALIGNMENTS = pa.schema([
    ('source_key', pa.string()), ('target_qid', pa.string()),
    ('assessment_id', pa.string()), ('relation', pa.string()), ('status', pa.string()),
    ('evidence_refs', STRINGS), ('reason', pa.string()),
])
LEGACY_SELECTION = pa.schema([('source_uri', pa.string()), ('source_version', pa.int64()),
                              ('concept_id', pa.string()), ('assessment_id', pa.string())])
CHOICES = pa.schema([('identity_key', pa.string()), ('field', pa.string()),
                     ('candidate_id', pa.string()), ('reason', pa.string())])
RESOLUTION = pa.struct([('field', pa.string()), ('status', pa.string()),
                        ('selected_id', pa.string()), ('rule', pa.string()),
                        ('choice_kind', pa.string()), ('reason', pa.string()),
                        ('choice_source', ROW_REF)])
SOURCE_MEMBER = pa.struct([('source_key', pa.string()), ('source', ROW_REF)])
NAME = pa.struct([('text', pa.string()), ('language', pa.string()),
                  ('kind', pa.string()), ('sources', pa.list_(ORIGIN))])


def sourced(value_type):
    return pa.list_(pa.struct([('value', value_type), ('sources', pa.list_(ORIGIN))]))


CONCEPTS = pa.schema([
    ('concept_id', pa.string()), ('identity_key', pa.string()),
    ('schema_version', pa.string()), ('rules_version', pa.string()),
    *[f for name, typ in VALUE_TYPES.items()
      for f in [pa.field(name, typ), pa.field(name + '_candidates', candidates(typ))]],
    ('names', pa.list_(NAME)), ('core_facts', sourced(FACT)),
    ('properties', sourced(XREF)), ('relations', sourced(RELATION)),
    ('image_sha256s', STRINGS), ('source_members', pa.list_(SOURCE_MEMBER)),
    ('identity_links', pa.list_(source_record(ALIGNMENTS))),
    *[(kind + '_records', pa.list_(source_record(schema))) for kind, schema in SOURCE_SCHEMAS.items()],
    ('resolutions', pa.list_(RESOLUTION)), ('conflict_fields', STRINGS),
    ('redirected_concept_ids', STRINGS),
    ('previous_records', pa.list_(ROW_REF)),
    ('resource_sources', pa.list_(pa.struct([('kind', pa.string()), ('source', REF)]))),
])
IDENTITIES = pa.schema([('source_key', pa.string()), ('previous_id', pa.string()),
    ('previous_identity_key', pa.string()), ('redirected_ids', STRINGS),
    ('previous_choices', pa.list_(RESOLUTION)), ('previous_ref', ROW_REF)])
SUMMARY = pa.schema([('run', pa.string()), ('complete', pa.bool_()), ('concept_count', pa.int64()),
                     ('config_json', pa.large_string()), ('output', REF),
                     ('schema_version', pa.string()), ('rules_version', pa.string()), ('identity_registry', REF), ('identity_registry_input', REF)])
