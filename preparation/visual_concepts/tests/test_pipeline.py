"""隔离的真实 Lance 读写：身份、择值、原记录保全与快照更新。"""

import lance
import pyarrow as pa
import pytest
from demiflow import data
from preparation.visual_concepts.visual_concepts_pipeline import config, run_pipeline
from preparation.visual_concepts.operators.schema import (
    QID, QID_REVIEW, LEGACY, ALIGNMENTS, CHOICES, LEGACY_SELECTION, POSITIVE_IMAGES,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(tmp_path))
    return tmp_path


def write(root, name, rows, schema):
    uri = str(root / (name + '.lance'))
    receipt = data.from_arrow(pa.Table.from_pylist(rows, schema=schema)).write_lance(
        uri, schema=schema, mode='overwrite', return_receipt=True)
    return {'uri': uri, 'version': receipt.committed_version}


def read(root, result):
    ref = result['output']
    return data.read_lance(str(root / ref['uri']), version=ref['version']).take_all()


def old(cid='old1', assessment_id='a1', name='古筝', definition='审定定义', task_status='hold'):
    return {'concept_id': cid, 'source_record_id': 'source-' + cid, 'original_name': name,
            'aliases': ['旧别名'], 'status': 'assessed', 'reason': '原执行理由',
            'assessment_id': assessment_id,
            'assessment': {'identity_status': 'resolved', 'canonical_name': name,
                'definition': definition, 'qualifiers': [], 'task_status': task_status,
                'core_facts': [{'fact_id': 'F1', 'statement': '审定事实', 'evidence_ids': ['E1']}],
                'identity_evidence_ids': ['E1'], 'reason': '原业务理由'},
            'documents': [{'url': 'https://fixture.invalid/doc', 'status': 'failed', 'reason': 'receipt retained'}],
            'rounds': [{'round': 1, 'status': 'assessed', 'reason': '完整历史', 'call': '{"call":1}'}],
            'raw_taxonomy': '原分类未改写', 'adoption_status': None}


def qid(q='Q1', name='筝'):
    return {'qid': q, 'name_zh': name, 'name_en': 'Zither', 'membership_sources': ['fat'],
        'p18': ['File:Instrument.jpg'], 'p373': ['Instruments'], 'names': [],
        'zh_page_id': 42, 'zh_title': name,
        'zh_document': {'status': 'matched', 'document_id': 'doc1', 'revision_id': 'r1',
            'content_sha256': 'content-hash', 'is_redirect': False, 'is_disambig': False,
            'candidates': [{'document_id': 'doc1', 'revision_id': 'r1', 'source_ref': {'source_id': 'docs', 'record_key': 'doc1'}}]},
        'p18_candidates': [{'filename': 'Instrument.jpg', 'match_key': 'Instrument.jpg',
            'parse_status': 'parsed', 'match_status': 'single_candidate',
            'origins': [{'raw_value': 'File:Instrument.jpg', 'source_ref': {'source_id': 'fat', 'record_key': q}}],
            'matches': [{'sha256': 'image-sha', 'has_existing_qid_relation': True, 'ref_ids': ['r1']}]}],
        'p18_matched_sha256s': ['image-sha'], 'image_sha256s': ['image-sha', 'other-sha'], 'image_n': 2,
        'xref_special_values': [{'prop': 'P1', 'status': 'somevalue', 'origins': []}],
        'xref_special_n': 1, 'audit_flags': [{'code': 'fixture', 'message': '原审计保留'}]}


def review(q='Q1', decision='candidate', status='reviewed'):
    return {'qid': q, 'name': '筛选时名称', 'status': status, 'reason': 'execution',
            'review': {'decision': decision, 'reason': '核心形态可考察'},
            'context_json': '{"metadata":"保留"}', 'documents_json': '[{"id":"reference"}]',
            'document_count': 1, 'reading_json': '{"blocks":[1]}', 'payload_json': '{"full":true}',
            'call_json': '{"original_call":true}', 'prompt_tokens': 17}


def alignment(cid='old1', aid='a1', q='Q1', relation='exact'):
    return {'source_key': 'legacy:' + cid, 'target_qid': q, 'assessment_id': aid,
            'relation': relation, 'status': 'confirmed', 'evidence_refs': ['identity:E1'], 'reason': 'fixture identity'}


def base_config(root, *, olds=None, qids=None, reviews=None, links=None, **kwargs):
    return config(run='fixture', qid_source=write(root, 'qids', qids if qids is not None else [qid()], QID),
        qid_review_source=write(root, 'reviews', reviews if reviews is not None else [review()], QID_REVIEW),
        legacy_sources=[write(root, 'legacy', olds if olds is not None else [old()], LEGACY)],
        alignment_source=write(root, 'alignments', links if links is not None else [alignment()], ALIGNMENTS),
        read_batch_size=1, **kwargs)


