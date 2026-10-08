"""合并固定来源的只读题单；按显式来源顺序选最新，完整材料只读取当前页。"""
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

from .case_viewer import _rows, notebook_browser, MAX_DOCUMENT_BYTES, MAX_TEXT_BYTES, MAX_MEDIA_BYTES
from .question_review_viewer import _question_review_snapshot, _question_review_payload
from .run_tables import RunTables


def latest_snapshot(project, review_runs, cohort_source, *, authoring_run=None):
    """冻结各来源当前已提交版本；同概念取最后来源，包括 hold 和技术失败。"""
    project = Path(project); owner = project / 'benchmark/t2i/v2'
    if not 1 <= len(review_runs) <= 8 or len(set(review_runs)) != len(review_runs):
        raise ValueError('Latest view requires 1..8 distinct review runs in explicit priority order')
    cohort = _rows(cohort_source, ['concept', 'selection_rank'])
    cohort.sort(key=lambda row: row['selection_rank'])
    if len({row['concept'] for row in cohort}) != len(cohort):
        raise ValueError('Latest view cohort contains duplicate concepts')
    scope = {row['concept'] for row in cohort}
    snapshots = {}; latest = {}; baseline = {}
    for run in review_runs:
        snapshot = _question_review_snapshot(project, run)
        snapshots[run] = snapshot
        short = _rows(snapshot['ref'], ['concept', 'source_task_id', 'review_status',
            'original_instruction', 'instruction'])
        if len({row['concept'] for row in short}) != len(short):
            raise ValueError('A review run contains duplicate concepts')
        for row in short:
            if row['concept'] not in scope:
                raise ValueError('Review contains a concept outside the declared cohort')
            baseline.setdefault(row['concept'], {'run': run, 'source_task_id': row['source_task_id'],
                'instruction': row['original_instruction']})
            latest[row['concept']] = {**row, 'source_run': run, 'kind': 'review'}
    authoring = None
    if authoring_run is not None:
        authoring = RunTables(project.parent,
            str(owner / 'datasets' / f'records__{authoring_run}.lance')).load() or {}
        for row in _rows(authoring.get('designs'), ['concept', 'status', 'reason']):
            if row['concept'] not in scope:
                raise ValueError('Authoring contains a concept outside the declared cohort')
            if row['status'] != 'candidate':
                # 已知最新作者不足或失败也保留，不能悄悄回退到旧题。
                latest[row['concept']] = {'concept': row['concept'], 'kind': 'authoring',
                    'source_run': authoring_run, 'review_status': row['status'], 'reason': row['reason']}
    overview = []
    for item in cohort:
        row = latest.get(item['concept'], {'concept': item['concept'], 'kind': 'missing',
            'review_status': 'unreviewed', 'reason': '尚无已提交的出题或审核结果。'})
        prior = baseline.get(item['concept'])
        modified = (row['kind'] == 'review' and row['review_status'] == 'ready'
            and prior is not None and row['instruction'] != prior['instruction'])
        overview.append({**row, 'selection_rank': item['selection_rank'], 'modified': modified})
    return {'overview': overview, 'snapshots': snapshots, 'baseline': baseline,
        'cohort_source': cohort_source, 'authoring_run': authoring_run,
        'authoring_state': authoring, 'review_runs': list(review_runs)}


