#!/usr/bin/env python3
"""从 Wikidata 桥表切 B4 任务队列（queue-b4-tmdb / queue-b4-gbif）。

用法（湖侧，桥表先落到本地 batch4/）：
  python3 cut_b4_queues.py --bridge wd_b4_bridge.nt [--dry-run]

产出任务行：
- tmdb: {"src":"tmdb","extid":"movie:290639","kind":"movie","tmdb_id":"290639","qids":[Q…]}
- gbif: {"src":"gbif","extid":"taxon:5128269","kind":"taxon","gbif_id":"5128269","qid":"Q…"}
  （P3151 = iNat taxon ID，归 b2-iNat 线，不切。）
幂等：COSQueue.produce(skip_existing=True)。
"""
import argparse
import gzip
import re
import sys
import os

sys.path.insert(0, os.environ.get("DEMIFLOW_PATH", "/yzp/zhaozy/yangzepeng/0905/demiflow"))
from demiflow.collect.cosio import COSCreds, COSIO, build_host
from demiflow.collect.cosqueue import COSQueue

PAT = re.compile(
    r'<http://www\.wikidata\.org/entity/(Q\d+)> '
    r'<http://www\.wikidata\.org/prop/direct/(P\d+)> "(.*)" \.')
TMDB_KIND = {"P4947": "movie", "P4983": "tv", "P4985": "person"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="wd_b4_bridge.nt")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tmdb = {}          # extid -> row（合并 qids）
    gbif = {}
    n = 0
    opener = gzip.open if args.bridge.endswith(".gz") else open
    with opener(args.bridge, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = PAT.search(line)
            if not m:
                continue
            qid, prop, val = m.group(1), m.group(2), m.group(3)
            n += 1
            if prop in TMDB_KIND:
                kind = TMDB_KIND[prop]
                extid = f"{kind}:{val}"
                r = tmdb.setdefault(extid, {"src": "tmdb", "extid": extid,
                                            "kind": kind, "tmdb_id": val,
                                            "qids": []})
                if qid not in r["qids"]:
                    r["qids"].append(qid)
            elif prop == "P846":
                extid = f"taxon:{val}"
                gbif.setdefault(extid, {"src": "gbif", "extid": extid,
                                        "kind": "taxon", "gbif_id": val,
                                        "qid": qid})
    k = {"movie": 0, "tv": 0, "person": 0}
    for r in tmdb.values():
        k[r["kind"]] += 1
    print(f"桥表属性行={n}  tmdb实体={len(tmdb)} (movie={k['movie']} tv={k['tv']} "
          f"person={k['person']})  gbif物种={len(gbif)}")
    if args.dry_run:
        return

    io = COSIO(COSCreds.from_file(
        "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds"),
        build_host("lhcos-368f6-1256345599", "ap-singapore"))
    b1 = COSQueue(io, "lhcos-data/demiwtg-data/queue-b4-tmdb").produce(
        list(tmdb.values()), 2000, skip_existing=True)
    b2 = COSQueue(io, "lhcos-data/demiwtg-data/queue-b4-gbif").produce(
        list(gbif.values()), 2000, skip_existing=True)
    print(f"queue-b4-tmdb: {len(b1)} 批  queue-b4-gbif: {len(b2)} 批")


if __name__ == "__main__":
    main()
