"""针对改选、身份继承及局部字段语义的纯行回归，不重复全量数据校验。"""

import pytest
from preparation.visual_concepts.operators import merge as m
from preparation.visual_concepts.tests.test_pipeline import old, qid, review, alignment

SOURCE = {'uri': 'fixture.lance', 'version': 1}


def legacy_member(value, links=()):
    member = m.legacy_member(m.legacy_input(value, source=SOURCE))
    member['alignment_rows'] = [m.alignment_row(a, source=SOURCE) for a in links]
    return m.identity_member(member)


def grouped(members, choices=()):
    return {'identity_key': members[0]['identity_key'], 'members': members,
            'choices': [m.choice_row(c, source=SOURCE) for c in choices]}


def finalize(members, choices=()):
    return m.finish_concept(grouped(members, choices), resource_sources=[])


def test_explicit_choice_retains_source_and_fails_when_value_disappears():
    members = [legacy_member(old(), [alignment()]),
               legacy_member(old('old2', 'a2', definition='另一个定义'), [alignment('old2', 'a2')])]
    first = finalize(members)
    wanted = next(c for c in first['definition_candidates'] if c['value'] == '另一个定义')
    choice = {'identity_key': 'qid:Q1', 'field': 'definition', 'candidate_id': wanted['candidate_id'], 'reason': '人工选择说明'}
    chosen = finalize(members, [choice])
    previous = m.previous_members(chosen, source=SOURCE)
    for member in members:
        member['previous_rows'] = [p for p in previous if p['source_key'] == member['source_key']]
    retained = finalize(members)
    assert retained['definition'] == '另一个定义'
    resolution = next(r for r in retained['resolutions'] if r['field'] == 'definition')
    assert resolution['choice_kind'] == 'explicit' and resolution['rule'] == 'retained_explicit_choice'
    assert resolution['reason'] == '人工选择说明' and resolution['choice_source']['uri'] == 'fixture.lance'
    with pytest.raises(ValueError, match='Previous explicit choice is stale'):
        finalize(members[:1])
    cleared = finalize(members[:1], [{**choice, 'candidate_id': None, 'reason': '重新审阅前暂不选取'}])
    assert cleared['definition'] is None and not any(c['selected'] for c in cleared['definition_candidates'])


def test_two_existing_identities_merge_and_keep_transitive_redirects():
    left, right = legacy_member(old()), legacy_member(old('old2', 'a2'))
    prior_left, prior_right = finalize([left]), finalize([right])
    prior_left['redirected_concept_ids'] = ['vc_previous_alias']
    combined = []
    for value, link, prior in [(old(), alignment(), prior_left), (old('old2', 'a2'), alignment('old2', 'a2'), prior_right)]:
        member = legacy_member(value, [link])
        member['previous_rows'] = m.previous_members(prior, source=SOURCE)
        combined.append(m.identity_member(member))
    merged = finalize(combined)
    ids = sorted([prior_left['concept_id'], prior_right['concept_id']])
    assert merged['concept_id'] == ids[0]
    assert set(merged['redirected_concept_ids']) == {ids[1], 'vc_previous_alias'}
    assert len(merged['previous_records']) == 2
    # 已发布的 QID 身份不能因遗漏 alignment 配置而拆成两个仍使用同一个 ID 的概念。
    source_member = legacy_member(old())
    source_member['previous_rows'] = [p for p in m.previous_members(merged, source=SOURCE)
                                      if p['source_key'] == 'legacy:old1']
    with pytest.raises(ValueError, match='cannot be split/remapped'):
        m.identity_member(source_member)


def test_same_value_merges_origins_and_empty_qualifiers_remain_a_value():
    first = legacy_member(old(), [alignment()])
    second = legacy_member(old('old2', 'a2'), [alignment('old2', 'a2')])
    merged = finalize([first, second])
    assert len(merged['definition_candidates']) == 1
    assert len(merged['definition_candidates'][0]['sources']) == 2
    assert merged['qualifiers'] == [] and merged['qualifiers_candidates'][0]['selected']
    assert merged['legacy_records'][0]['value']['adoption_status'] is None
    # 无法将两个记录的 F1/E1 混用；事实保留独立来源。
    assert len(merged['core_facts']) == 2


def test_multiple_final_assessments_for_same_legacy_id_fail():
    with pytest.raises(ValueError, match='Multiple final assessments'):
        finalize([legacy_member(old()), legacy_member(old(assessment_id='a2'))])


def test_missing_and_ineligible_final_selection_fail():
    selected = {'concept_id': 'missing', '_source_uri': 'x.lance', '_source_version': 1}
    with pytest.raises(ValueError, match='missing record'):
        m.legacy_member(selected, require_selected=True)
    bad = old()
    bad['status'] = 'invalid_response'
    with pytest.raises(ValueError, match='ineligible'):
        m.legacy_member(m.legacy_input(bad, source=SOURCE), require_selected=True)


def test_conflicting_retained_explicit_choices_need_new_decision():
    members = [legacy_member(old(), [alignment()]), legacy_member(old('old2', 'a2', definition='另一个定义'), [alignment('old2', 'a2')])]
    row = finalize(members)
    for index, member in enumerate(members):
        candidate = row['definition_candidates'][index]
        chosen = finalize(members, [{'identity_key': 'qid:Q1', 'field': 'definition',
                                    'candidate_id': candidate['candidate_id'], 'reason': '选择 ' + str(index)}])
        member['previous_rows'] = [p for p in m.previous_members(chosen, source=SOURCE)
                                  if p['source_key'] == member['source_key']]
    with pytest.raises(ValueError, match='conflicting explicit choices'):
        finalize(members)


def test_latest_snapshot_of_same_identity_wins_over_older_member_choice():
    members = [legacy_member(old(), [alignment()]), legacy_member(old('old2', 'a2', definition='另一个定义'), [alignment('old2', 'a2')])]
    first = finalize(members)
    decisions = []
    for candidate in first['definition_candidates']:
        decisions.append(finalize(members, [{'identity_key': 'qid:Q1', 'field': 'definition',
            'candidate_id': candidate['candidate_id'], 'reason': '修订选择'}]))
    for index, member in enumerate(members):
        member['previous_rows'] = [p for p in m.previous_members(decisions[index], source={**SOURCE, 'version': index + 1})
                                  if p['source_key'] == member['source_key']]
    assert finalize(members)['definition'] == decisions[1]['definition']


def test_reintroduced_alias_uses_canonical_identity_and_current_choice():
    old_member = legacy_member(old(), [alignment()])
    alias = finalize([old_member])
    alias['concept_id'] = 'vc_z_alias'
    selected = alias['definition_candidates'][0]['candidate_id']
    current = finalize([old_member], [{'identity_key': 'qid:Q1', 'field': 'definition',
                                     'candidate_id': selected, 'reason': '现行选择'}])
    current['concept_id'] = 'vc_a_current'
    current['redirected_concept_ids'] = ['vc_z_alias']
    row = legacy_member(old(), [alignment()])
    row['previous_rows'] = [p for p in m.previous_members(alias, source=SOURCE) if p['source_key'] == 'legacy:old1']
    row['identity_previous_rows'] = [p for p in m.previous_members(current, source={**SOURCE, 'version': 2})
                                     if p['source_key'] == 'qid:Q1']
    result = finalize([m.combine_previous(row)])
    assert result['concept_id'] == 'vc_a_current'
    assert result['definition'] == current['definition']
    assert 'vc_z_alias' in result['redirected_concept_ids']
