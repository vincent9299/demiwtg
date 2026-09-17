"""Isolated, user-authorized GPU handoff; CLI: --jobs JOBS.jsonl --out DIR.

No model is started on import. Session records live below OUT/session/. A shared
new-version lock prevents overlapping sessions. SIGINT/SIGTERM trigger cleanup
and restoration; SIGKILL or host loss require recovery from resume_server.json.
"""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request
try:
    from .bagel_runner import publish, encoded
except ImportError:
    from bagel_runner import publish, encoded

REPO = _ARCHIVE_ROOT
BACKGROUND = REPO / 'state/curation/image_preannotation_v1'
STATE = REPO / 'state/curation/knowledge_application_v1'
RUNNER = (_ARCHIVE_ROOT/'curation/archive/shared/bagel_runner.py')
PYTHON = REPO.parent / 'env-bagel/bin/python'
MODEL_PATH = REPO.parent / 'models/Qwen3.8-27B'
MODEL = 'qwen3.8-27b'
LAUNCHER = REPO / 'curation/run_image_pipeline.sh'


def process(pid):
    """PID identity includes start ticks, protecting against PID reuse."""
    try:
        root = Path('/proc') / str(pid)
        stat = root.joinpath('stat').read_text().rsplit(') ', 1)[1].split()
        if stat[0] == 'Z':
            return None
        return dict(pid=int(pid), start=stat[19], ppid=int(stat[1]), sid=int(stat[3]),
                    command=root.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0'),
                    cwd=str(root.joinpath('cwd').resolve(strict=True)))
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def processes():
    return [p for entry in Path('/proc').iterdir() if entry.name.isdigit()
            if (p := process(entry.name))]


def same(p):
    current = process(p['pid'])
    return current is not None and all(current[k] == p[k] for k in ('start', 'command', 'cwd'))


