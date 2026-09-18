#!/usr/bin/env python
"""档2提取器:流式重放 truthy,抽出"概念集之外"的 P18 图像引用。

输出 /tmp/tier2/d2-candidates.tsv.gz: (qid, filename) — 概念集外实体的 P18。
"""
import gzip
import json
import os
import re
import sys

OUT_DIR = "/tmp/tier2"
CONCEPTS = os.path.expanduser(
    "~/demi/kb_night/qid_build/qid_concepts.jsonl")
IMG_EXT = {".jpg", ".jpeg", ".png", ".svg", ".tif", ".tiff", ".gif", ".webp"}

SUB_RE = re.compile(r"^<http://www\.wikidata\.org/entity/(Q\d+)>\s+"
                    r"<http://www\.wikidata\.org/prop/direct/P18>\s+"
                    r"<http://commons\.wikimedia\.org/wiki/"
                    r"Special:FilePath/([^>]+)>\s*\.\s*$")
from urllib.parse import unquote


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    concepts = set()
    opener = gzip.open if CONCEPTS.endswith(".gz") else open
    with opener(CONCEPTS, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            q = r.get("qid") if isinstance(r, dict) else r
            if q:
                concepts.add(q)
    sys.stderr.write(f"[tier2] concepts loaded: {len(concepts)}\n")

    n = 0
    with gzip.open(f"{OUT_DIR}/d2-candidates.tsv.gz", "wt",
                   encoding="utf-8") as out:
        for line in sys.stdin:
            m = SUB_RE.match(line)
            if not m:
                continue
            qid, enc = m.groups()
            if qid in concepts:
                continue
            fname = unquote(enc).replace("_", " ")
            if os.path.splitext(fname)[1].lower() not in IMG_EXT:
                continue
            out.write(f"{qid}\t{fname}\n")
            n += 1
    sys.stderr.write(f"[tier2] d2 candidates: {n}\n")


if __name__ == "__main__":
    main()
