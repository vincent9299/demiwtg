#!/usr/bin/env python3
"""Phase B v2：3 并行 worker，各自批量走 cn1 内网免费链路，断点续传。"""
import json, os, subprocess, tarfile, io, time, threading, queue

JOBS = json.load(open('/yzp/zhaozy/yangzepeng/0905/demiwtg/_staging/fleet202609/relay_jobs.json'))
DST = os.environ.get('DEMIWTG_DATASETS_ROOT', '/yzp/zhaozy/yangzepeng/0905/datasets') + '/demiwtg/blobs'
BATCH = 100

def pending():
    out = []
    for j in JOBS:
        p = f"{DST}/{j['sha'][:2]}/{j['sha']}.{j['ext']}"
        if os.path.exists(p) and (j['size'] == 0 or os.path.getsize(p) == j['size']):
            continue
        out.append(j)
    return out

q = queue.Queue()
lock = threading.Lock()
stats = {'pulled': 0, 'fail': 0}

def worker(wid):
    while True:
        try:
            batch = q.get(timeout=5)
        except queue.Empty:
            return
        try:
            p = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'cn1', 'python3 /tmp/gz_stream.py'],
                               input=json.dumps(batch).encode(), capture_output=True, timeout=1800)
            if p.returncode == 0 and p.stdout:
                tf = tarfile.open(fileobj=io.BytesIO(p.stdout), mode='r|')
                tf.extractall(DST)
                with lock: stats['pulled'] += len(batch)
        except Exception as e:
            with lock: stats['fail'] += 1
            print(f'w{wid}: batch error {e}', flush=True)

for attempt in range(300):
    pd = pending()
    if not pd:
        print('ALL_DONE', flush=True)
        break
    print(f'pass {attempt}: pending={len(pd)} ({time.strftime("%H:%M:%S")})', flush=True)
    batches = [pd[i:i+BATCH] for i in range(0, len(pd), BATCH)]
    for b in batches:
        q.put(b)
    ths = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    print(f'pass {attempt}: pulled~{stats["pulled"]} fail={stats["fail"]}', flush=True)
    time.sleep(60)
