"""声明式数据流的复杂行/分组函数；不另建 Dataset、不执行整表或调度模型。"""

import json


def judge_response_fields(row, *, ANSWERS, judge):
    """提取 judge 响应、失败和调用引用，保留原始答案字段。"""
    return {
        **{name: row.get(name) for name in ANSWERS.names},
        'judge_model': judge['model'],
        'judge_json': None,
        'judge_result': row.get('judge_result'),
        'judge_error': row.get('judge_error'),
        'judge_call_json': json.dumps(
            row.get('judge_call') or (row.get('judge_error') or {}).get('call', {}), ensure_ascii=False
        ),
        'alignment_score': None,
        'quality_score': None,
        'aesthetics_score': None,
    }


def validate_scores(row):
    """检查三类评分的取值并保留原始 judge JSON。"""
    return (
        {
            **row,
            'invalid_scores': [
                f'{dimension}.{key} 分值须为 0、1、2 或 N/A'
                for dimension in ('alignment', 'quality', 'aesthetics')
                for key, value in row['judge_result'][dimension].items()
                if not ((type(value) is int and value in (0, 1, 2)) or value == 'N/A')
            ],
            'judge_json': json.dumps(row['judge_result'], ensure_ascii=False),
        }
        if row['status'] == 'generated'
        else row
    )


def score_values(row):
    """将有效的 0/1/2 档位映射为 0/60/100，排除 N/A。"""
    return (
        {
            **row,
            'scores': {
                dimension: [
                    {0: 0, 1: 60, 2: 100}[v] for v in row['judge_result'][dimension].values() if v != 'N/A'
                ]
                for dimension in ('alignment', 'quality', 'aesthetics')
            },
        }
        if row['status'] == 'generated'
        else row
    )


def mean_scores(row):
    """对每个适用维度求均分，完整保留不适用的空值。"""
    return (
        {
            **row,
            'status': 'scored',
            'reason': '',
            **{
                dimension + '_score': round(sum(values) / len(values), 2) if values else None
                for dimension, values in row['scores'].items()
            },
        }
        if row['status'] == 'generated'
        else row
    )
