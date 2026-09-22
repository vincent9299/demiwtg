"""Read-only question/result presentation; native Markdown and images, no HTML."""
from curation.preparation.records import run_state, run_manifest
from pathlib import Path

from curation.preparation.records import read, rows, read_record, run_records
from curation.preparation.inspection import (
    md, table, show_image, snapshot_ref, CHECKS,
)
from curation.preparation.materials import pixels
from curation.preparation.delivery import source_entries
from curation.evaluation.native.operators import verify_result


def show_question(row, number):
    kind = '文生图' if row['plan']['task_type'] == 't2i' else '编辑'
    md(f'### Q{number:02d} · {kind}')
    md(row['draft']['instruction'])
    if row.get('edit_source'):
        compact_image(row['edit_source'], '编辑原图')
    review = row.get('review_task') or {}
    verdict = '通过' if row.get('export_ready') else '未通过'
    failed = [CHECKS.get(k, k) for k, value in review.get('checks', {}).items() if value is False]
    md('**题目审核：' + verdict + '**' + ('；未通过项：' + '、'.join(failed) if failed else ''))
    md(review.get('reason') or '未记录审核意见。')


def compact_image(asset, label):
    from IPython.display import Image, display
    md('**' + label + '**')
    display(Image(data=pixels(asset)[0], width=640))


def show_question_knowledge(entry):
    record, figures = entry['record'], entry['figures']
    md('#### 知识库')
    shown = set()
    for topic in record.get('knowledge', []):
        md('**' + topic['title'] + '**')
        content = topic['content']
        for index, paragraph in enumerate(content['paragraphs']):
            md(paragraph)
            for figure in content['images']:
                iid = figure['image_id']
                if figure.get('paragraph_index') == index and iid in figures and iid not in shown:
                    compact_image(figures[iid], '知识配图')
                    shown.add(iid)
        for figure in content['images']:
            iid = figure['image_id']
            if iid in figures and iid not in shown:
                compact_image(figures[iid], '知识配图')
                shown.add(iid)
    md('**知识审核意见**')
    md(record.get('audit', {}).get('review_notes') or record.get('status_reason') or '未记录审核意见。')


def show_answers(run, groups=None):
    run = Path(run)
    if groups is None:
        state = run_state(run)
        stage = 'evaluation' if 'evaluation' in state['stages'] else 'comparison'
        if stage not in state['stages']:
            md('尚无完整作答汇总。已准备的任务或分区结果不表示全部完成。')
            return
        groups = rows(snapshot_ref(run, stage))
    groups = sorted(groups, key=lambda r: r['number'])
    order = {'bagel': 0, 'qwen2512': 1, 'qwen2511': 1, 'gemini': 2}
    for group in groups:
        md(f'## Q{group["number"]:02d} · {group["concept"]} · {group["review_status"]}')
        md(group['question']['instruction'])
        if group['question'].get('edit_source'):
            show_image(group['question']['edit_source'], '共同编辑原图')
        answers = sorted(group['answers'], key=lambda r: (order[r['backend']], r['condition'] == 'with_knowledge'))
        table(['模型', '条件', '知识模态', '作答', 'judge', '分数', '有效性'],
              [(a['backend'], a['condition'], a['actual_modalities'], a['status'],
                a.get('judge_status', '未判'), a.get('score', {}).get('metrics', {}),
                a.get('score', {}).get('validity', '—')) for a in answers])
        for answer in answers:
            verify_result(answer)
            label = answer['backend'] + ' / ' + answer['condition']
            if answer.get('image'):
                show_image(answer['image'], label)
            else:
                md('**' + label + '**：' + answer['status'] + '\n\n' +
                   str(answer.get('reason') or answer.get('error') or '没有图像'))
            if answer.get('score'):
                score = answer['score']
                raw = score.get('raw', {})
                table(['判据', '主维度／子项', '核心性', '结果', '图像证据'],
                      [(r['id'], r['dimension'] + ' / ' + r['check_category'], r.get('importance'),
                        r['result'], r.get('observation') or
                        'BEFORE：' + r.get('before', '') + '；AFTER：' + r.get('after', ''))
                       for r in raw.get('requirement_results', [])])
                if answer['task_type'] == 't2i':
                    for dim in ('alignment', 'quality', 'aesthetics'):
                        if dim in raw:
                            table([dim, '档位', '理由', '判据id'],
                                  [(key, value, raw[dim + '_reasons'][key], raw['criterion_refs'][dim][key])
                                   for key, value in raw[dim].items()])
                else:
                    table(['维度', '档位', '理由', '判据id'],
                          [(d['label'], d['tier'], d['reason'], d['criterion_ids'])
                           for d in raw.get('raw_dimensions', [])])
                if score.get('source'):
                    md('计分依据：' + score['source'] + '；没有图像证据的质量项保持未知。')
                if answer.get('judge_provenance'):
                    md('原始判分响应（Lance 固定引用）：' + str(answer['judge_provenance']['record_ref']))
            elif answer.get('judge_status'):
                md('判分状态：' + answer['judge_status'] + '；' + str(answer.get('judge_reason', '')))
        if group.get('pairs'):
            table(['配对模型', '指标', '有知识−无知识', '有效', '排除原因'],
                  [(p['backend'], p['metric'], p['delta'], p['valid'], p['reason']) for p in group['pairs']])


