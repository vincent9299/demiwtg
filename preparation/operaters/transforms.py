"""声明式数据流的复杂行/分组函数；不另建 Dataset、不执行整表或调度模型。"""

from demiflow.execution.artifacts import digest


def material_status(row):
    """汇总当前概念的材料计数并标记是否有可用材料。"""
    return {
        **row,
        **{
            key: row.get(key, 0)
            for key in ('document_count', 'image_count', 'readable_documents', 'verified_images')
        },
        'material_status': (
            'materials_available'
            if row.get('document_count', 0) + row.get('image_count', 0)
            else 'no_materials_in_read_scope'
        ),
        'knowledge_status': 'not_extracted',
    }


def selection_reason(row, *, ids, sample_rate, seed):
    """按指定概念与固定采样种子记录选入/排除原因。"""
    return {
        **row,
        'selection_reason': (
            'id_filter'
            if ids is not None and row['concept_ref'] not in ids
            else (
                'concept_sample'
                if sample_rate < 1
                and int(digest({'concept_ref': row['concept_ref'], 'seed': seed})[:16], 16)
                >= int(sample_rate * 2**64)
                else 'selected'
            )
        ),
    }
