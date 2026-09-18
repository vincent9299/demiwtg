"""全量审计 kb/blobs: 逐行扫 qid_images 账本, 按 blob 魔数分类,
统计 HTML 错误页混入量及其在账本中的位置分布. 结果写 TSV."""
import gzip, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

LEDGER = "/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz"
ROOT = "/lhcos-data/demiwtg-data/datasets/demiwtg/kb"
OUT = "/home/ubuntu/demi/raw/state/blob_magic_audit.tsv"

MAGIC = [
    (b"\xff\xd8\xff", "jpeg"), (b"\x89PNG", "png"), (b"GIF8", "gif"),
    (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"%PDF", "pdf"),
    (b"AT&TFORM", "djvu"), (b"\x00\x00\x00", "mp4/other"),
]
# RIFF 需要看 8..12; SVG/XML/HTML 看 ASCII 头

def sniff_head(p, n=32):
    try:
        with open(p, "rb") as f:
            h = f.read(n)
    except FileNotFoundError:
        return "MISSING"
    except OSError:
        return "UNREADABLE"
    if not h:
        return "EMPTY"
    for m, name in MAGIC:
        if h.startswith(m):
            return name
    if h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return "webp"
    low = h.lstrip()[:16].lower()
    if low.startswith(b"<svg") or (low.startswith(b"<?xml") and b"svg" in h.lower()):
        return "svg"
    if low.startswith((b"<!doctype html", b"<html", b"<!doctype")):
        return "HTML"
    if low.startswith(b"<?xml"):
        return "xml"
    if h[:1] == b"<":
        return "HTML"
    return "other"

def main():
    t0 = time.time()
    cache = {}
    # 桶: (decile, tier, sniff) -> [count, bytes]
    stat = defaultdict(lambda: [0, 0])
    ex = ThreadPoolExecutor(max_workers=48)

    def sniff_cached(sha, ext, rel):
        key = sha
        s = cache.get(key)
        if s is None:
            s = sniff_head(os.path.join(ROOT, "blobs", rel))
            cache[key] = s
        return s

    n = 0
    decile = 10  # 先按 886 万估段, 逐行用当前行号/总行近似: 改为收集后处理
    rows_buf = []
    with gzip.open(LEDGER, "rt", encoding="utf-8") as f:
        for line in f:
            n += 1
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                stat[(-1, "badjson", "")][0] += 1
                continue
            rows_buf.append(r)
            if len(rows_buf) >= 20000:
                flush(rows_buf, n, ex, cache, stat)
                rows_buf = []
    if rows_buf:
        flush(rows_buf, n, ex, cache, stat)

    ex.shutdown()
    dt = time.time() - t0
    total = sum(c for (_, _, _), (c, _) in stat.items())
    with open(OUT, "w", encoding="utf-8") as out:
        out.write(f"# rows={n} unique_blobs={len(cache)} secs={dt:.0f}\n")
        out.write("decile\ttier\tsniff\tcount\tbytes\n")
        for (d, t, s), (c, b) in sorted(stat.items(), key=lambda x: (x[0][0], -x[1][0])):
            out.write(f"{d}\t{t}\t{s}\t{c}\t{b}\n")
    print(f"DONE rows={n} blobs={len(cache)} secs={dt:.0f} -> {OUT}")

def flush(rows, lineno, ex, cache, stat):
    futs = []
    for r in rows:
        sha, rel = r["sha256"], r["path"].split("/", 1)[1]
        futs.append((r, sha, ex.submit(sniff_head_cached, cache, ROOT, rel)))
    base = lineno - len(rows)
    for i, (r, sha, fu) in enumerate(futs):
        pos = base + i
        s = fu.result()
        d = min(pos * 10 // 8861354, 9)
        k = (d, r.get("tier") or "?", s if s in ("HTML", "MISSING", "UNREADABLE", "EMPTY") else "ok_" + s)
        stat[k][0] += 1
        stat[k][1] += r.get("page_bytes") or 0

def sniff_head_cached(cache, root, rel):
    sha = rel.split("/", 1)[1].rsplit(".", 1)[0]
    s = cache.get(sha)
    if s is None:
        s = sniff_head(os.path.join(root, "blobs", rel))
        cache[sha] = s
    return s

if __name__ == "__main__":
    main()
