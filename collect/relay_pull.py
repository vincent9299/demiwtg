"""SG COS 轻资产拉回调度器:多机 Range 分块并行,断点续传,组装校验"""
import os, re, subprocess, sys, threading, time, queue

MIRROR = "/yzp/zhaozy/yangzepeng/0905/sg_cos_mirror"
PARTS = os.path.join(MIRROR, ".parts")
HOSTS = ["r1","r2","r3","r4","r5","r6","r7","r8","r10","r11","r12","r13",
         "r14","r15","r16","r17","r18","r19","r20"]
CHUNK = 16 * 1024 * 1024
LOG = open("/tmp/relay_pull.log", "a", buffering=1)

def log(m):
    LOG.write(f"{time.strftime('%H:%M:%S')} {m}\n")

PRIOS = [
    (r"\.md5$|rebuild_report", 0),
    (r"qid_edges/", 1),
    (r"images\.v2\.20260924d", 2),
    (r"qid_concepts|qid_graph|qid_gallery|qid_image_roles", 3),
    (r"deadletter|images\.v2|qid_images\.v2", 4),
    (r"relay1m|wikidata_bridges|quarantine", 5),
    (r".", 9),
]
files = []
for l in open("/tmp/cos_keys_light.tsv"):
    k, s = l.rstrip("\n").split("\t")
    s = int(s)
    if s == 0:
        continue
    p = next((p for pat, p in PRIOS if re.search(pat, k)), 9)
    files.append((p, s, k))
files.sort()
total = sum(s for _, s, _ in files)
log(f"队列 {len(files)} 文件 {total/1e9:.2f}GB")

jobs = queue.Queue()
done_bytes = [0]
lock = threading.Lock()

for p, s, k in files:
    dst = os.path.join(MIRROR, k)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.getsize(dst) == s:
        done_bytes[0] += s
        continue
    n = (s + CHUNK - 1) // CHUNK
    pd = os.path.join(PARTS, k.replace("/", "%"))
    for i in range(n):
        a, b = i * CHUNK, min((i + 1) * CHUNK, s) - 1
        pf = f"{pd}.{i}"
        want = b - a + 1
        if os.path.exists(pf) and os.path.getsize(pf) == want:
            continue
        jobs.put((k, a, b, i, s, pf, dst))

def worker(host):
    while True:
        try:
            k, a, b, i, s, pf, dst = jobs.get_nowait()
        except queue.Empty:
            return
        want = b - a + 1
        for attempt in range(4):
            try:
                r = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=15", host,
                     "python3", "/tmp/pull_range.py", k, str(a), str(b)],
                    stdout=open(pf + ".tmp", "wb"), stderr=subprocess.DEVNULL,
                    timeout=1800)
                if r.returncode == 0 and os.path.getsize(pf + ".tmp") == want:
                    os.rename(pf + ".tmp", pf)
                    with lock:
                        done_bytes[0] += want
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(10 * (attempt + 1))
        else:
            jobs.put((k, a, b, i, s, pf, dst))
            time.sleep(30)
        # 组装检查
        try_assemble(k, s, dst)

def try_assemble(k, s, dst):
    n = (s + CHUNK - 1) // CHUNK
    pd = os.path.join(PARTS, k.replace("/", "%"))
    for i in range(n):
        pf = f"{pd}.{i}"
        if not os.path.exists(pf):
            return
    with open(dst + ".tmp", "wb") as o:
        for i in range(n):
            with open(f"{pd}.{i}", "rb") as f:
                while True:
                    b = f.read(1 << 22)
                    if not b: break
                    o.write(b)
    if os.path.getsize(dst + ".tmp") == s:
        os.rename(dst + ".tmp", dst)
        for i in range(n):
            os.remove(f"{pd}.{i}")
        log(f"DONE {k} ({s:,}B)")

threads = []
for host in HOSTS * 2:
    t = threading.Thread(target=worker, args=(host,), daemon=True)
    t.start(); threads.append(t)
log(f"38 workers up")
while any(t.is_alive() for t in threads):
    time.sleep(60)
    log(f"PROGRESS {done_bytes[0]/1e9:.3f}/{total/1e9:.2f}GB "
        f"({done_bytes[0]/total:.1%}) 队列余 {jobs.qsize()}")
log("ALL_DONE")
