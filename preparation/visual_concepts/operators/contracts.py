"""Configuration/source schema checks; no business table scans."""
import pyarrow as pa


def fixed(ref):
    if (not isinstance(ref, dict) or set(ref) != {'uri', 'version'}
            or not isinstance(ref['uri'], str) or not ref['uri'].strip()
            or type(ref['version']) is not int or ref['version'] < 1):
        raise ValueError('Sources require uri and a positive fixed version')
    return dict(ref)


def check_schema(actual, allowed, path=''):
    """拒绝会被 Arrow 投影静默忽略的新字段；缺少的可选字段保持 null。"""
    for field in actual:
        location = path + field.name
        if field.name not in allowed.names:
            raise ValueError('Unmapped source field: ' + location)
        want, have = allowed.field(field.name).type, field.type
        if pa.types.is_struct(have) and pa.types.is_struct(want):
            check_schema(pa.schema(list(have)), pa.schema(list(want)), location + '.')
        elif (pa.types.is_list(have) or pa.types.is_large_list(have)) and (pa.types.is_list(want) or pa.types.is_large_list(want)):
            check_schema(pa.schema([pa.field('item', have.value_type)]),
                         pa.schema([pa.field('item', want.value_type)]), location + '[]')
        elif have != want:
            raise ValueError('Source type mismatch: ' + location + ': ' + str(have) + ' != ' + str(want))
