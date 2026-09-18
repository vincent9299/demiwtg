#!/usr/bin/env python3
"""下载质量抽检：对 done 账本样本做 COS 公共 Range-GET，
校验 (1) 对象存在 (2) 总长>0 且与账本 size 一致（cos_only 无 size 跳过）
(3) 首字节魔数与扩展名相符。stdin 每行一个 JSON（含 sha256/ext/cos_key，
可选 size_bytes），输出 JSON 行 {sha, verdict, detail}。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cos_util

HOST = cos_util.HOST

MAGIC = [  # (前缀字节, 允许的 ext 集合)
    (b"\xff\xd8\xff", {"jpg", "jpeg"}),
    (b"\x89PNG", {"png"}),
    (b"GIF8", {"gif"}),
    (b"II*\x00", {"tif", "tiff"}),
    (b"MM\x00*", {"tif", "tiff"}),
    (b"%PDF", {"pdf"}),
    (b"AT&TFORM", {"djvu"}),
    (b"<svg", {"svg"}),
    (b"<?xml", {"svg"}),
    (b"RIFF", {"webp"}),
    (b"\x00\x00\x00", {"mp4", "m4a"}),
]


def probe(rec: dict) -> dict:
    key = rec.get("cos_key") or ""
    if not key.startswith("/"):
        key = "/" + key
    ext = (rec.get("ext") or "").lower()
    err = ""
    for t in range(4):
        try:
            sid, skey = cos_util.creds()
            auth = cos_util._sig("GET", key, {}, sid, skey)
            url = f"{cos_util.SCHEME}://{HOST}{urllib.parse.quote(key)}"
            req = urllib.request.Request(url, headers={
                "Range": "bytes=0-15",
                "User-Agent": "oddparity-probe/0.3 (checksum research; https://github.com/oddparity/oddparity-probe)",
                "authorization": auth})
            with urllib.request.urlopen(req, timeout=30) as r:
                head = r.read(16)
                cr = r.headers.get("Content-Range", "")     # bytes 0-15/12345
                total = int(cr.rsplit("/", 1)[-1]) if "/" in cr else -1
            if total == 0:
                return {"sha": rec.get("sha256", "")[:12], "verdict": "EMPTY", "detail": key}
            if not any(head.startswith(m) for m, _ in MAGIC):
                if ext in ("svg", "xml", "json"):
                    pass                            # 文本类不强制魔数
                else:
                    return {"sha": rec.get("sha256", "")[:12], "verdict": "BAD_MAGIC",
                            "detail": f"{head[:8].hex()} ext={ext} key={key}"}
            for m, exts in MAGIC:
                if head.startswith(m) and ext not in exts:
                    return {"sha": rec.get("sha256", "")[:12], "verdict": "EXT_MISMATCH",
                            "detail": f"magic={m.hex()} ext={ext} key={key}"}
            sz = rec.get("size_bytes")
            if isinstance(sz, int) and sz > 0 and total > 0 and total != sz:
                return {"sha": rec.get("sha256", "")[:12], "verdict": "SIZE_DIFF",
                        "detail": f"cos={total} ledger={sz} key={key}"}
            return {"sha": rec.get("sha256", "")[:12], "verdict": "OK", "detail": total}
        except urllib.error.HTTPError as e:
            err = f"HTTP{e.code}"
            if e.code == 404:
                return {"sha": rec.get("sha256", "")[:12], "verdict": "MISSING", "detail": key}
            time.sleep(1 + t)
        except Exception as e:                      # noqa: BLE001
            err = str(e)
            time.sleep(1 + t)
    return {"sha": rec.get("sha256", "")[:12], "verdict": "ERR", "detail": err[:80]}


if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        print(json.dumps(probe(rec), ensure_ascii=False), flush=True)
