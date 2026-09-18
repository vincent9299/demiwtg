#!/usr/bin/env python3
"""COS 对象读写（纯标准库，供 fleet_curl / 存量直传共用）。

签名算法与 r 机上久经验证的 /tmp/stream_cos.py 一致（COS 原生 sha1 key-time）。
凭据读取顺序：~/wk_backfill/.cos_creds → /tmp/cos_creds（sid:skey 一行）。
endpoint 默认公网（内网 cos-internal 实测不可达），可用环境变量
COS_BUCKET / COS_REGION / COS_HOST 覆盖（测试注入 mock 用）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BUCKET = os.environ.get("COS_BUCKET", "lhcos-368f6-1256345599")
REGION = os.environ.get("COS_REGION", "ap-singapore")
HOST = os.environ.get("COS_HOST", f"{BUCKET}.cos.{REGION}.myqcloud.com")
SCHEME = os.environ.get("COS_SCHEME", "https")   # 测试注入 mock 时用 http

RETRY_DELAYS = (1, 4, 10, 25)


def creds() -> tuple[str, str]:
    for p in (os.path.expanduser("~/wk_backfill/.cos_creds"), "/tmp/cos_creds"):
        try:
            sid, skey = open(p).read().strip().split(":", 1)
            if sid and skey:
                return sid, skey
        except (OSError, ValueError):
            continue
    raise RuntimeError("无 COS 凭据：~/wk_backfill/.cos_creds 与 /tmp/cos_creds 均缺失")


def _sig(method: str, path: str, params: dict, sid: str, skey: str) -> str:
    now = int(time.time())
    kt = f"{now - 60};{now + 900}"
    sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
    p = "&".join(f"{k.lower()}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted(params.items()))
    hs = f"{method.lower()}\n{path}\n{p}\nhost={HOST}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    sigv = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list="
            f"{';'.join(sorted(k.lower() for k in params))}&q-signature={sigv}")


def _call(method: str, key: str, data: bytes | None = None,
          expect: tuple[int, ...] = (200,)):
    """单次调用；expect 里的状态码不算失败（HEAD 404 由调用方语义化）。"""
    sid, skey = creds()
    path = key if key.startswith("/") else "/" + key
    url = f"{SCHEME}://{HOST}{urllib.parse.quote(path)}"
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("authorization", _sig(method, path, {}, sid, skey))
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()[:400]


def head(key: str) -> int:
    """返回 Content-Length；对象不存在返回 -1；其他错误抛异常。"""
    for d in RETRY_DELAYS:
        st, h, _ = _call("HEAD", key, expect=(200, 404))
        if st == 200:
            try:
                return int(h.get("Content-Length", -1))
            except (TypeError, ValueError):
                return -1
        if st == 404:
            return -1
        time.sleep(d)
    raise RuntimeError(f"HEAD {key} 重试耗尽")


def put(key: str, blob: bytes) -> str | None:
    """单次 PUT（补图均 ≤20MB，远低于单 PUT 5GB 限制）。
    成功返回小写 ETag（单次 PUT 即内容 md5，供完整性校验），失败返回 None。"""
    for d in RETRY_DELAYS:
        st, h, body = _call("PUT", key, data=blob)
        if st == 200:
            return (h.get("ETag") or "").strip('"').lower() or None
        time.sleep(d)
    sys.stderr.write(f"put {key} 失败: {st} {body[:120]!r}\n")
    return None


def blob_key(prefix: str, sha: str, ext: str) -> str:
    """权威布局：blobs/<sha前2>/<sha>.<ext>（prefix 形如 .../datasets/demiwtg/blobs）"""
    return f"{prefix.rstrip('/')}/{sha[:2]}/{sha}.{ext}"
