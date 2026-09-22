#!/usr/bin/env python3
"""sg 中继 Phase A：SG COS → 广州 COS（对象级拷贝，HEAD 跳过已传，ETag=md5 校验）。"""
import json, sys, time, hashlib, hmac, urllib.parse, threading
from concurrent.futures import ThreadPoolExecutor

SID, SKEY = open('/tmp/cos_creds').read().strip().split(':', 1)
SRC_HOST = 'lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com'
DST_HOST = 'lhcos-cee54-1256345599.cos.ap-guangzhou.myqcloud.com'
PREFIX = 'lhcos-data/demiwtg-data/datasets/demiwtg/blobs'
JOBS = sys.argv[1]

_local = threading.local()

def sess():
    import requests
    if not hasattr(_local, 's'):
        _local.s = requests.Session()
    return _local.s

def sig(method, path, host):
    now = int(time.time())
    kt = f"{now-60};{now+900}"
    sk = hmac.new(SKEY.encode(), kt.encode(), hashlib.sha1).hexdigest()
    hs = f"{method.lower()}\n{path}\n\nhost={host}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    v = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={SID}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list=&q-signature={v}")

def call(host, method, key, data=None):
    path = key if key.startswith('/') else '/' + key
    url = f"https://{host}{urllib.parse.quote(path)}"
    try:
        r = sess().request(method, url, data=data,
                           headers={'authorization': sig(method, path, host)},
                           timeout=(15, 300))
        return r.status_code, r.headers, r.content if r.status_code < 400 else r.content[:200]
    except Exception:
        try:
            _local.s.close(); del _local.s
        except Exception:
            pass
        return 0, {}, b'transport_error'

stats = {'done': 0, 'skip': 0, 'fail': 0, 'bytes': 0}
lock = threading.Lock()

def work(job):
    sha, ext, want = job['sha'], job['ext'], job['size']
    key = f"{PREFIX}/{sha[:2]}/{sha}.{ext}"
    st, h, _ = call(DST_HOST, 'HEAD', key)
    if st == 200 and want and int(h.get('Content-Length', -1)) == want:
        with lock:
            stats['skip'] += 1
        return
    st, h, b = call(SRC_HOST, 'GET', key)
    if st != 200:
        with lock:
            stats['fail'] += 1
        return
    st2, h2, _ = call(DST_HOST, 'PUT', key, data=b)
    etag = (h2.get('ETag') or '').strip('"').lower() if st2 == 200 else ''
    if st2 != 200 or (etag and etag != hashlib.md5(b).hexdigest()):
        with lock:
            stats['fail'] += 1
        return
    with lock:
        stats['done'] += 1
        stats['bytes'] += len(b)

jobs = json.load(open(JOBS))
t0 = time.time()
with ThreadPoolExecutor(6) as ex:
    for i, _ in enumerate(ex.map(work, jobs)):
        if (i + 1) % 100 == 0:
            el = time.time() - t0
            print(f"{i+1}/{len(jobs)} done={stats['done']} skip={stats['skip']} "
                  f"fail={stats['fail']} {stats['bytes']/1e6:.0f}MB {stats['bytes']/el:.1f}MB/s",
                  flush=True)
print('RELAY_A_COMPLETE', stats, flush=True)
