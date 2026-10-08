"""只读分维度对照：复用A/B分数，调用现行D计算器处理已存响应。"""
import csv
import hashlib
import io
import json
from pathlib import Path
from .d_scores import score_d

MODES = {'text_only': '无参考图', 'positive_images': '正例参考图'}
COLUMNS = [
    ('a_alignment', 'A 对齐', '任务对齐／正确性'),
    ('b_task', 'B 任务分', '任务对齐／正确性'),
    ('d_task', 'D 任务正确性', '任务对齐／正确性'),
    ('d_core', 'D 核心正确性', '任务对齐／正确性'),
    ('d_noncore', 'D 非核心正确性', '任务对齐／正确性'),
    ('a_quality', 'A 质量', '视觉质量'), ('d_quality', 'D 质量', '视觉质量'),
    ('a_aesthetics', 'A 美感', '美感'), ('d_aesthetics', 'D 美感', '美感'),
    ('d_other', 'D 其他遵循', '其他题面遵循'),
    ('d_general', 'D 通用题面遵循', '通用题面遵循'),
    ('a_overall', 'A 综合', '各方案综合'), ('d_overall', 'D 综合', '各方案综合'),
]
D_FIELDS = {'d_task': 'task_correctness', 'd_quality': 'quality', 'd_aesthetics': 'aesthetics',
            'd_other': 'other_instruction_following', 'd_overall': 'overall',
            'd_core': 'task_correctness_core', 'd_noncore': 'task_correctness_noncore',
            'd_general': 'general_instruction_following'}


def calculate_d(row):
    """只有分数换算；不向模型发请求，不以旧的格式拒收状态丢弃响应。"""
    out = {**row, 'recalculated': None, 'calculation_error': ''}
    try:
        raw = json.loads(row['d_json']) if row.get('d_json') else None
        if raw is None:
            out['calculation_error'] = row.get('d_reason') or '没有已保存的判官响应'
        else:
            frozen = json.loads(row['core_requirements_json']) if row.get('d_protocol') == 'd7' else None
            out['recalculated'] = score_d(raw, instruction=None, test_points=None, core_requirements=frozen)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        out['calculation_error'] = str(error)
    return out


def _index(rows):
    indexed = {}
    for row in rows:
        key = row['task_id'], row['answer_mode']
        if key in indexed:
            raise ValueError('比较来源存在重复的题目／作答条件')
        indexed[key] = row
    return indexed


def build_payload(questions, ab_rows, d_rows, *, sources):
    """有界展示数据，每题保留两行；按相同题面和同一张作答图对齐。"""
    ab, ds = _index(ab_rows), _index(d_rows)
    rows = []
    d7 = any((r.get('recalculated') or {}).get('protocol') == 'd7' or r.get('d_protocol') == 'd7' for r in d_rows)
    old = any(r.get('d_protocol') != 'd7' for r in d_rows)
    columns = [col for col in COLUMNS if not (col[0] in ('d_core', 'd_noncore', 'd_general') and not d7)
               and not (col[0] == 'd_other' and d7 and not old)]
    for rank, question in enumerate(questions, 1):
        for mode, mode_label in MODES.items():
            key = question['task_id'], mode
            a, d = ab.get(key), ds.get(key)
            for item in (a, d):
                if item and item['instruction'] != question['instruction']:
                    raise ValueError('比较来源的题面不一致：' + question['concept'])
            if a and d and (json.loads(a['image_json']) != json.loads(d['image_json'])
                             or a['answer_model'] != d['answer_model']):
                raise ValueError('A/B和D不是同一张作答图：' + question['concept'])
            row = {'rank': rank, 'task_id': key[0], 'concept': question['concept'],
                   'instruction': question['instruction'], 'answer_mode': mode, 'mode': mode_label,
                   **{name: None for name, _, _ in COLUMNS}, 'notes': {}}
            for name, field in (('a_alignment', 'alignment_score'), ('a_quality', 'quality_score'),
                                ('a_aesthetics', 'aesthetics_score'), ('a_overall', 'a_score')):
                row[name] = a.get(field) if a and a.get('a_status') == 'scored' else None
                if row[name] is None:
                    row['notes'][name] = (a.get('a_reason') or a.get('a_status')) if a else '缺少A评审'
            row['b_task'] = a.get('b_score') if a and a.get('b_status') == 'reviewed' else None
            if row['b_task'] is None:
                b_result = json.loads(a.get('b_json') or 'null') if a else None
                row['notes']['b_task'] = ((b_result or {}).get('reason') or a.get('b_reason')
                                         or a.get('b_verdict') or a.get('b_status')) if a else '缺少B评审'
            metrics = d.get('recalculated') if d else None
            for name, dimension in D_FIELDS.items():
                row[name] = metrics.get(dimension + '_score') if metrics else None
                if row[name] is None:
                    row['notes'][name] = (metrics.get(dimension + '_status') or
                        ('有维度无法判定，综合分留空' if dimension == 'overall' else '无法判定')) if metrics else (
                        d.get('calculation_error') or '缺少D评审' if d else '缺少D评审')
            row['d_previous_status'] = d.get('d_status') if d else 'missing'
            row['d_calculation_status'] = 'reviewed' if metrics else 'unavailable'
            row['d_scoring_revision'] = metrics.get('scoring_revision') if metrics else None
            rows.append(row)
    summaries = {}
    for mode in MODES:
        subset = [r for r in rows if r['answer_mode'] == mode]
        summaries[mode] = {name: {'valid': len(values), 'mean': sum(values) / len(values) if values else None}
            for name, _, _ in COLUMNS for values in [[r[name] for r in subset if r[name] is not None]]}
    return {'columns': columns, 'rows': rows, 'summaries': summaries,
            'meta': {'sources': sources, 'questions': len(questions), 'answers': len(rows),
                     'd_scoring_revisions': sorted({r['d_scoring_revision'] for r in rows if r['d_scoring_revision']}),
                     'calculator_sha256': hashlib.sha256(Path(__file__).with_name('d_scores.py').read_bytes()).hexdigest(),
                     'd_recalculated_from_saved_responses': True, 'model_calls': 0}}


def render(payload):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    if len(encoded.encode()) > 8 * 1024 * 1024:
        raise ValueError('分数明细文本超过8MiB')
    return Path(__file__).with_suffix('.html').read_text().replace('__SCORE_DATA__', encoded)


def export_csv(payload):
    buffer = io.StringIO(newline='')
    fields = [('rank', '题号'), ('concept', '题目'), ('mode', '作答方式'), *[(k, label) for k, label, _ in payload['columns']],
              ('task_id', 'task_id'), ('instruction', '完整题面'), ('d_scoring_revision', 'D计算版本')]
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in fields] + ['空分说明'])
    for row in payload['rows']:
        writer.writerow([row.get(key) for key, _ in fields] + [json.dumps(row['notes'], ensure_ascii=False)])
    return '\ufeff' + buffer.getvalue()
