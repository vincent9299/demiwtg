"""Edit prompt payloads and business validation; HTTP uses map_prompt_async."""
import base64
import json
from pathlib import Path

import yaml
from demiflow.execution.artifacts import digest
from demiflow.operator_llm.parser import parse_prompt_pack
from preparation.operaters.inputs import pixels, duplicate

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
CHECKS = ('grounded', 'instruction_satisfied', 'visible_supervision', 'preservation',
          'input_sufficient', 'nonleaking', 'image_quality')


def prompt_pack(model):
    spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
    for definition in spec['prompts'].values():
        definition['model'].update(name=model['name'], base_url=model['base_url'])
    return parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True))


def image_url(asset):
    raw, mime, _ = pixels(asset)
    return 'data:' + mime + ';base64,' + base64.b64encode(raw).decode()


def prepare_design(row):
    images = [image_url(row['existing'])]
    materials = []
    for number, item in enumerate(row['materials'], 1):
        entry = {'number': number, 'kind': item['kind'], 'support': item.get('review', {})}
        if item['kind'] == 'text':
            entry['text'] = item['text']
        else:
            images.append(image_url(item['asset']))
            entry['image_number'] = len(images)
        materials.append(entry)
    payload = {'concept': row['concept'], 'existing_image_number': 1,
               'materials': materials, 'previous_samples': row['previous_samples']}
    return {**row, 'prompt_payload': payload, 'prompt_images': images}


def selected_materials(row, numbers):
    if (not isinstance(numbers, list) or len(set(numbers)) != len(numbers)
            or any(type(n) is not int or not 1 <= n <= len(row['materials']) for n in numbers)):
        raise ValueError('Selected material numbers must exist and be unique')
    selected = [row['materials'][n - 1] for n in numbers]
    figures = {m['image_id'] for m in selected if m['kind'] == 'image'}
    if any(set(m.get('publication', {}).get('visual_dependencies', [])) - figures for m in selected):
        raise ValueError('Selected text requires an unselected figure')
    return selected


def apply_design(row, *, guard):
    out = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    call = out.get('design_call') or {}
    out['design_seconds'] = call.get('elapsed_s')
    if out.get('design_error') or not out.get('design_result'):
        return {**out, 'status': 'design_failed', 'reason': str(out.get('design_error'))}
    result = out['design_result']
    if result['status'] == 'insufficient':
        return {**out, 'status': 'insufficient', 'reason': result['reason']}
    try:
        if result['status'] != 'ok' or result['existing_role'] not in {'source', 'target'}:
            raise ValueError('Invalid design status or image role')
        for field in ('learning_objective', 'edit_type', 'role_reason', 'supervision_signal',
                      'instruction', 'synthesis_instruction', 'reference_reason'):
            if not result[field].strip():
                raise ValueError('Missing ' + field)
        if not result['learning_directions'] or not result['criteria'] or not result['preserve']:
            raise ValueError('Design requires learning directions, criteria and preservation checks')
        evidence = selected_materials(row, result['evidence'])
        inputs = selected_materials(row, result['input_materials'])
        synthesis = selected_materials(row, result['synthesis_materials'])
        guard.check_instruction(result['instruction'], training=True)
        for item in evidence + inputs + synthesis:
            if not guard.allows_item(item, training=True):
                raise ValueError('Formal-test material reservation')
        for item in inputs:
            if item['kind'] == 'image' and duplicate(item['asset'], row['existing']):
                raise ValueError('Existing image or near-duplicate cannot be an extra reference')
        return {**out, **{k: v for k, v in result.items() if k != 'status'}, 'status': 'designed',
                'answer_materials': inputs, 'synthesis_materials_selected': synthesis,
                'evidence_materials': evidence, 'design_sha256': digest(result)}
    except (ValueError, KeyError, TypeError) as error:
        return {**out, 'status': 'invalid_design', 'reason': str(error)}


def bind_pair(row):
    if row['status'] != 'synthesized':
        return row
    source = row['existing'] if row['existing_role'] == 'source' else row['generated']
    target = row['generated'] if row['existing_role'] == 'source' else row['existing']
    if source['sha256'] == target['sha256']:
        return {**row, 'status': 'invalid_pair', 'reason': 'No image change'}
    if any(m['kind'] == 'image' and duplicate(m['asset'], target) for m in row['answer_materials']):
        return {**row, 'status': 'invalid_pair', 'reason': 'Target or near-duplicate in references'}
    content = [{'type': 'text', 'text': row['instruction'], 'blob_ref': None},
               {'type': 'image', 'text': None, 'blob_ref': source['blob_ref']}]
    for item in row['answer_materials']:
        content.append({'type': 'text', 'text': item['text'], 'blob_ref': None} if item['kind'] == 'text'
                       else {'type': 'image', 'text': None, 'blob_ref': item['asset']['blob_ref']})
    return {**row, 'status': 'pair_ready', 'source': source, 'target': target,
            'input_content': content, 'pair_sha256': digest([content, target['blob_ref'], row['criteria']])}


def prepare_review(row):
    if row['status'] != 'pair_ready':
        return row
    images = [image_url(row['source']), image_url(row['target'])]
    payload = {k: row[k] for k in ('concept', 'learning_objective', 'edit_type', 'instruction',
                                  'preserve', 'criteria', 'supervision_signal')}
    payload.update(source_image=1, target_image=2, actual_training_references=[], construction_evidence=[])
    for name, materials in [('actual_training_references', row['answer_materials']),
                            ('construction_evidence', row['evidence_materials'])]:
        for item in materials:
            if item['kind'] == 'text':
                payload[name].append({'text': item['text']})
            else:
                images.append(image_url(item['asset']))
                payload[name].append({'image_number': len(images)})
    return {**row, 'prompt_payload': payload, 'prompt_images': images}


def apply_review(row):
    out = {k: v for k, v in row.items() if not k.startswith('prompt_')}
    if row['status'] != 'pair_ready':
        return out
    out['review_seconds'] = (row.get('review_call') or {}).get('elapsed_s')
    if row.get('review_error') or not row.get('review_result'):
        return {**out, 'status': 'review_failed', 'reason': str(row.get('review_error'))}
    review = row['review_result']
    criteria = review['criteria']
    valid = (set(review['checks']) == set(CHECKS)
             and len(criteria) == len(row['criteria'])
             and {c['criterion'] for c in criteria} == set(range(1, len(row['criteria']) + 1)))
    unchanged = row['pair_sha256'] == digest([row['input_content'], row['target']['blob_ref'], row['criteria']])
    accepted = (valid and unchanged and all(c['passed'] is True for c in review['checks'].values())
                and all(c['passed'] is True for c in criteria))
    return {**out, 'status': 'accepted' if accepted else 'rejected', 'review': review,
            'reason': review['reason'] if valid and unchanged else 'Incomplete review or changed pair'}
