"""只读巡检已启动的 Review；写进度/异常回执，不提交或重试模型调用。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import lance
import psutil
from demiflow.operator_llm.call_ref import read_call
from .case_viewer import _rows
from .question_review_viewer import build_question_review_browser, recorded_codex_response
from .run_tables import RunTables


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def monitor_authoring(project, run_id, interval=30):
    """只读两阶段执行；审题交接后沿用同一进程身份巡检子阶段，不提交模型。"""
    project = Path(project).resolve(); root = project.parent
    owner = project / 'benchmark/t2i/v2'; directory = owner / 'runs' / run_id
    cfg = json.loads((directory / 'config.json').read_text())
    receipt = json.loads((directory / 'process.json').read_text())
    records = RunTables(root, str(owner / 'datasets' / f'records__{run_id}.lance'))
    table = owner / 'datasets' / f'designs__{run_id}.lance'
    child_id = run_id + '__question_review'; child = owner / 'runs' / child_id
    reviewed = False; previous = None; last_change = time.time()
    while True:
        try:
            process = psutil.Process(receipt['pid'])
            alive = process.create_time() == receipt['create_time'] and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            alive = False
        state = records.load() or {}
        version = lance.dataset(str(table)).version if table.exists() else None
        rows = _rows({'uri': str(table), 'version': version}, ['concept', 'status', 'reason']) if version else []
        counts = dict(Counter(row['status'] for row in rows))
        event = {'phase': state.get('phase'), 'counts': counts, 'process_alive': alive,
                 'complete': state.get('complete', False), 'review': state.get('question_review')}
        if event != previous:
            previous = event; last_change = time.time()
            print(json.dumps(event, ensure_ascii=False), flush=True)
        alerts = []
        failed = {key: count for key, count in counts.items() if key not in {'candidate', 'insufficient'}}
        if failed:
            alerts.append({'kind': 'technical_authoring_failures', 'counts': failed})
        if alive and time.time() - last_change > 1200:
            alerts.append({'kind': 'no_result_progress_over_20_minutes'})
        if not alive and not state.get('complete'):
            alerts.append({'kind': 'process_exited_before_complete'})
        snapshot = {'updated_at': datetime.now(timezone.utc).isoformat(), 'run': run_id,
                    'pipeline_pid': receipt['pid'], 'process_alive': alive,
                    'expected': cfg['sample_size'], 'recorded': len(rows), 'counts': counts,
                    'result_version': version, 'cases': rows, 'alerts': alerts, **event}
        save_json(directory / 'monitor_status.json', snapshot)
        if not alive:
            return
        if not reviewed and (child / 'config.json').exists():
            # 子阶段在父进程内执行；回执明确记录观测的实际进程，模型配置读取子阶段 YAML。
            child_cfg = json.loads((child / 'config.json').read_text())
            save_json(child / 'process.json', {**receipt, 'run': child_id, 'observed_parent_run': run_id,
                'sample_size': child_cfg['sample_size'], 'concurrency': child_cfg['concurrency'],
                'agent_sha256': hashlib.sha256(Path(child_cfg['agent_config']).read_bytes()).hexdigest()})
            monitor(project, child_id, interval=interval)
            reviewed = True
            continue
        time.sleep(interval)


def monitor(project, run_id, interval=30):
    project = Path(project).resolve(); root = project.parent
    owner = project / 'benchmark/t2i/v2'; directory = owner / 'runs' / run_id
    cfg = json.loads((directory / 'config.json').read_text())
    if cfg.get('through') == 'author':
        return monitor_authoring(project, run_id, interval=interval)
    receipt = json.loads((directory / 'process.json').read_text())
    records = RunTables(root, str(owner / 'datasets' / f'records__{run_id}.lance'))
    table = owner / 'datasets' / f'question_reviews__{run_id}.lance'
    audit = {}; rows = []; previous_version = None; previous_event = None
    last_change = time.time(); last_render = 0; rendered_version = None; view_error = None
    while True:
        now = time.time()
        try:
            process = psutil.Process(receipt['pid'])
            alive = process.create_time() == receipt['create_time'] and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            alive = False
        state = records.load() or {}
        version = lance.dataset(str(table)).version if table.exists() else None
        if version != previous_version:
            rows = _rows({'uri': str(table), 'version': version}, [
                'source_task_id', 'concept', 'review_status', 'review_reason', 'requires_new_answer',
                'review_call_json']) if version else []
            previous_version = version; last_change = now
            for row in rows:
                key = row['source_task_id']
                if key in audit:
                    continue
                call = json.loads(row['review_call_json'] or '{}')
                if not call.get('response_ref'):
                    continue
                # Failed sessions retain their actual events on the call;
                # there is no committed success response at response_ref.
                response = recorded_codex_response(call, root)
                request = read_call(call['request_ref'], root)
                observations = response.get('observations', call.get('environment', {}).get('observations', []))
                items = [event.get('params', {}).get('item', {}) for event in response.get('events', [])
                         if event.get('method') == 'item/completed']
                audit[key] = {'concept': row['concept'], 'tools': dict(Counter(
                    item['type'] for item in items if item.get('type') in {'commandExecution', 'viewImage', 'webSearch'})),
                    'operator_calls': dict(Counter(item['call']['method'] for item in observations)),
                    'document_reads': sum(item['call']['method'] == 'read_documents'
                        and item['result'].get('status') == 'ok' for item in observations),
                    'requires_native_file_read': request.get('prompt_version', '').split('/')[0] == 't2i-v2-question-review-3',
                    'elapsed_s': response.get('elapsed_s'), 'response_ref': call['response_ref']}
        counts = dict(Counter(row['review_status'] for row in rows)); alerts = []
        failed = {key: count for key, count in counts.items() if key not in {'ready', 'hold'}}
        if failed:
            alerts.append({'kind': 'technical_failures', 'counts': failed})
        no_reads = [key for key, value in audit.items()
                    if value['requires_native_file_read'] and not value['tools'].get('commandExecution')]
        if no_reads:
            alerts.append({'kind': 'missing_native_file_read_events', 'task_ids': no_reads})
        if alive and now - last_change > 1200:
            alerts.append({'kind': 'no_result_progress_over_20_minutes'})
        if hashlib.sha256(Path(cfg['agent_config']).read_bytes()).hexdigest() != receipt['agent_sha256']:
            alerts.append({'kind': 'prompt_file_changed_after_launch'})
        if not alive and not state.get('complete'):
            alerts.append({'kind': 'process_exited_before_complete'})
        if version != rendered_version and (now - last_render >= 60 or not alive):
            try:
                document, _ = build_question_review_browser(project, run_id, page_size=10)
                temporary = directory / 'question_review.html.tmp'; temporary.write_text(document)
                temporary.replace(directory / 'question_review.html')
                rendered_version = version; view_error = None
            except Exception as error:
                view_error = type(error).__name__ + ': ' + str(error)
            last_render = now
        if view_error:
            alerts.append({'kind': 'viewer_refresh_failed', 'detail': view_error})
        snapshot = {'updated_at': datetime.now(timezone.utc).isoformat(), 'run': run_id,
            'pipeline_pid': receipt['pid'], 'process_alive': alive,
            'phase': state.get('phase'), 'complete': state.get('complete', False),
            'expected': cfg['sample_size'], 'recorded': len(rows), 'counts': counts,
            'remaining': cfg['sample_size'] - len(rows), 'result_version': version,
            'requires_new_answer': sum(row['requires_new_answer'] is True for row in rows),
            'native_read_cases': sum(bool(value['tools'].get('commandExecution')) for value in audit.values()),
            'callback_document_read_cases': sum(value['document_reads'] > 0 for value in audit.values()),
            'alerts': alerts, 'cases': [{key: row[key] for key in ('source_task_id', 'concept', 'review_status',
                'review_reason', 'requires_new_answer')} for row in rows], 'native_audit': audit}
        save_json(directory / 'monitor_status.json', snapshot)
        event = {key: snapshot[key] for key in ('process_alive', 'complete', 'recorded', 'counts', 'alerts')}
        if event != previous_event:
            with (directory / 'monitor_events.jsonl').open('a') as stream:
                stream.write(json.dumps({'at': snapshot['updated_at'], **event}, ensure_ascii=False) + '\n')
            print(json.dumps({'at': snapshot['updated_at'], **event}, ensure_ascii=False), flush=True)
            previous_event = event
        if not alive:
            return
        time.sleep(interval)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True); parser.add_argument('--run', required=True)
    args = parser.parse_args()
    monitor(args.project, args.run)
