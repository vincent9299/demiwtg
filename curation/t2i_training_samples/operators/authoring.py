"""Published-material delivery and candidate evidence helpers."""
def require_texts(value, keys):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k].strip() for k in keys):
        raise ValueError('Nonempty fields required: ' + ', '.join(keys))


def evidence_numbers(value, count, *, allow_empty=False):
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(type(n) is not int or not 1 <= n <= count for n in value)):
        raise ValueError('Evidence must refer to supplied numbered materials')
    return list(dict.fromkeys(value))


def focus_material_numbers(row, opportunity, field='evidence'):
    """Close each selection over its own required figures, preserving role separation."""
    materials = row['materials']
    numbers = set(evidence_numbers(opportunity[field], len(materials),
                                   allow_empty=field == 'input_materials'))
    figures = {(m['concept'], m['image_id']): n for n, m in enumerate(materials, 1)
               if m['kind'] == 'image'}
    pending = list(numbers)
    while pending:
        item = materials[pending.pop() - 1]
        for iid in item.get('publication', {}).get('visual_dependencies', []):
            number = figures.get((item['concept'], iid))
            if number is None:
                raise ValueError('Selected material requires a missing figure: ' + iid)
            if number not in numbers:
                numbers.add(number)
                pending.append(number)
    return sorted(numbers)
