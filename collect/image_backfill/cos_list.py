#!/usr/bin/env python3
"""COS 桶列举：按 delimiter 出目录结构（顶层 + 二层），可指定 prefix 深挖。"""
import sys, time, hashlib, hmac, urllib.parse, re

sys.path.insert(0, '/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill')
import cos_util
import urllib.request

SID, SKEY = cos_util.creds()

def list_once(prefix, marker='', delimiter='/', maxkeys=1000):
    params = {'prefix': prefix}
    if marker: params['marker'] = marker
    if delimiter: params['delimiter'] = delimiter
    params['max-keys'] = str(maxkeys)
    q = '&'.join(f'{urllib.parse.quote(k, safe="")}={urllib.parse.quote(v, safe="")}'
                 for k, v in sorted(params.items()))
    url = f'https://{cos_util.HOST}/?{q}'
    auth = cos_util._sig('GET', '/', params, SID, SKEY)
    req = urllib.request.Request(url, headers={'authorization': auth})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode('utf-8')
    prefixes = re.findall(r'<Prefix>([^<]+)/</Prefix>', t)
    # CommonPrefixes 里的（带 </Prefix> 结尾的目录项）
    cp = re.findall(r'<CommonPrefixes>\s*<Prefix>([^<]+)</Prefix>', t)
    contents = []
    for block in re.findall(r'<Contents>.*?</Contents>', t, re.S):
        km = re.search(r'<Key>([^<]+)</Key>', block)
        sm = re.search(r'<Size>(\d+)</Size>', block)
        if km and sm:
            contents.append((km.group(1), int(sm.group(1))))
    trunc = '<IsTruncated>true</IsTruncated>' in t
    nm = re.search(r'<NextMarker>([^<]+)</NextMarker>', t)
    return cp, contents, trunc, (nm.group(1) if nm else '')

def walk(prefix, delimiter='/'):
    marker = ''
    dirs, files, total = [], [], 0
    while True:
        cp, ct, trunc, nm = list_once(prefix, marker, delimiter)
        dirs += cp
        for k, s in ct:
            files.append(k); total += int(s)
        if not trunc: break
        marker = nm
    return dirs, files, total

if __name__ == '__main__':
    p = sys.argv[1] if len(sys.argv) > 1 else ''
    d = sys.argv[2] if len(sys.argv) > 2 else '/'
    dirs, files, total = walk(p, d)
    for x in dirs: print('DIR ', x)
    print(f'文件数 {len(files)}, 字节 {total/1e9:.2f}GB')
    [print("FILE", k) for k in files]
