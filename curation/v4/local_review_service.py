"""Borrow the preannotation GPUs only around a materialized Gemma review stage."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import httpx
from .contracts import run_lock
from .local_model_comparison_session import ROOT, ANNOT, matching, command, ready


def wait_until(check, seconds, label, process=None):
    deadline = time.monotonic() + seconds
    while not check():
        if process is not None and process.poll() is not None:
            raise RuntimeError(f'{label}: service exited {process.returncode}')
        if time.monotonic() >= deadline:
            raise TimeoutError(label)
        time.sleep(5)


def spawn(command_line, log):
    env = os.environ.copy()
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    with log.open('ab') as stream:
        return subprocess.Popen(command_line, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=stream, start_new_session=True)


def stop_owned(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try: process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=30)


@contextmanager
def image_review_service(run, config, *, needed=True):
    if not needed:
        yield
        return
    model = config['image_review_model']
    if model != 'gemma-4-31b-it' or config['image_review_base_url'].rstrip('/') != 'http://127.0.0.1:8001/v1':
        raise ValueError('Review service requires the tested local Gemma31 endpoint')
    if ready(8001, model):
        yield  # Externally managed healthy service: leave it untouched.
        return
    if config['image_review_service'] != 'borrow':
        raise RuntimeError('Start Gemma31 on 8001, or configure image_review_service=borrow')
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            client.get('http://127.0.0.1:8001/v1/models')
    except httpx.ConnectError:
        pass
    else:
        raise RuntimeError('Port 8001 is occupied; will not replace another service')
    logdir = Path(run) / 'image_review_service'
    logdir.mkdir(parents=True, exist_ok=True)
    def event(status, **details):
        row = {'time': time.time(), 'status': status, **details}
        with (logdir / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(status, flush=True)
    # Shared lock protects GPU lending across formal runs.
    with run_lock(ROOT / 'state/curation/local_review_service'):
        if (ANNOT / 'STOP').exists():
            raise RuntimeError('Preannotation already paused; ownership is unclear')
        servers = matching('vllm.entrypoints.cli.main serve ' + str(ROOT.parent / 'models/Qwen3.8-27B'))
        if len(servers) != 1 or not ready(8000, 'qwen3.8-27b'):
            raise RuntimeError('Expected the original healthy Qwen service')
        pid = servers[0]; original = command(pid)
        started = Path(f'/proc/{pid}/stat').read_text().split()[21]
        had_worker = bool(matching('curation.image_preannotate run --run ' + str(ANNOT)))
        owned = None; stopped = False
        event('before_borrow', original_command=original, had_worker=had_worker)
        try:
            (ANNOT / 'STOP').touch()
            event('draining_preannotation')
            wait_until(lambda: not matching('curation.image_preannotate run --run ' + str(ANNOT)), 600, 'preannotation drain')
            wait_until(lambda: not matching('curation.image_supervisor --run ' + str(ANNOT)), 60, 'supervisor pause')
            if command(pid) != original or Path(f'/proc/{pid}/stat').read_text().split()[21] != started:
                raise RuntimeError('Original service identity changed')
            os.kill(pid, signal.SIGTERM); stopped = True
            wait_until(lambda: not command(pid), 180, 'original service stop')
            cmd = [sys.executable, '-m', 'vllm.entrypoints.cli.main', 'serve', str(ROOT.parent / 'models/gemma-4-31B-it'),
                   '--served-model-name', model, '--host', '127.0.0.1', '--port', '8001', '--tensor-parallel-size', '2',
                   '--gpu-memory-utilization', '0.90', '--max-model-len', '32768', '--max-num-seqs', '2',
                   '--enforce-eager', '--limit-mm-per-prompt', json.dumps({'image': config.get('image_batch_size', 4)})]
            event('starting_review_service', command=cmd)
            owned = spawn(cmd, logdir / 'gemma.log')
            wait_until(lambda: ready(8001, model), 600, 'Gemma startup', owned)
            event('review_service_ready')
            yield
        finally:
            stop_owned(owned)
            if stopped:
                event('restoring_qwen')
                restored = spawn(original, logdir / 'restore_qwen.log')
                wait_until(lambda: ready(8000, 'qwen3.8-27b'), 600, 'Qwen restoration', restored)
            if not ready(8000, 'qwen3.8-27b'):
                raise RuntimeError('Original service not healthy; STOP retained')
            wait_until(lambda: not matching('bash ' + str(ROOT / 'curation/run_image_pipeline.sh')), 60, 'old launcher exit')
            (ANNOT / 'STOP').unlink(missing_ok=True)
            if had_worker:
                spawn(['bash', str(ROOT / 'curation/run_image_pipeline.sh')], logdir / 'restore_preannotation.log')
                wait_until(lambda: bool(matching('curation.image_preannotate run --run ' + str(ANNOT))), 120, 'preannotation resume')
            event('original_resources_restored')
