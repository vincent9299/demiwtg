"""单行公共中性描述合并；其他生产者字段不参与更新。"""
from preparation.images.annotation.operaters.schema import canonical

def _business_content(record):
    """比较用业务内容：排除 provenance/attempts 等运行痕迹，run_id 不同不算冲突。"""
    return {k: v for k, v in record.items() if k not in ('provenance', 'attempts')}


def merge_by_id(old, new, *, field, key='annotation_id'):
    """一个所属列的旧列表 + 新列表 → 按 ID 合并后的完整列表（按 ID 排序，稳定）。

    同 ID 相同业务内容幂等并保留旧 provenance；同 ID 不同业务内容报冲突。
    不能因新 run_id/attempts/写入时刻不同就新增一条或判内容冲突。
    """
    result = {}
    for record in old or []:
        if record[key] in result:
            raise ValueError('duplicate existing ' + field + ' identity: ' + record[key])
        result[record[key]] = record
    for record in new or []:
        previous = result.get(record[key])
        if previous is not None:
            if _business_content(previous) != _business_content(record):
                raise ValueError(
                    f'conflicting {field} record {record[key]}: '
                    f'existing {canonical(previous)} vs incoming {canonical(record)}')
            continue  # 幂等：保留旧记录（含旧 provenance），本轮复用写本轮 summary。
        result[record[key]] = record
    return [result[k] for k in sorted(result)]



def image_patch_row(row):
    return {'sha256': row['sha256'], 'descriptions': [row['description_record']],
            'image_scores': [row['score_record']]}


def merge_public_row(row):
    if not row.get('target_present'):
        raise ValueError('Image SHA absent from target; catalog must publish it first: ' + row['sha256'])
    values = {name: merge_by_id(row.get(name + '_old'), row[name], field=name)
              for name in ('descriptions', 'image_scores')}
    return {'sha256': row['sha256'], **values,
            'changed': any(values[name] != (row.get(name + '_old') or []) for name in values)}
