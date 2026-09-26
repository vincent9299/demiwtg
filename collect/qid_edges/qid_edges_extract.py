# 16 路并行 qid_edges 抽取:P31/P279/P361/P527 四谓词全量边 + 类标签
# 架构同 p31_extract.py(分发器轮询写 fifo,worker 各自解析)。
# 主体不过滤(全量实体,含无图抽象类)——与当年 qid_graph 的本质区别。
# 输出行:
#   边表  from_qid \t pred \t to_qid          (一行一条边;首行=主值)
#   标签  qid \t en \t zh                      (仅有 P279 的实体=类)
# 用法: 先起 workers,再 pigz -dc latest-all.json.gz | python3 qid_edges_extract.py dispatch
import os, sys, re, threading

NF = 16
FIFO = [f"/tmp/qe{k}" for k in range(NF)]
PREDS = tuple(os.environ.get("QP_PREDS", "P31,P279,P361,P527").split(","))
OUT = os.environ.get("QP_OUT", "qid_edges")
NID = re.compile(r'"numeric-id":(\d+)')
NKEY = re.compile(r',"P\d{1,7}":')      # claims 级下一个属性键(限定窗口边界)


def _pred_ids(line, p):
    """谓词起点 p → 本谓词 claims 块内的 numeric-id(到下一 claims 键或 2000 字符)。"""
    m = NKEY.search(line, p + 6)
    end = m.start() if (m and m.start() < p + 2000) else p + 2000
    return NID.findall(line[p:end])


UESC = re.compile(r'u([0-9a-fA-F]{4})')


def _val_after(s, m, pat):
    """m 为完整模式 pat(以 '"value":"' 结尾)起点,取 value 到未转义 '"'。
    反斜杠转义就地解:\\uXXXX 按码点(CJK 扩展区为 surrogate pair,末尾合并),
    \\n 变空格,\\\\" \\\\\\ 取字面。"""
    i = m + len(pat)
    out = []
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "u" and i + 6 <= len(s):
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(" " if nxt == "n" else nxt)
            i += 2
            continue
        if c == '"':
            break
        out.append(c)
        i += 1
    v = "".join(out).replace("\t", " ").replace("\n", " ")
    try:
        v = v.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError:
        v = "".join(ch for ch in v if not (0xD800 <= ord(ch) <= 0xDFFF))
    return v


def _labels(line):
    """labels 块(在 descriptions 之前)取 en 与 zh 标签。"""
    pl = line.find('"labels":')
    if pl < 0:
        return "", ""
    pd = line.find('"descriptions":')
    end = pd if pd > pl else pl + 60000
    blk = line[pl:end]
    pat_en = '"en":{"language":"en","value":"'
    en = ""
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
    eout = open(f"/home/ubuntu/{OUT}_out_{k}.tsv", "w", buffering=1 << 20)
    lout = open(f"/home/ubuntu/{OUT}_labels_out_{k}.tsv", "w", buffering=1 << 20)
    ne = nl = 0
    with open(FIFO[k], "r", buffering=1 << 20) as f:
        for line in f:
            i = line.find('"id":"Q')
            if i < 0:
                continue
            j = line.find('"', i + 6)
            qid = line[i + 6:j]
            is_cls = False
            for pred in PREDS:
                p = line.find(f'"{pred}":')
                if p < 0:
                    continue
                ids = _pred_ids(line, p)
                for x in ids[:8]:
                    eout.write(f"{qid}\t{pred}\tQ{x}\n")
                ne += len(ids[:8])
                if pred == "P279" and ids:
                    is_cls = True
            if is_cls:
                en, zh = _labels(line)
                lout.write(f"{qid}\t{en}\t{zh}\n")
                nl += 1
            if ne and ne % 5_000_000 < 8:
                print(f"w{k}: edges~{ne:,}", flush=True)
    eout.close()
    lout.close()
    print(f"w{k} DONE edges={ne:,} labels={nl:,}", flush=True)


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
