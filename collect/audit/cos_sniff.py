#!/usr/bin/env python3
"""COS Range-GET 魔数嗅探: 对候选 blob 键取前 2KB, 分类内容并提取
Wikimedia 错误码。输入 TSV(rel_path\tsize), 输出 TSV(rel\tclass\terr)。

class: html/jpeg/png/gif/tiff/webp/svg/xml/pdf/djvu/empty/missing/err/other
"""
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BUCKET = "lhcos-368f6-1256345599"
REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
ROOT = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"

MAGIC = [(b"\xff\xd8\xff", "jpeg"), (b"\x89PNG", "png"), (b"GIF8", "gif"),
         (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"%PDF", "pdf"),
         (b"AT&TFORM", "djvu")]
ERR_RE = re.compile(rb"Error:\s*(\d{3})")
TITLE_RE = re.compile(rb"<title>([^<]{0,80})</title>")


def sniff(rel):
    url = f"https://{HOST}/{urllib.request.quote(ROOT + rel)}"
    req = urllib.request.Request(url, headers={"Range": "bytes=0-2047"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                h = r.read(2048)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "missing", ""
            if attempt == 5:
                return "err", f"HTTP{e.code}"
            time.sleep(1 + attempt)
        except Exception as e:
            if attempt == 5:
                return "err", repr(e)[:40]
            time.sleep(1 + attempt)
    if not h:
        return "empty", ""
    for m, name in MAGIC:
        if h.startswith(m):
            return name, ""
    if h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return "webp", ""
    low = h.lstrip()[:24].lower()
    if low.startswith((b"<!doctype html", b"<html", b"<!doctype")):
        m = ERR_RE.search(h)
        t = TITLE_RE.search(h)
        return "html", (m.group(1).decode() if m else
                        (t.group(1).decode("utf-8", "replace").strip()
                         [:40] if t else "?"))
    if low.startswith(b"<svg"):
        return "svg", ""
    if low.startswith(b"<?xml"):
        return "xml", ""
    if h[:1] == b"<":
        return "html", ""
    return "other", h[:8].hex()


def main():
    in_path, out_path = sys.argv[1], sys.argv[2]
    conc = int(sys.argv[3]) if len(sys.argv) > 3 else 48
    rels = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            rel = line.split("\t", 1)[0]
            if rel and not rel.endswith("/"):
                rels.append(rel)
    done_rels = set()
    if os.path.exists(out_path):            # 续跑: 跳过已产出
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                done_rels.add(line.split("\t", 1)[0])
        rels = [r for r in rels if r not in done_rels]
        print(f"[sniff] 续跑: 已完成 {len(done_rels):,}, 本次余 "
              f"{len(rels):,}", flush=True)
    t0 = time.time()
    done = 0
    with open(out_path, "a", encoding="utf-8") as out, \
            ThreadPoolExecutor(conc) as ex:
        for rel, (cls, err) in zip(rels, ex.map(sniff, rels)):
            out.write(f"{rel}\t{cls}\t{err}\n")
            done += 1
            if done % 20000 == 0:
                el = time.time() - t0
                print(f"[sniff] {done:,}/{len(rels):,} "
                      f"({done/el:.0f}/s)", flush=True)
    print(f"SNIFF_DONE {done:,} in {time.time()-t0:.0f}s -> {out_path}",
          flush=True)


if __name__ == "__main__":
    main()
