"""User-authorized GPU handoff: drain preannotation, run pilot, restore Qwen.

Only use after authorization to interrupt this exact background task.
Status consumed by the diagnostic notebook/operator: RUN/session.json.
"""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import fcntl
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from curation.rag_diagnostic import REPO, RUN, run_pilot, write_json
from curation.image_supervisor import find_process, process_args, health

BACKGROUND = REPO / 'state/curation/image_preannotation_v1'


def event(stage, **values):
    record = dict(stage=stage, updated=time.time(), pid=os.getpid(), **values)
    write_json(RUN / 'session.json', record)
    print(json.dumps(record), flush=True)


def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(5)
    return False


def main():
    global RUN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=RUN)
    args = parser.parse_args()
    RUN = args.run.resolve()
    with (RUN / '.session.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        server = find_process('server', BACKGROUND)
        if server is None:
            raise RuntimeError('Expected original Qwen service is not running')
        original_command = process_args(server)
        original_cwd = Path(f'/proc/{server}/cwd').resolve()
        write_json(RUN / 'resume_server.json', dict(command=original_command, cwd=str(original_cwd)))
        def interrupted(*_):
            raise KeyboardInterrupt('Session interrupted; restore background task')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        generation_ok = False
        failure = None
        try:
            (BACKGROUND / 'STOP').touch()
            event('draining_preannotation')
            if not wait_until(lambda: find_process('worker', BACKGROUND) is None, 360):
                raise RuntimeError('Worker did not drain; Qwen service left untouched')
            # STOP makes supervisor/launcher exit on their next observation.
            time.sleep(20)
            if process_args(server) != original_command:
                raise RuntimeError('Qwen process identity changed')
            event('releasing_qwen_gpu', server_pid=server)
            os.kill(server, signal.SIGTERM)
            if not wait_until(lambda: not process_args(server), 90):
                if process_args(server) == original_command:
                    os.kill(server, signal.SIGKILL)
                if not wait_until(lambda: not process_args(server), 30):
                    raise RuntimeError('Qwen process did not exit')
            time.sleep(10)
            event('running_bagel', n_images=json.loads((RUN / 'plan.json').read_text())['n_images'], gpus=[0, 1])
            run_pilot(gpus=[0, 1], run=RUN)
            generation_ok = True
        except BaseException as exc:
            failure = f'{type(exc).__name__}: {exc}'
            event('pilot_interrupted_or_failed', detail=failure)
        finally:
            # Start the exact prior command, then return ownership to the supervisor.
            event('restoring_preannotation', generation_ok=generation_ok, failure=failure)
            if find_process('server', BACKGROUND) is None:
                server_env = os.environ.copy()
                server_env['PATH'] = str(Path(original_command[0]).parent) + os.pathsep + server_env.get('PATH', '')
                with (BACKGROUND / 'server.log').open('ab') as log:
                    restored = subprocess.Popen(original_command, cwd=original_cwd, env=server_env, stdin=subprocess.DEVNULL,
                                                stdout=log, stderr=log, start_new_session=True)
                print(f'Restoring original Qwen PID {restored.pid}', flush=True)
            (BACKGROUND / 'STOP').unlink(missing_ok=True)
            with (BACKGROUND / 'supervisor.log').open('ab') as log:
                subprocess.Popen(['bash', str(REPO / 'curation/run_image_pipeline.sh')], cwd=REPO,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            ready = wait_until(lambda: health() == 'ready' and find_process('worker', BACKGROUND) is not None, 1200)
            event('completed' if generation_ok and ready else 'needs_inspection',
                  generation_ok=generation_ok, preannotation_restored=ready, failure=failure)


if __name__ == '__main__':
    main()
