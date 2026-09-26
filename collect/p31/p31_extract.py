# 16 路并行 P31 抽取:分发器轮询写 fifo,worker 各自解析
# 用法: 先 mkfifo,起 workers,再 cat latest-all.json.gz | pigz -dc | python3 p31_extract.py dispatch
import os, sys, re, threading

NF = 16
FIFO = [f"/tmp/p31f{k}" for k in range(NF)]

def worker(k):
    out = open(f"/home/ubuntu/p31_out_{k}.tsv", "w", buffering=1 << 20)
    n31 = re.compile(r'"numeric-id":(\d+)')
    n = 0
    with open(FIFO[k], "r", buffering=1 << 20) as f:
        for line in f:
            i = line.find('"id":"Q')
            if i < 0:
                continue
            p = line.find('"P31":')
            if p < 0:
                continue
            e = p + 2000   # 首段=主类,粗口径足够
            ids = n31.findall(line[p:e])
            if not ids:
                continue
            j = line.find('"', i + 6)
            out.write(line[i + 6:j] + "\t" + ",".join(ids[:8]) + "\n")
            n += 1
            if n % 5_000_000 == 0:
                print(f"w{k}: {n:,}", flush=True)
    out.close()
    print(f"w{k} DONE {n:,}", flush=True)

if sys.argv[1] == "workers":
    for k in range(NF):
        os.mkfifo(FIFO[k]) if not os.path.exists(FIFO[k]) else None
    ts = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(NF)]
    for t in ts: t.start()
    print("WORKERS_UP", flush=True)
    import time
    while True: time.sleep(60)
elif sys.argv[1] == "dispatch":
    import io
    bufs = [open(f, "w", buffering=1 << 20) for f in FIFO]
    k = 0; total = 0
    stdin = io.open(sys.stdin.fileno(), "rb", buffering=1 << 22, closefd=False)
    tail = b""
    while True:
        chunk = stdin.read(8 << 20)
        if not chunk:
            break
        chunk = tail + chunk
        nl = chunk.rfind(b"\n")
        if nl < 0:
            tail = chunk; continue
        tail = chunk[nl+1:]
        for line in chunk[:nl].split(b"\n"):
            bufs[k].write(line.decode("utf-8", "replace") + "\n")
            k = (k + 1) % NF
            total += 1
    if tail:
        bufs[k].write(tail.decode("utf-8", "replace") + "\n")
    for b in bufs: b.close()
    print(f"DISPATCH_DONE {total:,}", flush=True)
