#!/usr/bin/env python
"""嵌入图清单抽取器:扫语料分片的 images 字段,输出本机唯一文件清单。

用法: extract_embed.py <part1> [part2 ...]   (如 pages-en-part10)
输出: ~/imgbuf/embed-uniq.jsonl  每行 {"qid": ..., "f": 文件名}(全局按 f 去重由协调机做)
预过滤:无图行直接子串跳过,不整行 json 解析(语料行含全文,解析贵)。
"""
import gzip
import json
import os
import sys

KB = "/lhcos-data/demiwtg-data/datasets/demiwtg/kb"
OUT = os.path.expanduser("~/imgbuf/embed-uniq.jsonl")


def main() -> None:
    parts = sys.argv[1:]
    seen: dict = {}
    for part in parts:
        path = f"{KB}/{part}"
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"images": []' in line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                imgs = r.get("images") or []
                if not imgs:
                    continue
                qid = r.get("qid") or f"pid:{r.get('page_id')}"
                for name in imgs:
                    if isinstance(name, str) and name and name not in seen:
                        seen[name] = qid
    with open(OUT, "w", encoding="utf-8") as f:
        for name, qid in seen.items():
            f.write(json.dumps({"qid": qid, "f": name},
                               ensure_ascii=False) + "\n")
    print(f"[extract] parts={len(parts)} uniq_files={len(seen)}", flush=True)


if __name__ == "__main__":
    main()
