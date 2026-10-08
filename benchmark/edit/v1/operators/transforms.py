"""声明式数据流的复杂行/分组函数；不另建 Dataset、不执行整表或调度模型。"""


def make_edit_job(row, *, config):
    """为一条计划绑定编辑原图、题号和作者配置。"""
    return (
        {
            **{k: v for k, v in row.items() if k not in ('plan_images', 'plan_texts')},
            'task_id': row['job_id'],
            'attempt': config.get('attempt', 0),
            'source_image': row['plan_images'][row['image_index']],
            'target_edit_type': row['text_entry'].get('edit_type') or row['edit_type'],
            'author_text': row['text_entry'].get('text'),
            'adjustment': row.get('adjustment'),
            'status': 'ready_to_author' if row['text_entry'].get('text') else 'invalid_plan_row',
        }
        if row['status'] == 'plan_ready'
        else row
    )
