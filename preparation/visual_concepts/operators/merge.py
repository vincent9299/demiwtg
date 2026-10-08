"""显式身份对齐、完整原记录保留及候选择值；本文件不读取业务表。"""
import hashlib
import json
import re

from .schema import VALUE_TYPES, SOURCE_SCHEMAS, SCHEMA_VERSION, RULES_VERSION


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def row_ref(source, kind, key):
    return {**source, 'record_key': str(key), 'source_kind': kind}


def snapshot(row, source, kind, key):
    return {'source': row_ref(source, kind, key), 'present_fields': sorted(row), 'value': row}


def group_rows(state, row, *, key, output, max_group_bytes):
    """热点组超预算报错，不截断候选或来源。预算为 UTF-8 编码字节，不是 RSS。"""
    if state is None:
        state = {key: row[key], output: [], '_' + output + '_bytes': 0}
    size = len(canonical(row).encode())
    size_field = '_' + output + '_bytes'
    if state[size_field] + size > max_group_bytes:
        raise ValueError('Concept group exceeds max_group_bytes: ' + str(row[key]))
    state[size_field] += size
    state[output].append(row)
    return state


def qid_review_row(row, *, source):
    return {'qid': row['qid'], 'review_snapshot': snapshot(row, source, 'qid_review', row['qid'])}


def qid_member(row, *, source):
    qid = row['qid']
    if not re.fullmatch(r'Q[1-9][0-9]*', qid):
        raise ValueError('Invalid QID: ' + str(qid))
    value = row.get('qid_value')
    if value is None:
        raise ValueError('Reviewed QID has no source concept: ' + qid)
    return {'source_key': 'qid:' + qid, 'origin_kind': 'qid', 'qid': qid,
            'record': snapshot(value, source, 'qid', qid),
            'qid_reviews': [r['review_snapshot'] for r in row.get('review_rows') or []]}


def qid_members(row, *, source):
    if row.get('qid_value') is None and not row['candidate_admission']:
        return []
    return [qid_member(row, source=source)]


def qid_value(row):
    return {'qid': row['qid'], 'qid_value': row}


def legacy_input(row, *, source):
    return {'_source_uri': source['uri'], '_source_version': source['version'],
            'concept_id': row['concept_id'], 'assessment_id': row['assessment_id'],
            'legacy_value': row}


def legacy_selection(row):
    return {'_source_uri': row['source_uri'], '_source_version': row['source_version'],
            'concept_id': row['concept_id'], 'assessment_id': row['assessment_id']}


def legacy_member(row, *, require_selected=False):
    value = row.get('legacy_value')
    if value is None:
        raise ValueError('Final legacy selection names a missing record: ' + str(row.get('concept_id')))
    if require_selected and not admitted_legacy(value):
        raise ValueError('Final legacy selection names an ineligible record: ' + str(row.get('concept_id')))
    source = {'uri': row['_source_uri'], 'version': row['_source_version']}
    if not value.get('concept_id') or not value.get('assessment_id'):
        raise ValueError('Legacy identity requires concept_id and assessment_id')
    return {'source_key': 'legacy:' + value['concept_id'], 'origin_kind': 'legacy', 'qid': None,
            'record': snapshot(value, source, 'legacy', value['assessment_id']), 'qid_reviews': []}


def admitted_legacy(row):
    return row.get('status') == 'assessed' and (row.get('assessment') or {}).get('identity_status') == 'resolved'


def admitted_qid(row):
    return row.get('status') == 'reviewed' and (row.get('review') or {}).get('decision') == 'candidate'


def alignment_row(row, *, source):
    if not row.get('source_key') or not row['source_key'].startswith('legacy:'):
        raise ValueError('Alignment source_key must be legacy:<concept_id>')
    if not re.fullmatch(r'Q[1-9][0-9]*', row.get('target_qid') or ''):
        raise ValueError('Alignment requires a valid target_qid')
    if row.get('relation') not in {'exact', 'related', 'broader', 'narrower'}:
        raise ValueError('Unknown identity relation')
    if row.get('status') not in {'confirmed', 'candidate', 'rejected'}:
        raise ValueError('Unknown identity alignment status')
    if row['status'] == 'confirmed' and row['relation'] == 'exact':
        if not row.get('assessment_id') or not row.get('evidence_refs'):
            raise ValueError('Confirmed exact mapping requires bound assessment and evidence')
    return {'source_key': row['source_key'],
            'alignment': snapshot(row, source, 'alignment', digest(row))}


