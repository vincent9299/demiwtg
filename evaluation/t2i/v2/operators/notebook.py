"""Small notebook views and snapshots of public StreamStats; no model execution."""
from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import lance
from demiflow import data
from demiflow.execution.artifacts import run_is_active
from .run_tables import RunTables


D_STAGES = (
    ('prepare_score', '准备判分输入'),
    ('judge_d', '判官调用 · map_prompt_async'),
    ('finish_score', '解析响应与计算分数'),
    ('save_scores_d', '提交判分行 · save_lance'),
    ('report_result', '输出进度记录'),
)


def notebook_browser(document, relative_path, *, height=4000):
    """Keep the shared srcdoc viewer; bridge host-issued copy commands and selection.

    VS Code may execute copy in the outer output, including a Shadow DOM renderer,
    without delivering a keydown to the sandboxed frame. Never read the clipboard.
    """
    from uuid import uuid4
    from benchmark.t2i.v2.operators.case_viewer import notebook_browser as base_browser
    host_id = 't2i-copy-' + uuid4().hex
    return (f'<section id="{host_id}" data-t2i-copy-host="v1">'
            '<details data-copy-panel><summary>复制备用文本框（选中文字后自动同步）</summary>'
            '<textarea data-copy-text readonly rows="3" aria-label="复制备用文本" '
            'style="width:100%;box-sizing:border-box"></textarea>'
            '<button type="button" data-copy-select>选中备用文本</button> '
            '<span data-copy-status role="status">快捷键无效时，选中备用文本，再按 Ctrl+C / ⌘C。</span>'
            '</details>' + base_browser(document, relative_path, height=height) + '</section>' +
            r'''<script>
(()=>{'use strict';
function find(root){const own=root.querySelector('#__COPY_HOST_ID__');if(own)return own;
 for(const el of root.querySelectorAll('*'))if(el.shadowRoot){const hit=find(el.shadowRoot);if(hit)return hit;}}
const box=find(document);if(!box)return;
const frame=box.querySelector('iframe'),area=box.querySelector('[data-copy-text]'),
 status=box.querySelector('[data-copy-status]'),controller=new AbortController();
let selected='';
const live=()=>{if(box.isConnected)return true;controller.abort();return false;};
window.addEventListener('message',e=>{
 if(!live()||e.source!==frame.contentWindow)return;
 if(e.data?.type==='t2i-open-image-v1'){
  const href=e.data.href;
  if(typeof href!=='string'||!href.startsWith('/')||href.startsWith('//')||/[\u0000-\u0020]/.test(href))return;
  // VS Code's notebook link handler resolves absolute paths against the active
  // remote workspace. A normal click() has no view; its handler needs window.
  const link=document.createElement('a');link.setAttribute('href',href);link.hidden=true;box.append(link);
  link.addEventListener('click',event=>event.preventDefault());
  link.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,composed:true,view:window}));
  link.remove();return;
 }
 if(typeof e.data?.text!=='string')return;
 if(e.data.type==='t2i-selection-v1'){selected=e.data.text;area.value=selected;}
 else if(e.data.type==='t2i-copy-result-v1'){
  area.value=e.data.text;
  if(!e.data.copied){box.querySelector('[data-copy-panel]').open=true;
   status.textContent='自动复制未成功；请点击选中备用文本，再按 Ctrl+C / ⌘C。';}
 }
},{signal:controller.signal});
document.addEventListener('copy',e=>{
 if(!live()||!selected||!e.clipboardData)return;
 let active=document.activeElement;
 while(active?.shadowRoot?.activeElement)active=active.shadowRoot.activeElement;
 if(active!==frame&&box.getRootNode().activeElement!==frame)return;
 e.clipboardData.setData('text/plain',selected);e.preventDefault();
 status.textContent=`已提交复制 ${selected.length} 个字符。`;
},{capture:true,signal:controller.signal});
box.querySelector('[data-copy-select]').onclick=()=>{
 if(!area.value){status.textContent='请先选中文字或点击图片的复制按钮。';return;}
 area.focus();area.select();status.textContent='已选中备用文本，请按 Ctrl+C / ⌘C。';
};
frame.addEventListener('load',()=>frame.contentWindow.postMessage({type:'t2i-selection-request-v1'},'*'),{signal:controller.signal});
})();</script>'''.replace('__COPY_HOST_ID__', host_id))


