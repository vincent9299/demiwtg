#!/usr/bin/env python3
"""sg 机上执行：① 文档服务端复制到 docs/ ② 删除运维目录+脏树。幂等可重跑。"""
import sys, time, hashlib, hmac, urllib.parse
import requests

SID, SKEY = open('/tmp/cos_creds').read().strip().split(':', 1)
HOST = 'lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com'
s = requests.Session()

def sig(method, path):
    now = int(time.time()); kt = f'{now-60};{now+900}'
    sk = hmac.new(SKEY.encode(), kt.encode(), hashlib.sha1).hexdigest()
    hs = f'{method.lower()}\n{path}\n\nhost={HOST}\n'
    sts = f'sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n'
    v = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    return (f'q-sign-algorithm=sha1&q-ak={SID}&q-sign-time={kt}&q-key-time={kt}'
            f'&q-header-list=host&q-url-param-list=&q-signature={v}')

def call(method, key, headers=None, data=None):
    path = key if key.startswith('/') else '/' + key
    url = f'https://{HOST}{urllib.parse.quote(path)}'
    h = {'authorization': sig(method, path)}
    if headers: h.update(headers)
    r = s.request(method, url, headers=h, data=data, timeout=(15, 120))
    return r.status_code, r.text[:200]

def copy_object(src, dst):
    return call('PUT', dst, headers={'x-cos-copy-source': urllib.parse.quote(f'lhcos-368f6-1256345599/{src}', safe='/')})

DOCS = [
 'archive/p1-p5-release-20260917/README.md',
 'archive/p1-p5-release-20260917/RESUME_SNIFF.md',
 'archive/p1-p5-release-20260917/logs/DASHBOARD.txt',
 'archive/p1-p5-release-20260917/logs/pn_parts_truth.txt',
 'archive/p1-p5-release-20260917/private/p5_bash_history.txt',
 'archive/p1-p5-release-20260917/demiwtg-untracked/HANDOFF_V2.md',
 'archive/p1-p5-release-20260917/demiwtg-untracked/RESUME_2026-09-17.md',
 'archive/p1-p5-release-20260917/demiwtg-untracked/SHIP_STATUS_2026-09-14.md',
 'archive/p1-p5-release-20260917/demiwtg-untracked/sdc_fetch/HANDOVER_SDC_FETCH_EXEC.md',
 'archive/p1-p5-release-20260917/raw/HANDOVER_SDC_FETCH.md',
 'archive/p1-p5-release-20260917/raw/state/join1_stats.json',
 'audit/2026-09-17/audit_report.md',
 'audit/2026-09-17/audit_final_stats.json',
 'audit/kb_images_20260917/HANDOFF_AUDIT_2026-09-17.md',
 'audit/kb_images_20260917/integrity_sample.json',
 'analysis/quality.ipynb',
]
ROOT = 'lhcos-data/demiwtg-data/'

print('=== ① 文档集中到 docs/ ===')
ok = 0
for d in DOCS:
    dst = ROOT + 'docs/' + d.replace('/', '__')
    st, body = copy_object(ROOT + d, dst)
    print(f'  {d.split("/")[-1]}: {st}')
    if st == 200: ok += 1
print(f'文档复制 {ok}/{len(DOCS)}')

print('=== ② 删除（读 key 清单文件 /tmp/del_keys.txt）===')
import os
if not os.path.exists('/tmp/del_keys.txt'):
    print('  无删除清单，跳过'); sys.exit(0)
n = d_ok = d_404 = d_fail = 0
for line in open('/tmp/del_keys.txt'):
    k = line.strip()
    if not k or k.startswith('#'): continue
    n += 1
    st, body = call('DELETE', k)
    if st in (204, 200): d_ok += 1
    elif st == 404: d_404 += 1
    else: d_fail += 1; print(f'  FAIL {st} {k[:80]} {body[:80]}')
    if n % 100 == 0: print(f'  ...{n} 删{d_ok} 404={d_404}', flush=True)
print(f'删除完成: 处理 {n} 成功 {d_ok} 已不存在 {d_404} 失败 {d_fail}')