def related_row(row, *, source, kind):
    return {'source_key': 'legacy:' + row['concept_id'],
            'attachment': snapshot(row, source, kind, digest(row))}


def previous_members(row, *, source, priority=0):
    keys = sorted({m['source_key'] for m in row['source_members']} | {row['identity_key']})
    return [{'source_key': key, 'previous_id': row['concept_id'],
             'previous_identity_key': row['identity_key'], 'redirected_ids': row['redirected_concept_ids'],
             'previous_choices': [r for r in row.get('resolutions') or [] if r.get('choice_kind') == 'explicit'],
             'previous_ref': row_ref(source, 'visual_concepts', row['concept_id']), '_priority': priority}
            for key in keys]


def registry_previous(row):
    return {**row, '_priority': 1}


def latest_previous(state, row):
    # 当前目标快照优先于登记表，处理概念表已提交而登记表未提交的恢复情况。
    if state is None or row['_priority'] < state['_priority']:
        return row
    if row['_priority'] == state['_priority'] and row != state:
        raise ValueError('Source identity has contradictory previous bindings: ' + row['source_key'])
    return state


def identity_previous_group(row):
    return {'identity_key': row['source_key'], 'identity_previous_rows': row['previous_rows']}


def combine_previous(row):
    return {**row, 'previous_rows': _unique((row.get('previous_rows') or []) + (row.get('identity_previous_rows') or []))}


def registry_value(row):
    return {key: value for key, value in row.items() if key != '_priority'}


def identity_member(row):
    """只有绑定当前审定版本的 confirmed/exact 能改变分组键。"""
    links = [r['alignment'] for r in row.get('alignment_rows') or []]
    exact = [r['value'] for r in links if r['value']['relation'] == 'exact' and r['value']['status'] == 'confirmed']
    if exact:
        if any(r['assessment_id'] != row['record']['value'].get('assessment_id') for r in exact):
            raise ValueError('Exact mapping binds a different assessment: ' + row['source_key'])
        targets = {r['target_qid'] for r in exact}
        if len(targets) != 1:
            raise ValueError('Conflicting exact QID mappings: ' + row['source_key'])
        qid = next(iter(targets))
    else:
        qid = row.get('qid')
    key = 'qid:' + qid if qid else row['source_key']
    for prior in row.get('previous_rows') or []:
        if prior['previous_identity_key'].startswith('qid:') and prior['previous_identity_key'] != key:
            raise ValueError('Published identity cannot be split/remapped implicitly: ' + row['source_key'])
    return {**row, 'identity_key': key,
            'identity_links': links, 'resolved_qid': qid}


def choice_row(row, *, source):
    if row.get('field') not in VALUE_TYPES or not row.get('reason'):
        raise ValueError('Field choice requires a supported field and reason')
    # QID determines identity; it cannot be changed by a presentation override.
    if row['field'] == 'qid':
        raise ValueError('Change QID through identity alignment, not a field choice')
    if not isinstance(row.get('identity_key'), str) or not row['identity_key']:
        raise ValueError('Choice requires identity_key')
    return {**row, 'choice_source': row_ref(source, 'choice', digest(row))}


def resolved_qid_row(row):
    return {'qid': row['resolved_qid'], 'candidate_admission': False}


def admitted_qid_key(row):
    return {'qid': row['qid'], 'candidate_admission': True}


def merge_qid_key(state, row):
    return {'qid': row['qid'], 'candidate_admission': row['candidate_admission'] or bool(state and state['candidate_admission'])}


def known_qid(row):
    return row.get('resolved_qid') is not None


def legacy_admitted_input(row):
    return admitted_legacy(row['legacy_value'])


def _unique(values):
    return [v for _, v in sorted({canonical(v): v for v in values}.items())]


