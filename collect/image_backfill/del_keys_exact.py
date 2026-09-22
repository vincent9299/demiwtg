#!/usr/bin/env python3
"""按清单逐 key DELETE（幂等：204=删 404=本无），绝不做前缀删除。
用法: python3 del_keys_exact.py <key清单文件> [并发]
清单文件每行一个完整 key。凭证 /tmp/cos_creds。"""
import sys, time, hashlib, hmac, urllib.parse, threading
from concurrent.futures import ThreadPoolExecutor

SID, SKEY = open('/tmp/cos_creds').read().strip().split(':', 1)
HOST = 'lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com'
KEYS = [l.strip() for l in open(sys.argv[1]) if l.strip()]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 8

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

stats = {'ok': 0, 'miss': 0, 'fail': 0}
fails = []
lock = threading.Lock()

def work(key):
    path = '/' + key
    url = f"https://{HOST}{urllib.parse.quote(path)}"
    for attempt in range(5):
        try:
            r = sess().delete(url, headers={'authorization': sig('DELETE', path, HOST)},
                              timeout=(15, 60))
            if r.status_code == 204:
                with lock: stats['ok'] += 1
                return
            if r.status_code == 404:
                with lock: stats['miss'] += 1
                return
        except Exception:
            try:
                _local.s.close(); del _local.s
            except Exception:
                pass
        time.sleep(min(2 * (attempt + 1), 10))
    with lock:
        stats['fail'] += 1
        fails.append(key)

t0 = time.time()
with ThreadPoolExecutor(N) as ex:
    for i, _ in enumerate(ex.map(work, KEYS)):
        if (i + 1) % 2000 == 0:
            el = time.time() - t0
            print(f"{i+1}/{len(KEYS)} ok={stats['ok']} miss={stats['miss']} "
                  f"fail={stats['fail']} {(i+1)/el:.0f}/s", flush=True)
if fails:
    open('/tmp/del_fails.txt', 'w').write('\n'.join(fails))
print('DEL_COMPLETE', stats, f"{time.time()-t0:.0f}s", flush=True)
