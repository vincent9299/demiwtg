"""声明式数据流的复杂行/分组函数；不另建 Dataset、不执行整表或调度模型。"""


def expand_author_models(row, *, config):
    """把一个有效样本按配置作者模型展开为出题任务。"""
    return (
        [
            {
                **row,
                'task_id': row['sample_id'] + '_' + model.split('/')[-1],
                'qid': row['sample_id'] + '_' + model.split('/')[-1],
                'author_model': model,
                'status': 'ready_to_author',
            }
            for model in config['models']
        ]
        if row['status'] == 'sample_ready'
        else [row]
    )


def bind_question_metadata(row):
    """为通过审核的 V1 题目绑定原样本与作者元信息。"""
    return (
        {
            **row,
            'question': {
                'suite': 'basic',
                'task': 't2i',
                'qid': row['qid'],
                **row['question'],
                '_query_label': row['instance'],
                '_generator_model': row['author_model'],
                '_job_sample': row['sample_id'],
                '_job_qid': row['qid'],
                **({'_sample_image': row['image_ref']} if row.get('image_ref') else {}),
            },
        }
        if row['export_ready']
        else row
    )
