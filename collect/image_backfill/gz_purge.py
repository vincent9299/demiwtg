#!/usr/bin/env python3
"""cn1 侧：清空 GZ 中转桶指定前缀（cos-internal 免费），list→DELETE 循环直到空。
用法: python3 gz_purge.py <prefix> [<prefix2> ...]"""
import sys, re, time, threading, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/tmp/costest")
import cos_util

cos_util.HOST = "lhcos-cee54-1256345599.cos-internal.ap-guangzhou.myqcloud.com"
SID, SKEY = cos_util.creds()

stats = {'ok': 0, 'miss': 0, 'fail': 0}
lock = threading.Lock()


def list_page(prefix, marker=''):
    params = {'prefix': prefix, 'max-keys': '1000'}
    if marker:
        params['marker'] = marker
    q = '&'.join(f'{urllib.parse.quote(k, safe="")}={urllib.parse.quote(v, safe="")}'
                 for k, v in sorted(params.items()))
    url = f'https://{cos_util.HOST}/?{q}'
    auth = cos_util._sig('GET', '/', params, SID, SKEY)
    req = urllib.request.Request(url, headers={'authorization': auth})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode('utf-8')
    keys = re.findall(r'<Contents>\s*<Key>([^<]+)</Key>', t)
    trunc = '<IsTruncated>true</IsTruncated>' in t
    nm = re.search(r'<NextMarker>([^<]+)</NextMarker>', t)
    return keys, trunc, (nm.group(1) if nm else '')


def del_one(key):
    for attempt in range(4):
        st, _, _ = cos_util._call('DELETE', key)
        if st in (204, 404):
            with lock:
                stats['ok' if st == 204 else 'miss'] += 1
            return True
        time.sleep(2 * (attempt + 1))
    with lock:
        stats['fail'] += 1
    print('DEL_FAIL', key, flush=True)
    return False


def purge(prefix):
    t0 = time.time()
    while True:
        marker = ''
        pages = []
        while True:
            keys, trunc, nm = list_page(prefix, marker)
            pages += keys
            if not trunc:
                break
            marker = nm
        if not pages:
            break
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(del_one, pages))
        print(f'{prefix}: 本轮删 {len(pages)}，累计 ok={stats["ok"]} '
              f'fail={stats["fail"]} {time.time()-t0:.0f}s', flush=True)
    print(f'PURGE_DONE {prefix}', flush=True)


for pfx in sys.argv[1:]:
    purge(pfx)
print('GZ_PURGE_COMPLETE', stats, flush=True)