def test_exact_merge_candidates_and_full_source_rows_roundtrip(root):
    cfg = base_config(root)
    result = run_pipeline(cfg)
    assert result['complete'] and result['concept_count'] == 1 and result['requests_issued'] == 0
    row = read(root, result)[0]
    assert row['canonical_name'] == '古筝' and row['definition'] == '审定定义'
    assert row['task_status'] == 'hold' and row['visual_value_decision'] == 'candidate'
    assert row['primary_document']['document']['revision_id'] == 'r1'
    assert row['primary_image']['sha256'] == 'image-sha'
    assert row['primary_image']['basis'] == 'source_declared'
    assert {c['value'] for c in row['canonical_name_candidates']} >= {'古筝', '筝', 'Zither', '筛选时名称'}
    assert [c['value'] for c in row['canonical_name_candidates'] if c['selected']] == ['古筝']
    for kind, ref, output in [('qid', cfg['qid_source'], 'qid_records'),
                             ('qid_review', cfg['qid_review_source'], 'qid_review_records'),
                             ('legacy', cfg['legacy_sources'][0], 'legacy_records')]:
        source = data.read_lance(**ref).take_all()[0]
        assert row[output][0]['value'] == source, kind
        assert row[output][0]['present_fields'] == sorted(source)
    assert row['image_sha256s'] == ['image-sha', 'other-sha']


def test_same_name_related_mapping_and_unreviewed_qid_do_not_merge(root):
    cfg = base_config(root, olds=[old(name='筝')],
        qids=[qid(), qid('Q2', '拒绝项'), qid('Q3', '失败项')],
        reviews=[review(), review('Q2', 'low_priority'), review('Q3', status='invalid_response')],
        links=[alignment(relation='related')])
    rows = read(root, run_pipeline(cfg))
    assert len(rows) == 2
    assert {r['identity_key'] for r in rows} == {'qid:Q1', 'legacy:old1'}
    assert next(r for r in rows if r['qid'] is None)['identity_links'][0]['value']['relation'] == 'related'


def test_equal_priority_conflict_and_explicit_choice_keep_all_values(root):
    cfg = base_config(root, olds=[old(), old('old2', 'a2', definition='另一个定义')],
                      links=[alignment(), alignment('old2', 'a2')])
    first = run_pipeline(cfg)
    row = read(root, first)[0]
    assert row['definition'] is None and 'definition' in row['conflict_fields']
    wanted = next(c for c in row['definition_candidates'] if c['value'] == '另一个定义')
    choice = write(root, 'choices', [{'identity_key': 'qid:Q1', 'field': 'definition',
                                    'candidate_id': wanted['candidate_id'], 'reason': '显式修订选择'}], CHOICES)
    second = run_pipeline({**cfg, 'choice_source': choice})
    new = read(root, second)[0]
    assert new['concept_id'] == row['concept_id'] and new['definition'] == '另一个定义'
    assert len(new['definition_candidates']) == 2 and len(new['legacy_records']) == 2
    assert new['previous_records'][0]['version'] == first['output']['version']
    assert read(root, first)[0]['definition'] is None
    retained = read(root, run_pipeline(cfg))[0]
    assert retained['definition'] == '另一个定义'
    assert next(r for r in retained['resolutions'] if r['field'] == 'definition')['rule'] == 'retained_explicit_choice'


def test_final_legacy_selection_preserves_hold_and_excludes_obsolete_ready(root):
    ready = write(root, 'ready', [old(assessment_id='old-ready', definition='旧值', task_status='ready')], LEGACY)
    hold = write(root, 'hold', [old(assessment_id='final-hold', definition='新值')], LEGACY)
    selected = write(root, 'selected', [{'source_uri': hold['uri'], 'source_version': hold['version'],
                                      'concept_id': 'old1', 'assessment_id': 'final-hold'}], LEGACY_SELECTION)
    cfg = config(run='selection', legacy_sources=[ready, hold], legacy_selection_source=selected)
    row = read(root, run_pipeline(cfg))[0]
    assert row['definition'] == '新值' and row['task_status'] == 'hold'
    assert len(row['legacy_records']) == 1


def test_identity_enrichment_keeps_id_and_scope_shrink_does_not_resurrect(root):
    cfg = base_config(root, qids=[], reviews=[], links=[])
    first = run_pipeline(cfg)
    cid = read(root, first)[0]['concept_id']
    qref = write(root, 'qids', [qid()], QID)
    rref = write(root, 'reviews', [review()], QID_REVIEW)
    links = write(root, 'alignments', [alignment()], ALIGNMENTS)
    second = run_pipeline({**cfg, 'qid_source': qref, 'qid_review_source': rref, 'alignment_source': links})
    assert read(root, second)[0]['concept_id'] == cid
    excluded = write(root, 'reviews', [review(decision='low_priority')], QID_REVIEW)
    empty = run_pipeline({**cfg, 'qid_source': qref, 'qid_review_source': excluded,
                          'legacy_sources': [], 'alignment_source': None})
    assert empty['concept_count'] == 0 and read(root, empty) == []
    assert len(read(root, second)) == 1
    restored = run_pipeline({**cfg, 'qid_source': qref, 'qid_review_source': rref, 'alignment_source': links})
    assert read(root, restored)[0]['concept_id'] == cid


