"""Supervise the frozen local Qwen image run. STOP pauses worker and supervisor.

Run with the same Python environment as vLLM. Logs/status stay in the run directory.
Only adopt processes matching the exact model path / worker module and run cwd.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from curation.common import ROOT
from curation.image_preannotate import DEFAULT_RUN, atomic_json

MODEL_PATH = ROOT.parent / 'models/Qwen3.8-27B'
MODEL = 'qwen3.8-27b'

def process_args(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        if stat[0] == 'Z': return []
        return Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
    except (OSError, UnicodeError): return []

def find_process(kind, run):
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        args = process_args(entry.name)
        if kind == 'server':
            ok = ('serve' in args and str(MODEL_PATH) in args and
                  '--served-model-name' in args and MODEL in args and '--port' in args and
                  args[args.index('--port') + 1] == '8000')
        else:
            ok = 'curation.image_preannotate' in args and 'run' in args
            if ok:
                try:
                    cwd = Path(f'/proc/{entry.name}/cwd').resolve()
                    target = Path(args[args.index('--run')+1]) if '--run' in args else DEFAULT_RUN
                    if not target.is_absolute(): target = cwd / target
                    ok = cwd == ROOT and target.resolve() == run.resolve()
                except (OSError, ValueError, IndexError): ok = False
        if ok: found.append(int(entry.name))
    if len(found) > 1: raise RuntimeError(f'Multiple {kind} processes: {found}')
    return found[0] if found else None

def health():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8000/v1/models', timeout=5) as r:
            ids = [m['id'] for m in json.load(r)['data']]
        return 'ready' if ids == [MODEL] else 'wrong_model'
    except Exception: return 'unavailable'

def remaining(run):
    with sqlite3.connect(f'file:{run / "annotations.sqlite"}?mode=ro', uri=True, timeout=30) as c:
        return c.execute("SELECT 1 FROM images WHERE status IN ('pending','running','error') AND attempts < 3 LIMIT 1").fetchone() is not None

def spawn(command, logfile):
    env = os.environ.copy()
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    with logfile.open('ab') as log:
        return subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, default=DEFAULT_RUN)
    p.add_argument('--concurrency', type=int, default=12)
    a = p.parse_args(); run = a.run.resolve()
    protocol = json.loads((run/'protocol.json').read_text())
    if protocol['model'] != MODEL or protocol['base_url'] != 'http://127.0.0.1:8000/v1':
        raise ValueError('Supervisor only supports the frozen local Qwen run')
    with (run/'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = False
        def stopping(*_):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, stopping); signal.signal(signal.SIGINT, stopping)
        children = []; starts = {'server': 0, 'worker': 0}; next_start = {'server': 0, 'worker': 0}
        previous = None; last_progress = time.monotonic(); progress_key = None
        unhealthy_since = None; term_sent = {}
        def event(state, **extra):
            nonlocal previous
            data = dict(updated=time.time(), pid=os.getpid(), state=state, starts=starts, **extra)
            atomic_json(run/'supervisor.json', data)
            key = (state, extra.get('server_pid'), extra.get('worker_pid'))
            if key != previous: print(json.dumps(data), flush=True); previous = key
        while not stop:
            now = time.monotonic()
            children[:] = [child for child in children if child.poll() is None]
            server = find_process('server', run); worker = find_process('worker', run)
            if (run/'STOP').exists():
                event('paused', server_pid=server, worker_pid=worker)
                if not worker: return
                time.sleep(5); continue
            if not worker and not remaining(run):
                event('completed_with_separate_failure_records', server_pid=server); return
            ready = health()
            if ready == 'wrong_model':
                event('blocked_wrong_model', server_pid=server, worker_pid=worker)
                time.sleep(30); continue
            if ready == 'unavailable':
                unhealthy_since = unhealthy_since or now
                # A matching live server gets 20 minutes to load or recover.
                if server and now - unhealthy_since > 1200:
                    if server not in term_sent:
                        os.kill(server, signal.SIGTERM); term_sent[server] = now
                    elif now - term_sent[server] > 120:
                        os.kill(server, signal.SIGKILL)
                if not server and now >= next_start['server']:
                    command = [sys.executable, '-m', 'vllm.entrypoints.cli.main', 'serve', str(MODEL_PATH),
                               '--served-model-name', MODEL, '--host', '127.0.0.1', '--port', '8000',
                               '--gpu-memory-utilization', '0.92', '--tensor-parallel-size', '2']
                    child = spawn(command, run/'server.log'); children.append(child)
                    starts['server'] += 1
                    next_start['server'] = now + min(1800, 60 * 2**min(starts['server'], 5))
                    unhealthy_since = now
                event('waiting_for_server', server_pid=server, worker_pid=worker)
            else:
                unhealthy_since = None
                if not worker and now >= next_start['worker']:
                    child = spawn([sys.executable, '-u', '-m', 'curation.image_preannotate', 'run',
                                   '--run', str(run), '--concurrency', str(a.concurrency)], run/'worker.log')
                    children.append(child); starts['worker'] += 1
                    next_start['worker'] = now + min(1800, 30 * 2**min(starts['worker'], 6))
                    last_progress = now; progress_key = None
                if worker:
                    try:
                        progress = json.loads((run/'progress.json').read_text())
                        key = (progress.get('pid'), progress.get('session_completed'),
                               progress.get('images', {}).get('missing'), progress.get('images', {}).get('invalid'))
                        if key != progress_key: progress_key = key; last_progress = now
                    except (OSError, ValueError): pass
                    if now - last_progress > 1800:
                        if worker not in term_sent:
                            os.kill(worker, signal.SIGTERM); term_sent[worker] = now
                        elif now - term_sent[worker] > 300: os.kill(worker, signal.SIGKILL)
                event('running', server_pid=server, worker_pid=worker)
            time.sleep(15)
        # Signal means pause, so an outer launcher must not silently resume.
        (run/'STOP').touch()
        event('paused_by_signal')

if __name__ == '__main__': main()
