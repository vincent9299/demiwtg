#!/usr/bin/env python3
"""提交 GBIF occurrence download(全站带图记录,DWCA 格式含 multimedia.txt)。

用法:
  python3 gbif_dl_submit.py --user <gbif用户名> --pass <密码> [--email 通知邮箱]
凭证只用于创建下载(basic auth),不落盘。
产出:downloadKey(后续轮询/取包用)。
"""
import argparse
import base64
import json
import sys
import urllib.request

API = "https://api.gbif.org/v1/occurrence/download/request"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--pass", dest="password", required=True)
    ap.add_argument("--email", default="")
    args = ap.parse_args()

    body = {
        "format": "DWCA",                      # 产出 occurrence.txt + multimedia.txt
        "predicate": {"type": "equals", "key": "MEDIA_TYPE", "value": "StillImage"},
        "notification_address": [args.email] if args.email else [],
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "Authorization": "Basic " + base64.b64encode(
                     f"{args.user}:{args.password}".encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            key = r.read().decode().strip().strip('"')
            print("DOWNLOAD_KEY:", key)
            print("轮询: python3 gbif_dl_poll.py", key)
    except urllib.error.HTTPError as e:
        print(f"提交失败 HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
