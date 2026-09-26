#!/usr/bin/env python3
"""Lake-side puller: GZ relay1m/ -> /yzp sample_1m_images/<blob_path>.
sha256 verified, checkpointed, multi-round 404-tolerant (chases copy frontier).
Decoupled: NTHREADS GET workers + NWRITERS NFS writers."""
import importlib.util, sys, os, time, hashlib, threading, queue
_spec = importlib.util.spec_from_file_location("cosio", "/yzp/zhaozy/yangzepeng/0905/demiflow/demiflow/collect/cosio.py")
cosio = importlib.util.module_from_spec(_spec); sys.modules["cosio"] = cosio; _spec.loader.exec_module(cosio)
_gz = cosio.COSIO(cosio.COSCreds.from_file("/tmp/cos_creds"), cosio.build_host("lhcos-cee54-1256345599", "ap-guangzhou"))
def cos_get(key):
    return _gz.call("GET", key, timeout=180.0)

BASE = "/yzp/zhaozy/yangzepeng/0905/demiwtg"
MANIFEST = f"{BASE}/collect/sample_1m_transfer/manifest_1m_v2all.tsv"
OUTROOT = f"{BASE}/sample_1m_images"
DONE = f"{BASE}/collect/sample_1m_transfer/done.sha"
FAIL = f"{BASE}/collect/sample_1m_transfer/fail.sha"
RELAY = "relay1m/"
NTHREADS = 64
NWRITERS = 6
ROUND_SLEEP = 120

done_set = set()
try:
    with open(DONE) as f:
        for l in f:
            done_set.add(l.strip())
except FileNotFoundError:
    pass

tasks = []
tot_bytes_manifest = 0
with open(MANIFEST) as f:
    next(f)
    for l in f:
        p = l.rstrip("\n").split("\t")
        sha, bp, sz = p[0], p[1], int(p[5])
        tot_bytes_manifest += sz
        if sha in done_set:
            continue
        tasks.append((sha, bp, sz))
print(f"[start] pending={len(tasks):,} done={len(done_set):,} manifest_bytes={tot_bytes_manifest/1e12:.2f}TB threads={NTHREADS}+{NWRITERS}w", flush=True)

waiting = set()
lock = threading.Lock()
n_ok = n_fail = 0
bytes_ok = 0
t0 = time.time()
done_f = open(DONE, "a", buffering=1)
fail_f = open(FAIL, "a", buffering=1)
wq = queue.Queue(maxsize=256)

class _State:
    last_prog = 0
state = _State()

def _progress_locked():
    tot = n_ok + n_fail
    if tot - state.last_prog >= 10000:
        dt = time.time() - t0
        rate = bytes_ok / dt / 1e6 if dt > 0 else 0
        eta_d = (tot_bytes_manifest - bytes_ok) / (bytes_ok + 1) * dt / 86400
        state.last_prog = tot
        print(f"[prog] ok={n_ok:,} fail={n_fail:,} got={bytes_ok/1e12:.3f}TB rate={rate:.1f}MB/s eta={eta_d:.1f}d elapsed={dt/60:.0f}m", flush=True)

def _land(fp, body):
    d = os.path.dirname(fp)
    for attempt in range(6):
        try:
            os.makedirs(d, exist_ok=True)
            tmp = fp + ".part"
            with open(tmp, "wb") as w:
                w.write(body)
            os.replace(tmp, fp)
            return True
        except OSError:
            time.sleep(0.3 + 0.4 * attempt)
    return False

def writer():
    global n_ok, n_fail, bytes_ok
    while True:
        item = wq.get()
        if item is None:
            return
        sha, bp, sz, body = item
        if _land(os.path.join(OUTROOT, bp), body):
            with lock:
                n_ok += 1
                bytes_ok += sz
                done_f.write(sha + "\n")
                _progress_locked()
        else:
            with lock:
                n_fail += 1
                fail_f.write(f"{sha}\t{bp}\tland_fail\n")
                _progress_locked()

def process(sha, bp, sz):
    global n_fail
    try:
        st, hd, body = cos_get(RELAY + bp)
    except Exception:
        st = -1
    if st == 404:
        with lock:
            waiting.add((sha, bp, sz))
        return
    if st == 200 and body is not None and hashlib.sha256(body).hexdigest() == sha:
        wq.put((sha, bp, sz, body))
    else:
        with lock:
            n_fail += 1
            fail_f.write(f"{sha}\t{bp}\tst={st}\n")
            _progress_locked()

def run_round(round_tasks):
    q = queue.Queue()
    for t in round_tasks:
        q.put(t)
    writers = [threading.Thread(target=writer, daemon=True) for _ in range(NWRITERS)]
    for w in writers:
        w.start()

    def worker():
        while True:
            try:
                sha, bp, sz = q.get_nowait()
            except queue.Empty:
                return
            try:
                process(sha, bp, sz)
            except Exception as e:
                with lock:
                    fail_f.write(f"{sha}\t{bp}\tEXC:{str(e)[:60]}\n")
            q.task_done()

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(NTHREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for _ in range(NWRITERS):
        wq.put(None)
    for w in writers:
        w.join()

# NFS 瞬时 ENOENT 规避:预建全部分片目录
for zone in ("blobs", "blobs-nc"):
    for i in range(256):
        os.makedirs(os.path.join(OUTROOT, zone, "%02x" % i), exist_ok=True)
print("[dirs] shard dirs pre-created", flush=True)

round_no = 0
while tasks:
    round_no += 1
    n_before = n_ok
    print(f"[round {round_no}] tasks={len(tasks):,}", flush=True)
    run_round(tasks)
    print(f"[round {round_no}] done_this_round={n_ok - n_before:,} total_ok={n_ok:,} fail={n_fail:,} got={bytes_ok/1e12:.3f}TB elapsed={(time.time()-t0)/60:.0f}m", flush=True)
    tasks = list(waiting)
    waiting = set()
    if tasks:
        time.sleep(ROUND_SLEEP)

print(f"[END] ok={n_ok:,} fail={n_fail:,} got={bytes_ok/1e12:.3f}TB elapsed={(time.time()-t0)/60:.0f}m avg={bytes_ok/(time.time()-t0)/1e6:.1f}MB/s", flush=True)
