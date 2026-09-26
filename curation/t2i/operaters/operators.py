"""T2I validation helpers and explicit model-input binding."""
from preparation.operaters.inputs import add_image, asset_for_item
from demiflow.execution.artifacts import digest

FORBIDDEN = {"combo_type", "premise_types", "premise_count", "hop_count", "weakness_product"}


def fail(row, status, reason):
    return {**row, "status": status, "issues": row.get("issues", []) + [reason]}


def contains_forbidden(value):
    if isinstance(value, dict):
        return bool(FORBIDDEN.intersection(value)) or any(contains_forbidden(v) for v in value.values())
    return isinstance(value, list) and any(contains_forbidden(v) for v in value)


def training_content(instruction, materials):
    """Only explicitly selected text/pixels enter the answer, never review metadata."""
    content = [{'type': 'text', 'text': instruction}]
    roles = []
    for number, item in enumerate(materials, 1):
        if item['kind'] == 'text':
            content.append({'type': 'text', 'text': f'参考文字 {number}：\n' + item['text']})
        elif item['kind'] == 'image':
            asset = asset_for_item(item)
            add_image(content, asset, f'参考图 {number}')
            roles.append({'number': number, 'item_id': item['item_id'], 'role': 'reference',
                          'sha256': asset['sha256'], 'path': asset.get('path'),
                          'blob_ref': asset.get('blob_ref')})
        else:
            raise ValueError('Unsupported answer material kind')
    return content, roles


class BindTrainingInputs:
    """Validate and assemble the references fixed during task design; no retrieval."""
    def __init__(self, guard):
        self.guard = guard

    def __call__(self, row):
        if row['status'] != 'accepted_task':
            return row
        from preparation.operaters.inputs import reference_options
        try:
            if row['branch'] != 'training':
                raise ValueError('Bound training inputs require a training task')
            if row['plan']['task_type'] != 't2i' or row.get('edit_source'):
                raise ValueError('T2I input binding cannot accept an edit task or source image')
            construction = row['materials']
            evidence_binding = row['construction_selection']
            evidence_ids = [m['item_id'] for m in construction]
            if (evidence_ids != evidence_binding['item_ids']
                    or digest(construction) != evidence_binding['materials_sha256']):
                raise ValueError('Construction evidence changed after task design')
            selected = row['answer_materials']
            binding = row['training_input_binding']
            ids = [m['item_id'] for m in selected]
            if ids != binding['item_ids'] or digest(selected) != binding['materials_sha256']:
                raise ValueError('Selected training materials changed after task design')
            required = {key for criterion in row['criteria'] for key in criterion['knowledge_ids']}
            if not construction or not required <= set(evidence_ids):
                raise ValueError('Construction materials do not cover criterion evidence')
            excluded = list(row.get('design_targets', []))
            if (len(reference_options(selected, excluded)['evidence']) != len(selected)
                    or len(reference_options(construction, excluded)['evidence']) != len(construction)):
                raise ValueError('Bound references conflict with target/source or required figure')
            if not all(self.guard.allows_item(m, True) for m in selected + construction):
                raise ValueError('Bound training materials violate formal-test reservation')
            content, mapping = training_content(row['draft']['instruction'], selected)
            return {**row, 'answer_materials': selected,
                    'answer_input': {'messages': [{'role': 'user', 'content': content}], 'image_roles': mapping}}
        except (ValueError, TypeError, KeyError, OSError) as error:
            return fail({k: v for k, v in row.items() if k != 'answer_input'},
                        'invalid_training_inputs', str(error))