def finish_concept(row, *, resource_sources):
    members = row['members']
    out = {f: None for f in VALUE_TYPES}
    buckets = {f: {} for f in VALUE_TYPES}
    records = {k: [] for k in SOURCE_SCHEMAS}
    names, facts, properties, relations, image_ids, source_members, links, previous = [], [], [], [], set(), [], [], []
    legacy_identities = {}
    for member in members:
        if member['origin_kind'] == 'legacy':
            legacy_identities.setdefault(member['source_key'], set()).add(member['record']['value']['assessment_id'])
    if any(len(ids) > 1 for ids in legacy_identities.values()):
        raise ValueError('Multiple final assessments for one legacy concept; provide exact final selection')
    p18_shas = {sha for member in members if member['origin_kind'] == 'qid'
                for sha in member['record']['value'].get('p18_matched_sha256s') or []}

    def add(field, value, ref, path, priority=10, eligible=True, rank=0):
        if value is None or value == '':
            return
        key = digest([field, value])
        origin = {'record': ref, 'field': path}
        candidate = buckets[field].setdefault(key, {
            'candidate_id': key, 'value': value, 'sources': [], 'priority': priority, 'rank': rank,
            'eligible': False, 'selected': False, 'selection_reason': None})
        candidate['sources'].append(origin)
        if eligible:
            if not candidate['eligible'] or (priority, rank) < (candidate['priority'], candidate['rank']):
                candidate['priority'], candidate['rank'] = priority, rank
            candidate['eligible'] = True

    def name(text, language, kind, ref, path):
        if text:
            names.append({'text': text, 'language': language, 'kind': kind,
                          'sources': [{'record': ref, 'field': path}]})

    for m in members:
        raw, ref = m['record']['value'], m['record']['source']
        kind = m['origin_kind']
        records[kind].append(m['record'])
        source_members.append({'source_key': m['source_key'], 'source': ref})
        links.extend(m['identity_links'])
        previous.extend(m.get('previous_rows') or [])
        for r in m.get('qid_reviews') or []:
            records['qid_review'].append(r)
            review = r['value'].get('review')
            if r['value'].get('status') == 'reviewed' and review:
                add('visual_value_decision', review['decision'], r['source'], 'review.decision')
                add('visual_value_reason', review['reason'], r['source'], 'review.reason')
            add('canonical_name', r['value'].get('name'), r['source'], 'name', 40)
        if m['resolved_qid']:
            qref = ref if kind == 'qid' else next(x['source'] for x in m['identity_links']
                if x['value']['relation'] == 'exact' and x['value']['status'] == 'confirmed')
            add('qid', m['resolved_qid'], qref, 'qid' if kind == 'qid' else 'target_qid')
        if kind == 'qid':
            for lang, priority in [('zh', 20), ('en', 30)]:
                add('canonical_name', raw.get('name_' + lang), ref, 'name_' + lang, priority)
                add('name_' + lang, raw.get('name_' + lang), ref, 'name_' + lang)
                name(raw.get('name_' + lang), lang, raw.get('name_' + lang + '_kind'), ref, 'name_' + lang)
                doc = raw.get(lang + '_document')
                if doc:
                    add('primary_document', {'language': lang, 'page_id': raw.get(lang + '_page_id'),
                        'title': raw.get(lang + '_title'), 'document': doc}, ref, lang + '_document', priority,
                        doc.get('status') == 'matched' and bool(doc.get('document_id'))
                        and doc.get('is_redirect') is not True and doc.get('is_disambig') is not True)
            for n in raw.get('names') or []:
                name(n['text'], n['language'], n['kind'], ref, 'names')
            for target, field in [(properties, 'xrefs'), (relations, 'relations')]:
                target.extend({'value': v, 'sources': [{'record': ref, 'field': field}]} for v in raw.get(field) or [])
            add('classification', raw.get('classification'), ref, 'classification', eligible=(raw.get('classification') or {}).get('status') == 'mapped')
            image_ids.update(raw.get('image_sha256s') or [])
            for p in raw.get('p18_candidates') or []:
                for match in p.get('matches') or []:
                    add('primary_image', {'sha256': match['sha256'], 'image_uri': None,
                        'filename': p['filename'], 'basis': 'source_declared'}, ref, 'p18_candidates', 30,
                        p.get('parse_status') == 'parsed' and p.get('match_status') == 'single_candidate')
        else:
            a = raw['assessment']
            for field in ('canonical_name', 'definition', 'concept_kind', 'qualifiers', 'taxon_rank',
                          'name_relation', 'identity_status', 'task_status', 'task_sketch'):
                add(field, a.get(field), ref, 'assessment.' + field)
            add('canonical_name', raw.get('original_name'), ref, 'original_name', 50)
            name(raw.get('original_name'), None, 'original_name', ref, 'original_name')
            name(a.get('canonical_name'), None, 'canonical_name', ref, 'assessment.canonical_name')
            for alias in raw.get('aliases') or []:
                name(alias, None, 'alias', ref, 'aliases')
            facts.extend({'value': v, 'sources': [{'record': ref, 'field': 'assessment.core_facts'}]}
                         for v in a.get('core_facts') or [])
        for attachment in m.get('attachment_rows') or []:
            s = attachment['attachment']
            records[s['source']['source_kind']].append(s)
            v = s['value']
            if v.get('sha256'):
                image_ids.add(v['sha256'])
            if s['source']['source_kind'] == 'positive_images':
                eligible = v.get('assessment_id') == raw.get('assessment_id') and bool(v.get('image_uri'))
                add('primary_image', {'sha256': v['sha256'], 'image_uri': v.get('image_uri'),
                    'filename': None, 'basis': 'reviewed_positive'}, s['source'], 'sha256',
                    10 if v['sha256'] in p18_shas else 20, eligible,
                    v['selection_rank'] if v.get('selection_rank') is not None else 2**63-1)

    # 多条旧审定归到同一概念时，不把相同 fact_id 跨记录当成同一个证据 ID。
    for field in buckets:
        for candidate in buckets[field].values():
            candidate['sources'] = _unique(candidate['sources'])
    choices = {}
    for item in row.get('choices') or []:
        if item['field'] in choices and choices[item['field']] != item:
            raise ValueError('Conflicting field choices for ' + item['field'])
        choices[item['field']] = item
    # 同一个旧统一概念的多个来源成员可能留在不同快照；人工选择采用该概念最新快照。
    latest_revisions = {}
    for p in previous:
        key = (p['previous_id'], p['previous_ref']['uri'])
        latest_revisions[key] = max(latest_revisions.get(key, 0), p['previous_ref']['version'])
    retired_ids = {cid for p in previous for cid in p.get('redirected_ids') or []}
    effective_previous = [p for p in previous if p['previous_id'] not in retired_ids and p['previous_ref']['version'] ==
                          latest_revisions[(p['previous_id'], p['previous_ref']['uri'])]]
    resolutions, conflicts = [], []
    for field, items in buckets.items():
        eligible = [v for v in items.values() if v['eligible']]
        best = [] if not eligible else [v for v in eligible if (v['priority'], v['rank']) == min((x['priority'], x['rank']) for x in eligible)]
        chosen, rule, choice_kind, reason, choice_source = None, 'source_priority_unique', 'automatic', None, None
        prior_choices = _unique([r for p in effective_previous for r in p.get('previous_choices') or [] if r['field'] == field])
        explicit = choices.get(field)
        if explicit is not None:
            rule, choice_kind, reason, choice_source = 'explicit_choice', 'explicit', explicit['reason'], explicit['choice_source']
            if explicit['candidate_id'] is not None:
                chosen = items.get(explicit['candidate_id'])
                if chosen is None or not chosen['eligible']:
                    raise ValueError('Choice names missing/ineligible candidate: ' + field)
        elif prior_choices:
            ids = {r['selected_id'] for r in prior_choices}
            if len(ids) != 1:
                raise ValueError('Merged concepts have conflicting explicit choices: ' + field)
            choice = prior_choices[0]
            rule, choice_kind, reason, choice_source = 'retained_explicit_choice', 'explicit', choice['reason'], choice['choice_source']
            if choice['selected_id'] is not None:
                chosen = items.get(choice['selected_id'])
                if chosen is None or not chosen['eligible']:
                    raise ValueError('Previous explicit choice is stale; provide a new choice: ' + field)
        elif len(best) == 1:
            chosen = best[0]
        if chosen:
            chosen['selected'], chosen['selection_reason'] = True, rule
            out[field] = chosen['value']
        status = 'selected' if chosen else 'unresolved' if best or choice_kind == 'explicit' else 'unavailable'
        if status == 'unresolved':
            conflicts.append(field)
        out[field + '_candidates'] = sorted(items.values(), key=lambda c: c['candidate_id'])
        resolutions.append({'field': field, 'status': status, 'selected_id': chosen['candidate_id'] if chosen else None, 'rule': rule, 'choice_kind': choice_kind, 'reason': reason, 'choice_source': choice_source})

    prior_ids = sorted({p['previous_id'] for p in previous})
    concept_id = prior_ids[0] if prior_ids else 'vc_' + digest(row['identity_key'])[:32]
    out.update(concept_id=concept_id, identity_key=row['identity_key'],
        schema_version=SCHEMA_VERSION, rules_version=RULES_VERSION,
        names=_unique(names), core_facts=_unique(facts), properties=_unique(properties), relations=_unique(relations),
        image_sha256s=sorted(image_ids), source_members=_unique(source_members), identity_links=_unique(links),
        resolutions=resolutions, conflict_fields=conflicts,
        redirected_concept_ids=sorted((set(prior_ids) | {v for p in previous for v in p.get('redirected_ids') or []}) - {concept_id}),
        previous_records=_unique([p['previous_ref'] for p in previous]),
        resource_sources=resource_sources)
    for kind, values in records.items():
        out[kind + '_records'] = _unique(values)
    return out
