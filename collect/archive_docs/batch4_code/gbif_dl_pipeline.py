#!/usr/bin/env python3
"""GBIF download(DWCA)落湖后的处理管线:解析 → P846 过滤 → 每物种≤N 图 → 切队列。

输入:解压后的 DWCA 目录(含 occurrence.txt + multimedia.txt,TSV 带表头)。
用法:
  python3 gbif_dl_pipeline.py --dwca /root/gbif_dl --bridge wd_b4_bridge.nt [--dry-run]
产出任务行(queue-b4-gbifdl):
  {"src":"gbifdl","extid":"taxon:<K>#<i>","qid":"Q…","url":"…","license":"…","nc":1}
  license 含 CC0/BY 视为非商业分区外(nc=""),否则进 blobs-nc(保守)。
内存:occurrence 匹配行 gbifID→taxonKey dict(~千万级,湖侧 2T 内存无压力)。
"""
import argparse
import csv
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.environ.get("DEMIFLOW_PATH", "/yzp/zhaozy/yangzepeng/0905/demiflow"))

PAT = re.compile(r'<http://www\.wikidata\.org/entity/(Q\d+)> '
                 r'<http://www\.wikidata\.org/prop/direct/P846> "(.*)"')
NC_RE = re.compile(r'nc|non[- ]?commercial|by-nc', re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwca", required=True)
    ap.add_argument("--bridge", default="wd_b4_bridge.nt")
    ap.add_argument("--per-taxon", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 1) P846 桥: taxonKey -> qid
    key2qid = {}
    opener = gzip.open if args.bridge.endswith(".gz") else open
    with opener(args.bridge, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = PAT.search(line)
            if m:
                key2qid.setdefault(m.group(2), m.group(1))
    print(f"P846 桥: {len(key2qid):,} taxonKey", flush=True)

    # 2) occurrence.txt: 匹配 taxon 的 gbifID → taxonKey
    gid2tk = {}
    occ_path = os.path.join(args.dwca, "occurrence.txt")
    with open(occ_path, encoding="utf-8", errors="replace") as f:
        rd = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        for i, r in enumerate(rd):
            tk = r.get("taxonKey")
            if tk in key2qid:
                gid2tk[r["gbifID"]] = tk
            if (i + 1) % 20_000_000 == 0:
                print(f"  occurrence {i+1:,} 行, 命中 {len(gid2tk):,}", flush=True)
    print(f"occurrence 命中: {len(gid2tk):,} gbifID", flush=True)

    # 3) multimedia.txt: 命中行,每 taxonKey ≤ N 张
    rows, per = [], {}
    mm_path = os.path.join(args.dwca, "multimedia.txt")
    with open(mm_path, encoding="utf-8", errors="replace") as f:
        rd = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        for i, r in enumerate(rd):
            gid = r.get("gbifID")
            tk = gid2tk.get(gid)
            if tk is None:
                continue
            n = per.get(tk, 0)
            if n >= args.per_taxon:
                continue
            url = r.get("identifier") or ""
            if not url.startswith("http"):
                continue
            lic = r.get("license") or ""
            per[tk] = n + 1
            rows.append({"src": "gbifdl", "extid": f"taxon:{tk}#{n}",
                         "qid": key2qid[tk], "url": url, "license": lic,
                         "nc": 1 if (lic and NC_RE.search(lic)) else ""})
            if (i + 1) % 20_000_000 == 0:
                print(f"  multimedia {i+1:,} 行, 已选 {len(rows):,}", flush=True)
    print(f"multimedia 选出: {len(rows):,} 图(覆盖 {len(per):,} 物种)", flush=True)
    if args.dry_run:
        return

    # 4) 切批上传
    from demiflow.collect.cosio import COSCreds, COSIO, build_host
    from demiflow.collect.cosqueue import COSQueue
    io = COSIO(COSCreds.from_file(
        "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds"),
        build_host("lhcos-368f6-1256345599", "ap-singapore"))
    bids = COSQueue(io, "lhcos-data/demiwtg-data/queue-b4-gbifdl").produce(
        rows, 2000, skip_existing=True)
    print(f"queue-b4-gbifdl: {len(bids)} 批", flush=True)


if __name__ == "__main__":
    main()
