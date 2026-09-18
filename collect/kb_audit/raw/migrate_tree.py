#!/usr/bin/env python3
# 迁移: 桶根 demiwtg-data/* -> lhcos-data/demiwtg-data/* (COS 服务端拷贝)
import time, hmac, hashlib, urllib.request, urllib.parse, sys

BUCKET = "lhcos-368f6-1256345599"; REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"

def creds():
    sid, skey = open("/tmp/cos_creds").read().strip().split(":", 1)
    return sid, skey

def sign(method, path, params):
    sid, skey = creds()
    now = int(time.time()); kt = f"{now-60};{now+900}"
    sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
    p = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
    hs = f"{method.lower()}\n{path}\n{p}\nhost={HOST}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    return (f"q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list={';'.join(sorted(k.lower() for k in params))}"
            f"&q-signature={hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()}")

def list_keys(prefix, token=None):
    params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
    if token: params["continuation-token"] = token
    q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
    auth = sign("GET", "/", dict(params))
    plist = urllib.parse.quote(";".join(sorted(k.lower() for k in params)))
    sig_part = auth.split("q-signature=")[0] + "q-signature=" + auth.split("q-signature=")[1]
    # 重建: q-url-param-list 也要 url 编码
    sig_part = sig_part.replace(";".join(sorted(k.lower() for k in params)), plist)
    url = f"https://{HOST}/?{q}&{sig_part}"
    req = urllib.request.Request(url); req.add_header("host", HOST)
    import re
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
    keys = re.findall(r"<Key>([^<]+)</Key>", txt)
    tok = None
    m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", txt)
    if m: tok = m.group(1)
    return keys, tok

def server_copy(src_key, dst_key):
    """PUT dst with x-cos-copy-source: src (同桶)"""
    path = "/" + dst_key
    auth = sign("PUT", path, {})
    url = f"https://{HOST}{path}?{auth}"
    req = urllib.request.Request(url, method="PUT", data=b"")
    req.add_header("host", HOST)
    req.add_header("x-cos-copy-source", urllib.parse.quote(f"/{BUCKET}/{src_key}"))
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status

def head_len(key):
    try:
        auth = sign("HEAD", "/" + key, {})
        req = urllib.request.Request(f"https://{HOST}/{key}?{auth}", method="HEAD")
        req.add_header("host", HOST)
        with urllib.request.urlopen(req, timeout=20) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return -1

def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "demiwtg-data/"
    done = skipped = failed = 0
    tok = None
    while True:
        keys, tok = list_keys(prefix, tok)
        for k in keys:
            dst = "lhcos-data/" + k
            # 已存在同尺寸则跳过
            if head_len(dst) == head_len(k):
                skipped += 1; continue
            try:
                server_copy(k, dst); done += 1
                if done % 25 == 0: print(f"[{time.strftime('%H:%M:%S')}] copied={done} skip={skipped} fail={failed}", flush=True)
            except Exception as e:
                failed += 1; print(f"FAIL {k}: {e}", flush=True)
        if not tok: break
    print(f"MIGRATE_DONE copied={done} skipped={skipped} failed={failed}", flush=True)

if __name__ == "__main__":
    main()
