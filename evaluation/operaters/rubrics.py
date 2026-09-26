"""Build source-grounded judge context, then freeze the prompt-reviewed rubric."""
import base64
import json
from pathlib import Path

from preparation.operaters.inputs import asset_for_item, eligible, pixels
from demiflow.execution.artifacts import digest, file_record, immutable, read
from preparation.operaters.runfiles import run_records, read_record
from evaluation.operaters.prompting import apply_native, prepare_native, template
from evaluation.operaters.scores import dimensions, validate_rubric


def evidence_for(row):
    """Use frozen authoring publication/evidence, never an answering arm's retrieval."""
    evidence, images, by_id, issues = [], [], {}, []
    for number, item in enumerate(row['authoring_materials'], 1):
        if not eligible(item):
            issues.append('Unpublished author material: ' + item.get('item_id', '?'))
            continue
        eid = 'S' + str(number)
        if item['item_id'] in by_id:
            raise ValueError('Duplicate author evidence ID')
        by_id[item['item_id']] = eid
        review = item.get('review', {})
        record = {'id': eid, 'knowledge_id': item['item_id'], 'kind': item['kind'],
                  'title': item.get('title', ''), 'text': item.get('text', ''),
                  'support_scope': review.get('support_scope', review.get('scope', '')),
                  'limitations': review.get('limitations', ''),
                  'sources': [{k: s[k] for k in ('source_id', 'title', 'url', 'text', 'sections',
                              'context_before', 'context_after', 'start', 'end', 'quote_basis') if k in s}
                              for s in item.get('sources', [])],
                  'references': item.get('references', [])}
        if item['kind'] == 'image':
            asset = asset_for_item(item)
            record['image_role'] = 'EVIDENCE_' + str(len(images) + 1)
            record['image_sha256'] = asset['sha256']
            images.append({'evidence_id': eid, 'label': record['image_role'], 'asset': asset})
        evidence.append(record)
    author = []
    for number, criterion in enumerate(row['criteria'], 1):
        required = criterion.get('knowledge_ids', [])
        missing = [k for k in required if k not in by_id]
        if missing:
            issues.append(f'A{number} missing source materials: ' + ', '.join(missing))
        author.append({**{k: v for k, v in criterion.items() if k not in {'evidence', 'knowledge_ids'}},
                       'source_criterion_id': f'A{number}',
                       'evidence_ids': [by_id[k] for k in required if k in by_id],
                       'missing_knowledge_ids': missing})
    return {'author_criteria': author, 'evidence': evidence, 'evidence_images': images,
            'material_issues': issues}


def image_values(assets, directory):
    """Anonymous content-bound pixels; filenames never expose an answer arm."""
    images, roles = [], []
    for number, (asset, role, label) in enumerate(assets, 1):
        raw, mime, _ = pixels(asset)
        images.append(f'data:{mime};base64,' + base64.b64encode(raw).decode())
        roles.append({'sha256': asset['sha256'], 'path': asset.get('path'),
                      **({'blob_ref': asset['blob_ref']} if asset.get('blob_ref') else {}),
                      'number': number, 'role': role, 'label': label})
    return images, roles


class PrepareRubric:
    def __init__(self, run, pack, config):
        self.run, self.pack, self.config = Path(run), pack, config

    def __call__(self, row):
        try:
            q = row['question']
            context = evidence_for(row)
            context['rubric_policy'] = self.config.get('rubric_policy', 'author_exact_v1')
            assets = []
            if q.get('edit_source'):
                assets.append((q['edit_source'], 'before', 'BEFORE'))
            assets += [(i['asset'], 'evidence', i['label']) for i in context['evidence_images']]
            images, roles = image_values(assets, self.run / 'rubric_inputs' / q['task_id'])
            public = {k: q[k] for k in ('task_type', 'edit_type', 'instruction')}
            payload = {'question': public, 'authoring_context': row['authoring_context'],
                       **{k: context[k] for k in ('author_criteria', 'evidence', 'material_issues')},
                       'dimensions': dimensions(q),
                       'image_catalog': [{k: r[k] for k in ('number', 'role', 'label')} for r in roles]}
            system, user = template(q['task_type'], q.get('edit_type'))
            # 协议随 task_type/edit_type 选择；固定 rubric 编制规则已在 YAML，无需重复写入行。
            values = {'judge_protocol': system + '\n' + user,
                      'payload': json.dumps(payload, ensure_ascii=False), 'images': images}
            return prepare_native({**row, 'rubric_context': context}, 'rubric', q['task_id'],
                                  values, roles, self.run, self.pack, self.config)
        except (ValueError, OSError, KeyError, TypeError) as error:
            return {**row, 'rubric_status': 'input_error', 'rubric_reason': str(error)}