def option(command, flag):
    try:
        return command[command.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def matching(kind):
    found = []
    for p in processes():
        c = p['command']
        if p['cwd'] != str(REPO):
            continue
        if kind == 'server':
            ok = ('serve' in c and str(MODEL_PATH) in c and
                  option(c, '--served-model-name') == MODEL and option(c, '--port') == '8000')
        elif kind == 'launcher':
            ok = any(x in (str(LAUNCHER), 'curation/run_image_pipeline.sh') for x in c)
        else:
            module = 'curation.image_preannotate' if kind == 'worker' else 'curation.image_supervisor'
            target = option(c, '--run')
            run = (Path(p['cwd']) / target).resolve() if target else BACKGROUND
            ok = option(c, '-m') == module and run == BACKGROUND
            if kind == 'worker':
                ok = ok and 'run' in c
        if ok:
            found.append(p)
    if len(found) > 1:
        raise RuntimeError(f'Multiple exact {kind} processes: {[p["pid"] for p in found]}')
    return found[0] if found else None


def health():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8000/v1/models', timeout=5) as response:
            return [m['id'] for m in json.load(response)['data']] == [MODEL]
    except (OSError, ValueError, KeyError):
        return False


def done():
    return int(json.loads((BACKGROUND / 'progress.json').read_text())['images']['done'])


def wait(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(2)
    return False


def stop_exact(items, timeout=90):
    """Signal only captured identities; never pattern-based kill or pkill."""
    for p in items:
        if same(p):
            try:
                os.kill(p['pid'], signal.SIGTERM)
            except ProcessLookupError:
                pass
    if not wait(lambda: all(not same(p) for p in items), timeout):
        for p in items:
            if same(p):
                try:
                    os.kill(p['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if not wait(lambda: all(not same(p) for p in items), 30):
            raise RuntimeError('Captured processes did not exit')


def descendants(root):
    rows = processes()
    selected = {root['pid']: root}
    changed = True
    while changed:
        changed = False
        for p in rows:
            if p['ppid'] in selected and p['pid'] not in selected:
                selected[p['pid']] = p
                changed = True
    return list(selected.values())


def spawn(command, cwd, env, logfile):
    with logfile.open('ab') as log:
        return subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    jobs, out = args.jobs.resolve(strict=True), args.out.resolve()
    record_dir = out / 'session'
    record_dir.mkdir(parents=True, exist_ok=True)
    if (record_dir / 'resume_server.json').exists():
        raise RuntimeError('Session already attempted in this output directory; inspect it and use a new run for another GPU session')
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'gpu_session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (BACKGROUND / 'STOP').exists():
            raise RuntimeError('Background is already paused; no ownership to resume it')
        # Validate all jobs and input hashes before touching the background task.
        subprocess.run([str(PYTHON), str(RUNNER), '--jobs', str(jobs), '--out', str(out),
                        '--validate-only'], cwd=REPO, check=True)
        server = matching('server')
        if server is None or not health():
            raise RuntimeError('Expected exact healthy original Qwen service is required')
        launcher, supervisor, worker = (matching(k) for k in ('launcher', 'supervisor', 'worker'))
        if supervisor is None or worker is None:
            raise RuntimeError('Expected original supervisor and annotation worker')
        # Keep original environment only in memory: it can contain credentials.
        original_env = dict(item.split('=', 1) for item in
                            Path(f'/proc/{server["pid"]}/environ').read_bytes().decode().split('\0')
                            if '=' in item)
        initial_done = done()
        original = dict(server=server, launcher=launcher, supervisor=supervisor, worker=worker,
                        done_before=initial_done, environment_saved='memory_only')
        publish(record_dir / 'resume_server.json', encoded(original))

        def event(stage, **extra):
            row = dict(stage=stage, updated=time.time(), pid=os.getpid(), **extra)
            tmp = record_dir / 'session.json.tmp'
            tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
            tmp.replace(record_dir / 'session.json')
            with (record_dir / 'events.jsonl').open('a') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            print(json.dumps(row, ensure_ascii=False), flush=True)

        def interrupted(signum, _frame):
            raise InterruptedError(f'Signal {signum}: restoring background')

        previous_handlers = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
        children = []
        qwen_tree = []
        release_started = False
        owned = {}
        failure = None
        restored = False
        paused = False
        try:
            # Cooperative STOP drains worker; supervisor and launcher then exit.
            paused = True
            (BACKGROUND / 'STOP').touch(exist_ok=False)
            event('draining_background', done_before=initial_done)
            if not wait(lambda: all(matching(k) is None for k in ('worker', 'supervisor', 'launcher')), 420):
                raise RuntimeError('Background did not fully drain; Qwen left untouched')
            if not same(server):
                raise RuntimeError('Original Qwen identity changed while draining')
            qwen_tree = descendants(server)
            event('stopping_original_qwen', server_pid=server['pid'])
            release_started = True
            stop_exact(qwen_tree)
            if matching('server') is not None:
                raise RuntimeError('Another matching server appeared')
            event('running_bagel', gpus=[0, 1], jobs=str(jobs))
            for shard in (0, 1):
                env = os.environ.copy()
                env['CUDA_VISIBLE_DEVICES'] = str(shard)
                child = spawn([str(PYTHON), '-u', str(RUNNER), '--jobs', str(jobs), '--out', str(out),
                               '--shard-id', str(shard), '--num-shards', '2'], REPO, env,
                              record_dir / f'bagel_shard_{shard}.log')
                children.append(child)
            while True:
                # New sessions identify detached descendants even after their root exits.
                for p in processes():
                    if p['sid'] in {child.pid for child in children}:
                        owned[(p['pid'], p['start'])] = p
                codes = [child.poll() for child in children]
                if any(code is not None and code != 0 for code in codes):
                    raise RuntimeError(f'BAGEL shard failed: {codes}')
                if all(code == 0 for code in codes):
                    break
                time.sleep(2)
            event('generation_finished')
        except BaseException as exc:
            failure = f'{type(exc).__name__}: {exc}'
            event('generation_failed', error=failure)
        finally:
            # Repeated termination must not interrupt the restoration transaction.
            for sig in previous_handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                for p in processes():
                    if p['sid'] in {child.pid for child in children}:
                        owned[(p['pid'], p['start'])] = p
                stop_exact(list(owned.values()))
                for child in children:
                    child.wait(timeout=30)
                if paused:
                    # A signal may have interrupted the original tree shutdown.
                    if release_started:
                        stop_exact(qwen_tree)
                    event('restoring_original_qwen')
                    current = matching('server')
                    if current is None:
                        spawn(server['command'], server['cwd'], original_env,
                              record_dir / 'restored_qwen.log')
                    elif current['command'] != server['command'] or current['cwd'] != server['cwd']:
                        raise RuntimeError('Conflicting Qwen service during restore; not replacing it')
                    if not wait(health, 1200):
                        raise RuntimeError('Original Qwen health did not recover')
                    # Any old launcher/supervisor must be gone before removing STOP.
                    if not wait(lambda: all(matching(k) is None for k in ('supervisor', 'launcher', 'worker')), 420):
                        raise RuntimeError('Previous background processes still draining')
                    before_resume = done()
                    (BACKGROUND / 'STOP').unlink(missing_ok=True)
                    env = os.environ.copy()
                    env['CURATION_PYTHON'] = supervisor['command'][0]
                    spawn(['bash', str(LAUNCHER)], REPO, env, record_dir / 'restored_supervisor.log')
                    restored = wait(lambda: health() and matching('worker') is not None and done() > before_resume, 1200)
                    if not restored:
                        raise RuntimeError('Background health/worker/done-increase verification failed')
                    event('restoration_verified', done_before_resume=before_resume, done_after=done())
            except BaseException as exc:
                restore_error = f'{type(exc).__name__}: {exc}'
                failure = f'{failure}; restore: {restore_error}' if failure else restore_error
            finally:
                event('completed' if failure is None and restored else 'needs_inspection',
                      failure=failure, preannotation_restored=restored)
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)
        if failure or not restored:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