def _run_id(value):
    if not isinstance(value, str) or not value or Path(value).name != value or value in {'.', '..'}:
        raise ValueError('run_id must be a single directory name')
    return value


def _now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def _save_json(path, value):
    """A replaceable observation artifact, never a business result table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.name, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class DStreamProgress:
    """Observe D's five unfiltered, one-row calls through public StreamStats.

    in counts entered rows. timing.count counts returned/raised invocations, not
    emitted rows. This mapping is valid here because every map consumes one row
    and save_lance has max_batch=1. It is not a generic batch/flat-map estimator.
    Cancellation also records durations, so interrupted drains leave completion
    unknown. The successful return of run_stream establishes a normal drain.
    """
    def __init__(self, root, run_id, arm, expected, max_consecutive_failures=None):
        self.run_id, self.arm, self.expected = _run_id(run_id), arm, expected
        self.path = Path(root) / '_demiflow/evaluation_t2i_v2' / run_id / f'operators_arm{arm:02}.json'
        self.stats = None
        self.max_consecutive_failures = max_consecutive_failures
        self.consecutive_failures = 0

    def progress(self, stats):
        self.stats = stats
        self.snapshot('running')

    def drained(self, stats):
        self.stats = stats
        self.snapshot('drain_unconfirmed')

    def finished(self, stats):
        self.stats = stats
        self.snapshot('finished')

    def snapshot(self, phase):
        if self.stats is None:
            return
        durations = self.stats.timing_summary()
        operators = []
        for name, title in D_STAGES:
            native = self.stats.stages.get(name, {})
            entered = native.get('in', 0)
            # Every stage has an explicit one-input-per-call contract in this DAG.
            done = durations.get(name, {}).get('count', 0) if phase != 'drain_unconfirmed' else None
            operators.append({'operator': name, 'title': title,
                'input_grain': '题目 × 作答路', 'output_grain': '题目 × 作答路',
                'expected_input_rows': self.expected, 'entered_input_rows': entered,
                'completed_input_rows': done,
                'pending_input_rows': max(0, self.expected - done) if done is not None else None,
                'in_flight_input_rows': max(0, entered - done) if done is not None else None,
                'emitted_output_rows': native.get('emitted', 0),
                'misses': {k: v for k, v in self.stats.miss.items() if k.startswith(name + ':')},
                'evidence': 'StreamStats.stages + timing_summary; 无过滤、逐行调用、sink max_batch=1'})
        _save_json(self.path, {'run': self.run_id, 'arm': self.arm, 'phase': phase,
            'updated_at': _now(), 'operators': operators, 'outputs': self.stats.outputs,
            'note': '行完成事件触发快照，非定时心跳。完成含失败返回；输出行与有效评分分开。'})

    def report_result(self, row):
        print(json.dumps({'phase': 'd_judge', 'arm': self.arm, 'concept': row['concept'],
            'status': row['d_status'], 'task': row['d_task_score'], 'quality': row['d_quality_score'],
            'aesthetics': row['d_aesthetics_score'], 'other_instruction_following': row['d_other_score'],
            'overall': row['d_overall_score']}, ensure_ascii=False), flush=True)
        self.snapshot('running')
        call = json.loads(row.get('d_call_json') or '{}')
        if row['d_status'] == 'reviewed':
            self.consecutive_failures = 0
        elif row['d_status'] not in {'pending', 'ready'} and not call.get('reused'):
            self.consecutive_failures += 1
        if self.max_consecutive_failures and self.consecutive_failures >= self.max_consecutive_failures:
            raise RuntimeError(f'D judge arm {self.arm} stopped after {self.consecutive_failures} consecutive failures; committed rows and raw calls retained')
        return row


def monitor_run(project, run_id):
    """Read fixed committed versions; never refresh them by invoking a pipeline."""
    run_id = _run_id(run_id)
    project = Path(project).resolve()
    root, owner = project.parent, project / 'evaluation/t2i/v2'
    records = RunTables(root, f'demiwtg/evaluation/t2i/v2/datasets/records__{run_id}.lance')
    manifest = records.load_manifest()
    if not manifest:
        return {'run': run_id, 'found': False, 'message': '未找到此运行；未回退到其他 run。'}
    state = records.load() or {}
    cfg = manifest['config']
    is_d = 'd_evaluation' in cfg
    models = cfg.get('answers', [])
    expected = manifest.get('expected_questions', state.get('expected_questions'))
    if expected is None or not 0 <= expected <= 300:
        raise ValueError('Monitor requires a bounded declared question scope')
    control = root / '_demiflow/evaluation_t2i_v2' / run_id
    arms, errors = [], []
    for index, model in enumerate(models):
        table = owner / 'datasets' / f'{"scores_d" if is_d else "answers"}__{run_id}__arm{index:02}.lance'
        reference = state.get('outputs', {}).get(f'arm{index}') if is_d else None
        if reference is None and table.exists():
            reference = {'uri': str(table), 'version': lance.dataset(str(table)).version}
        status_field = 'd_status' if is_d else 'status'
        reason_field = 'd_reason' if is_d else 'reason'
        rows = (data.read_lance(**reference, columns=['task_id', 'concept', status_field, reason_field] + (['d_overall_score'] if is_d else []))
                .take(expected + 1)) if reference else []
        if len(rows) > expected or len({r['task_id'] for r in rows}) != len(rows):
            raise ValueError('Monitor output identities exceed the declared scope')
        counts = dict(Counter(r[status_field] for r in rows))
        success = (sum(r['d_status'] == 'reviewed' and r['d_overall_score'] is not None for r in rows)
                   if is_d else counts.get('generated', 0))
        unscorable = counts.get('reviewed', 0) - success if is_d else 0
        failures = [r for r in rows if r[status_field] not in
                    {'reviewed', 'generated', 'pending', 'ready'}]
        for row in failures:
            errors.append({'arm': index, 'concept': row['concept'],
                           'status': row[status_field], 'reason': row[reason_field]})
        path = control / f'operators_arm{index:02}.json'
        telemetry = json.loads(path.read_text()) if path.exists() else None
        if telemetry and (telemetry['run'] != run_id or telemetry['arm'] != index):
            raise ValueError('Operator telemetry belongs to another run/arm')
        arms.append({'index': index, 'label': model['model'] + ' · ' + model['answer_mode'],
                     'expected': expected, 'committed_rows': len(rows), 'valid_rows': success,
                     'failed_rows': len(failures), 'unscorable_rows': unscorable,
                     'pending_rows': counts.get('pending', 0) + counts.get('ready', 0),
                     'missing_rows': expected - len(rows),
                     'states': counts, 'source': reference, 'telemetry': telemetry})
        if not is_d and model.get('answer_mode') == 'imagerag':
            stages = []
            for stage, status, unit in [('rag_diagnoses', 'rag_status', '诊断题目'),
                                        ('rag_queries', 'embedding_error', '检索描述'),
                                        ('rag_retrievals', 'embedding_error', '检索描述'),
                                        ('rag_inputs', None, '最终检索输入题目')]:
                uri = owner / 'datasets' / f'{stage}__{run_id}__arm{index:02}.lance'
                if not uri.exists():
                    stages.append({'stage': stage, 'unit': unit, 'rows': 0, 'source': None, 'states': {}})
                    continue
                table = lance.dataset(str(uri))
                maximum = expected * model['imagerag']['max_queries'] if stage in ('rag_queries', 'rag_retrievals') else expected
                if table.count_rows() > maximum:
                    raise ValueError('RAG monitor stage exceeds declared row budget')
                values = table.to_table(columns=[status]).column(0).to_pylist() if status else []
                if status == 'embedding_error':
                    values = ['failed' if value else 'succeeded' for value in values]
                stages.append({'stage': stage, 'unit': unit, 'rows': table.count_rows(),
                    'source': {'uri': str(uri), 'version': table.version}, 'states': dict(Counter(values))})
            arms[-1]['rag_stages'] = stages
            arms[-1]['rag_reuse_sources'] = {k: v for k, v in model['imagerag'].get('reuse', {}).items()
                                           if k in ('inputs', 'answers')}
    log_path = control / 'driver.log'
    if not log_path.exists():
        log_path = control / 'run.log'
    tail = ''
    if log_path.exists():
        with log_path.open('rb') as stream:
            stream.seek(max(0, log_path.stat().st_size - 16384))
            tail = stream.read().decode('utf-8', errors='replace')
        tail = '\n'.join(tail.splitlines()[-15:])
    return {'run': run_id, 'found': True, 'updated_at': _now(), 'phase': state.get('phase', '未提交摘要'),
            'complete': state.get('complete', False), 'active': run_is_active(control),
            'source': manifest['source'], 'questions': expected, 'expected': expected * len(models),
            'is_d': is_d, 'arms': arms, 'errors': errors, 'log_tail': tail,
            'judge': cfg.get('judge', {}).get('model'),
            'reasoning_effort': cfg.get('judge', {}).get('reasoning_effort')}


def render_monitor(report):
    """Small image-free HTML; unknown telemetry is never shown as a zero."""
    esc = lambda x: escape(str(x))
    count = lambda x: '未记录' if x is None else str(x)
    title = '<h2>运行监控 · ' + esc(report['run']) + '</h2>'
    if not report['found']:
        return title + '<p>' + esc(report['message']) + '</p>'
    parts = [title, '<p>状态：' + esc(report['phase']) + '；后台' + ('运行中' if report['active'] else '未运行')
             + '；评测完整：' + ('是' if report['complete'] else '否') + '；快照 ' + esc(report['updated_at']) + '</p>',
             '<p>范围：' + str(report['questions']) + ' 题 × ' + str(len(report['arms'])) + ' 路 = '
             + str(report['expected']) + ' 条。输入完成包括技术失败返回，不能等同于有效评分。</p>',
             '<table><tr><th>作答路</th><th>已提交结果行 / 应有</th><th>有效结果</th><th>技术/格式失败</th><th>响应有效但不可评分</th><th>待响应</th><th>尚未提交</th></tr>']
    for arm in report['arms']:
        parts.append(f'<tr><td>{esc(arm["label"])}</td><td>{arm["committed_rows"]} / {arm["expected"]}</td>'
                     f'<td>{arm["valid_rows"]}</td><td>{arm["failed_rows"]}</td><td>{arm["unscorable_rows"]}</td>'
                     f'<td>{arm["pending_rows"]}</td><td>{arm["missing_rows"]}</td></tr>')
    parts.append('</table>')
    for arm in report['arms']:
        if not arm.get('rag_stages'):
            continue
        parts.append('<h3>' + esc(arm['label']) + ' · RAG阶段提交快照</h3>'
                     '<p>诊断按题目、编码/检索按描述计数；这些是已提交回执，不是模型请求次数。'
                     '固定复用输入另列，尚未完成诊断的题目仍属于当前范围。</p>'
                     '<table><tr><th>阶段</th><th>粒度</th><th>已提交行</th><th>状态</th><th>固定版本</th></tr>')
        for stage in arm['rag_stages']:
            parts.append('<tr>' + ''.join('<td>' + esc(stage[key]) + '</td>'
                for key in ('stage', 'unit', 'rows', 'states', 'source')) + '</tr>')
        parts.append('</table><details><summary>固定复用来源</summary><pre>'
                     + esc(json.dumps(arm['rag_reuse_sources'], ensure_ascii=False, indent=2)) + '</pre></details>')
    parts.append('<h3>逐算子处理漏斗</h3><p>已处理完、待处理、在途均按输入行；产出按输出行。'
                 '待处理 = 应处理输入 − 已完成输入，含在途。算子已返回不代表下游已提交。'
                 '下面的计数来自标准算子的快照，非模型请求次数；失败/跳过按实际结果状态查看。</p>')
    for arm in report['arms']:
        parts.append('<h4>' + esc(arm['label']) + '</h4>')
        telemetry = arm['telemetry']
        if telemetry:
            parts.append('<p>算子快照 ' + esc(telemetry['updated_at']) + ' · ' + esc(telemetry['phase']) + '</p>')
            operators = telemetry['operators']
        else:
            parts.append('<p><strong>此运行未记录逐算子遥测。</strong>以下保留未知；上方提交数来自结果表，不反推算子完成数。</p>')
            operators = [{'title': title, 'operator': name, 'expected_input_rows': arm['expected']}
                         for name, title in (D_STAGES if report['is_d'] else
                         [('prepare_answer', '准备答题输入'), ('generate', '生成图片'), ('save_answers', '提交作答行')])]
        parts.append('<table><tr><th>算子</th><th>输入 → 输出粒度</th><th>应处理（输入行）</th>'
                     '<th>已处理完（输入行）</th><th>待处理（输入行，含在途）</th><th>在途（输入行）</th>'
                     '<th>产出（输出行）</th><th>引擎认缺记录</th></tr>')
        for op in operators:
            parts.append('<tr><td>' + esc(op['title']) + '<br><code>' + esc(op['operator']) + '</code></td><td>'
                         + esc(op.get('input_grain', '题目 × 作答路')) + ' → ' + esc(op.get('output_grain', '题目 × 作答路'))
                         + '</td>' + ''.join('<td>' + count(op.get(key)) + '</td>' for key in
                         ('expected_input_rows', 'completed_input_rows', 'pending_input_rows',
                          'in_flight_input_rows', 'emitted_output_rows'))
                         + '<td>' + esc(json.dumps(op.get('misses'), ensure_ascii=False)) + '</td></tr>')
        parts.append('</table>')
    parts.append('<p>题表读取、答案筛选/物化和按 task_id 关联属于流式算子前的 Dataset 操作；'
                 '当前公共接口未提供这些惰性操作的逐算子完成计数，故不伪造处理数。上方保留冻结范围与提交证据。</p>')
    parts.append('<h3>异常明细</h3><table><tr><th>路</th><th>题目</th><th>状态</th><th>原因</th></tr>')
    parts.extend('<tr>' + ''.join('<td>' + esc(error[key]) + '</td>' for key in ('arm', 'concept', 'status', 'reason'))
                 + '</tr>' for error in report['errors'])
    parts.append('</table><details><summary>最近 15 行日志（最多读取末尾 16 KiB）</summary><pre>'
                 + esc(report['log_tail']) + '</pre></details><details><summary>固定来源与运行快照</summary><pre>'
                 + esc(json.dumps({k: v for k, v in report.items() if k not in {'errors', 'log_tail'}}, ensure_ascii=False, indent=2))
                 + '</pre></details>')
    return '<div style="line-height:1.65;overflow:auto"><style>td,th{padding:8px;border:1px solid #ddd}table{border-collapse:collapse}pre{white-space:pre-wrap}</style>' + ''.join(parts) + '</div>'


def imagerag_d_config(project, run_id, baseline_config):
    """已完成 RAG 作答快照接入同题 D7；仅返回配置，不启动评测或写文件。

    baseline_config 是该模型既有 D7 配置（含固定无图答案来源和 judge）。
    RAG 尚未完成时拒绝，避免预填未来 Lance 版本或退回旧作答。
    """
    run_id = _run_id(run_id)
    project = Path(project).resolve()
    root = project.parent
    records = RunTables(root, f'demiwtg/evaluation/t2i/v2/datasets/records__{run_id}.lance')
    manifest, state = records.load_manifest(), records.load()
    if not manifest or not state or not state.get('complete') or not state.get('target'):
        raise ValueError('ImageRAG answer run must be complete with a committed target')
    models = manifest['config']['answers']
    if len(models) != 1 or models[0]['answer_mode'] != 'imagerag':
        raise ValueError('Expected a single-model ImageRAG answer run')
    cfg = json.loads(json.dumps(baseline_config))
    expected_source = {**cfg['source'], 'uri': str((root / cfg['source']['uri']).resolve())}
    if expected_source != manifest['source']:
        raise ValueError('D7 baseline and RAG must use the same fixed question source')
    baseline = cfg['d_evaluation']['answers'][0]
    if baseline['answer_mode'] != 'text_only' or baseline['model'] != models[0]['model']:
        raise ValueError('D7 baseline must be the same model without reference images')
    cfg.update(run=run_id + '_d7', source=manifest['source'],
        target=f'demiwtg/evaluation/t2i/v2/datasets/scores_d__{run_id}_d7.lance')
    cfg['d_evaluation'] = {**cfg['d_evaluation'], 'question_limit': manifest['expected_questions'],
        'answers': [baseline, {'model': models[0]['model'] + '+ImageRAG',
                             'answer_mode': 'imagerag', 'source': state['target']}]}
    rag_uri = project / 'evaluation/t2i/v2/datasets' / f'rag_inputs__{run_id}__arm00.lance'
    cfg['d_evaluation']['answers'][1]['rag_inputs'] = {
        'uri': str(rag_uri), 'version': lance.dataset(str(rag_uri)).version}
    baseline_state = RunTables(root, f'demiwtg/evaluation/t2i/v2/datasets/records__{baseline_config["run"]}.lance').load()
    if not baseline_state or not baseline_state.get('complete') or not baseline_state.get('target'):
        raise ValueError('D7 baseline must have a complete committed score table for reuse')
    cfg['d_evaluation']['reuse_scores'] = [baseline_state['target']]
    for key in ('retry_failed_from',):
        cfg['d_evaluation'].pop(key, None)
    cfg['d_evaluation']['reuse_initial_scores'] = True
    return cfg