class ApplyRubric:
    def __init__(self, run, config):
        self.run, self.config = Path(run), config

    def __call__(self, row):
        row, result = apply_native(row, 'rubric', self.run, self.config)
        if result is None:
            return row
        try:
            validate_rubric(result, row['question'], row['rubric_context'])
            if result['status'] != 'ready' or result['audit']['status'] != 'ok':
                return {**row, 'rubric_status': 'insufficient', 'rubric_review': result}
            context = row['rubric_context']
            packet = {'schema': 'v4-frozen-judge-packet/1', 'question': row['question'],
                      'question_sha256': digest(row['question']),
                      'rubric': {**result, **row['authoring_context']},
                      'rubric_policy': context.get('rubric_policy'),
                      'author_criteria': context['author_criteria'],
                      'evidence': context['evidence'], 'evidence_images': context['evidence_images'],
                      'material_issues': context['material_issues'],
                      'input_binding': row['rubric_binding'], 'provenance': row['rubric_provenance']}
            ref = run_records(self.run).put('rubric/' + row['question']['task_id'], packet)
            return {**row, 'rubric_status': 'frozen', 'judge_packet': {'record_ref': ref.to_dict(), 'sha256': digest(packet)}}

        except (ValueError, KeyError, TypeError, OSError, AttributeError, IndexError) as error:
            return {**row, 'rubric_status': 'invalid_response', 'rubric_reason': str(error)}


def freeze_rubrics(run, rows):
    """No candidate may be generated before all question packets are frozen."""
    if any(r.get('rubric_status') != 'frozen' for r in rows):
        return False
    index = {r['question']['task_id']: r['judge_packet'] for r in rows}
    if not index or len(index) != len(rows):
        raise ValueError('Missing or duplicate rubric packets')
    run_records(run).put('rubrics_index', index)
    verify_rubrics(run)
    return True


def verify_rubrics(run):
    from evaluation.operaters.contracts import frozen_stage
    from preparation.operaters.runfiles import rows
    index = run_records(run).get('rubrics_index')
    if index is None: raise ValueError('Frozen rubric index missing')
    questions = {r['question']['task_id']: r['question'] for r in rows(frozen_stage(run, 'questions')['dataset_ref'])}
    if set(index) != set(questions):
        raise ValueError('Rubrics must cover every frozen question')
    for qid, entry in index.items():
        packet = read_record(entry['record_ref'])
        if digest(packet) != entry['sha256']: raise ValueError('Frozen rubric packet changed')
        if packet['question']['task_id'] != qid or packet['question_sha256'] != digest(packet['question']):
            raise ValueError('Rubric question binding changed')
        if packet['question'] != questions[qid]:
            raise ValueError('Rubric differs from original question')
        if packet.get('rubric_policy') == 'author_exact_v1':
            validate_rubric(packet['rubric'], packet['question'],
                            {'rubric_policy': packet['rubric_policy'],
                             'author_criteria': packet['author_criteria'], 'evidence': packet['evidence']})
        request = read_record(packet['input_binding']['request_ref'])
        if digest(request['messages']) != packet['input_binding']['input_sha256']:
            raise ValueError('Rubric request binding changed')
        response = read_record(packet['provenance']['record_ref'])
        if digest(response) != packet['provenance']['sha256']:
            raise ValueError('Rubric response provenance changed')
        call = response['call']
        if call.get('mode') == 'offline':
            from demiflow.operator_llm.lance_journal import key_for
            if key_for(read_record(call['response_ref'])) != call['response_sha256']:
                raise ValueError('Rubric native response changed')
        for image in packet['evidence_images']:
            pixels(image['asset'])
        if packet['question'].get('edit_source'):
            pixels(packet['question']['edit_source'])
    return index


def verify_rubric_inputs(rows):
    # Recheck mutable image files even when the prepared-request checkpoint is reused.
    for row in rows:
        if row.get('rubric_status') == 'prepared':
            if row['question'].get('edit_source'):
                pixels(row['question']['edit_source'])
            for image in row['rubric_context']['evidence_images']:
                pixels(image['asset'])
            binding = row['rubric_binding']
            if digest(read_record(binding['request_ref'])['messages']) != binding['input_sha256']:
                raise ValueError('Prepared rubric request changed')