def show_rubrics(run):
    from curation.evaluation.native.contracts import frozen_stage
    state = run_state(Path(run))
    if 'rubrics' not in state['stages']:
        md('尚未执行评分清单核验。')
        return
    for row in rows(frozen_stage(run, 'rubrics')['dataset_ref']):
        md(f"### Q{row['number']:02d} · {row.get('rubric_status')} · {row['question']['instruction']}")
        if row.get('judge_packet'):
            packet = read_record(row['judge_packet']['record_ref'])
            md('作答前核验：' + str(packet['rubric']['audit']))
            table(['id', '维度／子项', '判据', '观察位置', '依据', '允许变化'],
                  [(r['id'], r['dimension'] + ' / ' + r['check_category'], r['requirement'],
                    r['observable_region'], r['evidence_ids'], r['allowed_variation'])
                   for r in packet['rubric']['criteria']])
        else:
            md(str(row.get('rubric_reason') or row.get('rubric_review', {})))


def show_prompt_requests(run, stage, limit=1):
    from curation.preparation.review_notebooks import display_json
    from curation.preparation.records import response_records
    requests = [(k,v) for k,v in sorted(run_records(run).items().items()) if k.startswith('request/'+stage+'/')]
    received = len(response_records(run,stage))
    md(f'已冻结{stage}请求：{len(requests)}；已收到offline响应的请求：{received}。')
    for key, request in requests[:limit]:
        md('完整请求固定引用：' + str(run_records(run).reference(key).to_dict()))
        md(display_json(request))
        for role in request['image_roles']:
            show_image(role, f"图像{role['number']} · {role['label']}")


def show_evaluation_summary(run):
    state = run_state(Path(run))
    if 'summary' not in state['stages']:
        md('尚无判分汇总；待办请求和已准备条件不代表已完成评测。')
        return
    summary = next(rows(snapshot_ref(run, 'summary')))
    table(['状态／分母', '数量'], list(summary['counts'].items()))
    table(['题型', '编辑类型', '模型', '增强模态', '题目机器通过', '指标', '配对n', '排除n', '平均变化'],
          [(r['task_type'], r['edit_type'], r['backend'], r['knowledge_mode'], r['accepted_by_machine'],
            r['metric'], r['n'], r['excluded'], r['mean_delta']) for r in summary['paired_deltas']])
    table(['题型', '编辑类型', '模型', '增强模态', '题目机器通过', '指标', '共同n', '增强前差距', '增强后差距', '差距缩小'],
          [(r['task_type'], r['edit_type'], r['backend'], r['knowledge_mode'], r['accepted_by_machine'], r['metric'], r['n'],
            r['mean_gap_without'], r['mean_gap_with'], r['mean_gap_reduction'])
           for r in summary['closed_reference_gaps']])
    md('三维分别报告；编辑按类型、增强按模态、开发题按机器审核状态分组。正的gap_reduction表示与同题Gemini无知识结果的差距缩小，不能由单图推出模型使用了知识。')
