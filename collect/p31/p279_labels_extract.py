# 16 路并行 P279+labels 抽取:分发器轮询写 fifo,worker 各自解析
# 与 p31_extract.py 同架构;每实体仅当 claims 含 "P279" 时输出(即类实体):
#   qid \t P279上位ids(逗号,首8) \t en标签 \t zh标签(zh→zh-hans→zh-hant)
# 用法: 先起 workers,再 cat latest-all.json.gz | pigz -dc | python3 p279_labels_extract.py dispatch
import os, sys, re, threading

NF = 16
FIFO = [f"/tmp/p279f{k}" for k in range(NF)]

NID = re.compile(r'"numeric-id":(\d+)')


def _val_after(s, m, pat):
    """m 是完整模式 pat(以 '\"value\":\"' 结尾)的起点,取 value 到未转义 '\"'。"""
    i = m + len(pat)
    out = []
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            break
        out.append(c)
        i += 1
    return "".join(out).replace("\t", " ").replace("\n", " ")


def _labels(line):
    """从 labels 块(在 descriptions 之前)取 en 与 zh 标签。"""
    pl = line.find('"labels":')
    if pl < 0:
        return "", ""
    pd = line.find('"descriptions":')
    end = pd if pd > pl else pl + 60000
    blk = line[pl:end]
    en = ""
    pat_en = '"en":{"language":"en","value":"'
    m = blk.find(pat_en)
    if m >= 0:
        en = _val_after(blk, m, pat_en)
    zh = ""
    for pat in ('"zh":{"language":"zh","value":"',
                '"zh-hans":{"language":"zh-hans","value":"',
                '"zh-hant":{"language":"zh-hant","value":"'):
        m = blk.find(pat)
        if m >= 0:
            zh = _val_after(blk, m, pat)
            break
    return en, zh


def worker(k):
    out = open(f"/home/ubuntu/p279_out_{k}.tsv", "w", buffering=1 << 20)
    n = 0
    with open(FIFO[k], "r", buffering=1 << 20) as f:
        for line in f:
            p = line.find('"P279":')
            if p < 0:
                continue
            e = p + 2000
            ids = NID.findall(line[p:e])
            if not ids:
                continue
            i = line.find('"id":"Q')
            if i < 0:
                continue
            j = line.find('"', i + 6)
            qid = line[i + 6:j]
            en, zh = _labels(line)
            out.write(f"{qid}\t{','.join(ids[:8])}\t{en}\t{zh}\n")
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
        bufs[k].write(tail.decode("utf-8", "replace"))
    for b in bufs: b.close()
    print(f"DISPATCH_DONE {total:,}", flush=True)