def test_stale_identity_mapping_and_unknown_nested_field_fail_before_publish(root):
    cfg = base_config(root, links=[alignment(aid='wrong-assessment')])
    with pytest.raises(ValueError, match='different assessment'):
        run_pipeline(cfg)
    assert not (root / cfg['target_uri']).exists()
    extra = pa.schema([*QID, ('new_business_field', pa.string())])
    source = write(root, 'unknown', [{**qid(), 'new_business_field': 'must not disappear'}], extra)
    with pytest.raises(ValueError, match='Unmapped source field'):
        run_pipeline({**cfg, 'qid_source': source})


def test_primary_candidates_keep_ambiguity_and_positive_image_assessment_binding(root):
    value = qid()
    value['zh_document']['status'] = 'ambiguous'
    value['p18_candidates'][0]['match_status'] = 'multiple_candidates'
    positives = write(root, 'positives', [
        {'concept_id': 'old1', 'assessment_id': 'a1', 'sha256': 'good', 'image_uri': 'file:///good.jpg', 'selection_rank': 1},
        {'concept_id': 'old1', 'assessment_id': 'obsolete', 'sha256': 'stale', 'image_uri': 'file:///stale.jpg', 'selection_rank': 0},
    ], POSITIVE_IMAGES)
    cfg = base_config(root, qids=[value], related_sources=[{'kind': 'positive_images', 'source': positives}])
    row = read(root, run_pipeline(cfg))[0]
    assert row['primary_document'] is None and len(row['primary_document_candidates']) == 1
    assert row['primary_image']['sha256'] == 'good'
    assert len(row['primary_image_candidates']) == 3 and len(row['positive_images_records']) == 2


def test_group_budget_fails_without_dropping_values(root):
    cfg = base_config(root, max_group_bytes=32)
    with pytest.raises(ValueError, match='max_group_bytes'):
        run_pipeline(cfg)
    assert not (root / cfg['target_uri']).exists()


def test_unknown_nested_source_column_is_not_silently_projected(root):
    cfg = base_config(root)
    fields = [pa.field(f.name, pa.struct([*list(f.type), ('new_flag', pa.bool_())]))
              if f.name == 'zh_document' else f for f in QID]
    value = qid()
    value['zh_document']['new_flag'] = True
    source = write(root, 'nested_unknown', [value], pa.schema(fields))
    with pytest.raises(ValueError, match='zh_document.new_flag'):
        run_pipeline({**cfg, 'qid_source': source})


def test_exact_old_admission_keeps_qid_low_priority_and_metadata(root):
    cfg = base_config(root, reviews=[review(decision='low_priority')])
    row = read(root, run_pipeline(cfg))[0]
    assert row['qid'] == 'Q1' and row['task_status'] == 'hold'
    assert row['visual_value_decision'] == 'low_priority'
    assert row['qid_records'][0]['value']['p18'] == ['File:Instrument.jpg']
    assert row['qid_review_records'][0]['value']['review']['decision'] == 'low_priority'


def test_final_selection_missing_record_is_not_silently_dropped(root):
    source = write(root, 'legacy', [old()], LEGACY)
    selected = write(root, 'selected', [{'source_uri': source['uri'], 'source_version': source['version'],
                                      'concept_id': 'missing', 'assessment_id': 'not-found'}], LEGACY_SELECTION)
    cfg = config(run='missing', legacy_sources=[source], legacy_selection_source=selected)
    with pytest.raises(ValueError, match='missing record'):
        run_pipeline(cfg)
    assert not (root / cfg['target_uri']).exists()


def test_registry_failure_does_not_publish_success_and_rerun_recovers(root, monkeypatch):
    from demiflow.data.dataset import Dataset
    source = write(root, 'legacy', [old()], LEGACY)
    cfg = config(run='recover', legacy_sources=[source])
    original = Dataset.write_lance
    def fail_registry(self, uri, **kwargs):
        if str(uri).endswith('__identities.lance'):
            raise OSError('fixture registry commit failure')
        return original(self, uri, **kwargs)
    with monkeypatch.context() as isolated:
        isolated.setattr(Dataset, 'write_lance', fail_registry)
        with pytest.raises(OSError, match='registry commit failure'):
            run_pipeline(cfg)
    assert (root / cfg['target_uri']).exists()
    assert not (root / 'demiwtg/preparation/visual_concepts/datasets/summary__recover.lance').exists()
    before = data.read_lance(str(root / cfg['target_uri']), version=1).take_all()[0]['concept_id']
    result = run_pipeline(cfg)
    assert result['complete'] and read(root, result)[0]['concept_id'] == before
    assert result['identity_registry']['version'] == 1
