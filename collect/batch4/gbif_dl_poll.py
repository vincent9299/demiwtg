#!/usr/bin/env python3
"""轮询 GBIF download 状态;SUCCEEDED 时给出下载链接。

用法: python3 gbif_dl_poll.py <downloadKey>
状态机: PREPARING/RUNNING → SUCCEEDED(给 downloadUrl)/FAILED/KILLED
"""
import json
import sys
import urllib.request

key = sys.argv[1]
url = f"https://api.gbif.org/v1/occurrence/download/{key}"
with urllib.request.urlopen(url, timeout=30) as r:
    d = json.load(r)
print(json.dumps({k: d.get(k) for k in
                  ("status", "totalRecords", "size", "created", "durationMillis",
                   "downloadLink", "doi", "license")}, ensure_ascii=False, indent=1))
if d.get("status") == "SUCCEEDED":
    link = d.get("downloadLink") or f"https://api.gbif.org/v1/occurrence/download/{key}/download"
    print("下载(sg1 上跑,内网国际带宽好):\n  curl -L -C - -o gbif_dl.zip '" + link + "'")
