#!/usr/bin/env python3
"""COS 写能力探针：PUT/HEAD/DELETE 一个临时 key，验证凭据与签名。"""
import hmac
import hashlib
import sys
import time
import urllib.parse
import urllib.request

BUCKET = "lhcos-368f6-1256345599"
REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"


def creds():
    import os
    for p in (os.path.expanduser("~/wk_backfill/.cos_creds"), "/tmp/cos_creds"):
        try:
            sid, skey = open(p).read().strip().split(":", 1)
            return sid, skey
        except (OSError, ValueError):
            continue
    sys.exit("无凭据：~/wk_backfill/.cos_creds 与 /tmp/cos_creds 都不存在")


def sig(method, path, params, sid, skey):
    now = int(time.time())
    kt = f"{now-60};{now+900}"
    sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
    p = "&".join(f"{k.lower()}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted(params.items()))
    hs = f"{method.lower()}\n{path}\n{p}\nhost={HOST}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    sigv = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list="
            f"{';'.join(sorted(k.lower() for k in params))}&q-signature={sigv}")


def call(method, path, params, data=None):
    q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted(params.items()))
    url = f"https://{HOST}{path}" + (f"?{q}" if q else "")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("authorization", sig(method, path, params, sid, skey))
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, dict(r.headers)


if __name__ == "__main__":
    sid, skey = creds()
    key = f"/lhcos-data/demiwtg-data/_tmp_backfill_test/probe_{int(time.time())}"
    st, h = call("PUT", key, {}, b"backfill-probe-test")
    print("PUT:", st)
    st, h = call("HEAD", key, {})
    print("HEAD:", st, "len=", h.get("Content-Length"))
    st, _ = call("DELETE", key, {})
    print("DELETE:", st)
    print("结论: 写/读/删全部通过" if st == 204 else "异常")