def build_latest_browser(project, snapshot, *, page=1, page_size=10, query='', modified_only=False):
    """只加载全局筛选后这一页；左侧为原正式题，右侧为最新定稿。"""
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 20:
        raise ValueError('Latest view requires a positive page and page_size in 1..20')
    overview = snapshot['overview']; query = query.strip().casefold()
    filtered = [row for row in overview if (not query or query in row['concept'].casefold())
        and (not modified_only or row['modified'])]
    pages = max(1, (len(filtered) + page_size - 1) // page_size)
    if page > pages:
        raise ValueError('Latest view page exceeds the selected scope')
    selected = filtered[(page - 1) * page_size:page * page_size]
    by_concept = {}; media = {}
    for run in snapshot['review_runs']:
        chosen = [row for row in selected if row.get('source_run') == run and row['kind'] == 'review']
        if not chosen:
            continue
        local = {**snapshot['snapshots'][run], 'overview': chosen}
        payload = _question_review_payload(project, run, snapshot=local, page_size=20)
        for case in payload['cases']:
            base = snapshot['baseline'][case['concept']]
            if base['run'] != run:
                ref = snapshot['snapshots'][base['run']]['ref']
                predicate = "source_task_id = '" + base['source_task_id'].replace("'", "''") + "'"
                original = _rows(ref, ['original_instruction', 'original_test_points_json'], predicate=predicate)
                if len(original) != 1:
                    raise ValueError('Original question is missing from its frozen source')
                case['review_input_instruction'] = case['original_instruction']
                case.update(original[0])
            case['requires_new_answer'] = (case['instruction'] != base['instruction']) if case['review_status'] == 'ready' else None
            case['source_run'] = run
            by_concept[case['concept']] = case
        for key, value in payload['media'].items():
            if key in media and media[key] != value:
                raise ValueError('Conflicting preview media for the same image')
            media[key] = value
    cases = []
    for row in selected:
        if row['kind'] == 'review':
            cases.append(by_concept[row['concept']])
        else:
            cases.append({'concept': row['concept'], 'source_run': row.get('source_run'),
                'review_status': row['review_status'], 'review_reason': row['reason'],
                'original_instruction': '', 'original_test_points_json': '[]', 'instruction': '',
                'requirements_json': '[]', 'requires_new_answer': None, 'result': None,
                'actual_request': None, 'actual_images': [], 'operator_observations': [],
                'native_tool_events': [], 'available_references': [], 'actual_parameters': None})
    meta = {'run': '全量最新题单', 'updated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'phase': 'latest_committed_results',
        'prompt_versions': sorted({case['actual_parameters']['prompt_version'].split('/')[0]
            for case in cases if (case.get('actual_parameters') or {}).get('prompt_version')}),
        'expected': len(overview), 'recorded': sum(row['kind'] != 'missing' for row in overview),
        'counts': dict(Counter(row['review_status'] for row in overview)),
        'modified': sum(row['modified'] for row in overview), 'matched': len(filtered),
        'shown': len(cases), 'page': page, 'pages': pages, 'page_size': page_size,
        'comparison_basis': '左侧为原正式题；右侧为按显式来源顺序选取的最新定稿。',
        'cohort_source': snapshot['cohort_source'],
        'sources': {run: value['ref'] for run, value in snapshot['snapshots'].items()},
        'authoring_source': (snapshot.get('authoring_state') or {}).get('designs')}
    payload = {'meta': meta, 'cases': cases, 'media': media}
    if len(json.dumps(cases, ensure_ascii=False).encode()) > MAX_TEXT_BYTES:
        raise ValueError('Latest view text exceeds 16MiB; reduce page size')
    if sum(len(value) for value in media.values()) > MAX_MEDIA_BYTES:
        raise ValueError('Latest view media exceeds 64MiB; reduce page size')
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    document = Path(__file__).with_name('question_review_viewer.html').read_text().replace('__REVIEW_DATA__', encoded)
    if len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('Latest view document exceeds 96MiB; reduce page size')
    return document, meta


def latest_browser_controls(project, review_runs, cohort_source, *, authoring_run=None, page_size=10, height=4000):
    """全局概念筛选、仅改题筛选和分页；刷新只读取表，不调用模型。"""
    import ipywidgets as widgets
    project = Path(project)
    snapshot = latest_snapshot(project, review_runs, cohort_source, authoring_run=authoring_run)
    search = widgets.Text(description='全量搜概念', continuous_update=False,
        placeholder='输入概念后回车，搜索全部300个概念', layout=widgets.Layout(width='520px'))
    modified = widgets.Checkbox(description='仅看题面有修改的题', value=False)
    picker = widgets.Dropdown(description='明细批次', layout=widgets.Layout(width='700px'))
    refresh = widgets.Button(description='刷新最新题单', icon='refresh')
    previous = widgets.Button(description='上一批'); following = widgets.Button(description='下一批')
    status = widgets.HTML(); view = widgets.HTML(); handles = {'busy': False}
    directory = project / 'benchmark/t2i/v2/runs/question_review_latest_view'
    directory.mkdir(parents=True, exist_ok=True)

    def options():
        rows = [row for row in snapshot['overview'] if search.value.strip().casefold() in row['concept'].casefold()
            and (not modified.value or row['modified'])]
        return [(f'{i//page_size+1}：{i+1}–{min(i+page_size,len(rows))} / {len(rows)} · '+rows[i]['concept'], i//page_size+1)
            for i in range(0, len(rows), page_size)] or [('没有匹配概念', 1)]

    def render(_=None):
        if handles['busy']:
            return
        document, meta = build_latest_browser(project, snapshot, page=picker.value, page_size=page_size,
            query=search.value, modified_only=modified.value)
        relative = f'runs/question_review_latest_view/question_review_page_{picker.value:03d}.html'
        target = project / 'benchmark/t2i/v2' / relative
        target.write_text(document)
        status.value = (f"<b>全部 {meta['expected']} 个概念 · 最新状态 {meta['counts']} · 题面修改 {meta['modified']} 题</b>"
            f"<br>当前筛选 {meta['matched']} 项 · 第 {meta['page']} / {meta['pages']} 批 · 左：原正式题，右：最新定稿。")
        previous.disabled = picker.value <= 1; following.disabled = picker.value >= meta['pages']
        view.value = notebook_browser(document, relative, height=height)

    def reset(_=None):
        handles['busy'] = True
        try:
            picker.options = options(); picker.value = 1
        finally:
            handles['busy'] = False
        render()

    def reload_results(_):
        nonlocal snapshot
        snapshot = latest_snapshot(project, review_runs, cohort_source, authoring_run=authoring_run)
        reset()

    picker.observe(render, names='value'); search.observe(reset, names='value'); modified.observe(reset, names='value')
    refresh.on_click(reload_results)
    previous.on_click(lambda _: setattr(picker, 'value', picker.value - 1))
    following.on_click(lambda _: setattr(picker, 'value', picker.value + 1))
    reset()
    return widgets.VBox([widgets.HBox([refresh, previous, following]), search, modified, picker, status, view])
