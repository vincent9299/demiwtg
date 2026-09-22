#!/usr/bin/env python3
"""Restore 116 shards, honoring checkpointed host moves in hub/worker_hosts.json."""
import argparse
import base64
import json
from pathlib import Path
import shlex
import subprocess
import time

HUB = Path(__file__).resolve().parents[2] / 'collect/image_backfill/checkpoints/hub'
# m1-m5 已于 2026-09-19 退役释放；布局全部由 worker_hosts.json 的 override 决定
HOSTS = []
PLACEMENTS = HUB / 'worker_hosts.json'


def fleet_hosts(plan):
    return list(dict.fromkeys(HOSTS + [p['host'] for p in plan]))


def worker_plan():
    sg = Path('/tmp/sg_proxies.txt').read_text().splitlines()
    us = (HUB / 'proxies/master_final.txt').read_text().splitlines()
    ua = dict(line.split('\t', 1) for line in (HUB / 'ua_assign.tsv').read_text().splitlines())
    # w0-110: 原 111 个代理；w111-130: 直连（宿主=r{w-110}）；w131-140: master_final 前 10 个备用代理
    TOTAL = 141
    plan=[]
    for w in range(TOTAL):
        if w < 111 or w >= 131:
            idx = w if w < 111 else w - 131
            ip, port, user, pwd = (sg[idx] if w < 10 else us[idx]).split(':', 3)
            scheme = 'socks5' if w < 10 else 'http'
            # Preserve the verified proxy grammar and the assigned identity.
            from urllib.parse import quote
            proxy = f'{scheme}://{quote(user,safe="")}:{quote(pwd,safe="")}@{ip}:{port}'
            ident = ua[f'proxy:{ip}']
            host = f'm{w % 5 + 1}'
        else:
            # 直连：身份在 override 定宿主后按最终 host 解析（m6-m20 无身份条目）
            proxy=''; host=f'm{w-110}'; ident=None
        plan.append(dict(w=w,host=host,proxy=proxy,ua=ident))
    overrides = json.loads(PLACEMENTS.read_text()) if PLACEMENTS.exists() else {}
    allowed = set(HOSTS + [f'r{i}' for i in range(1, 21)])
    for p in plan:
        target = overrides.get(str(p['w']))
        if target:
            if target not in allowed:
                raise ValueError('unknown worker host override')
            p['host'] = target
        if not p['proxy']:
            p['ua'] = ua[f'host:{p["host"]}']   # 直连：身份跟随宿主出口
    for p in plan:
        if not p['ua']:
            raise ValueError(f'w{p["w"]} has no identity resolved')
    endpoints=[p['proxy'].split('@')[-1] for p in plan if p['proxy']]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError('duplicate proxy endpoints; refusing independent rate budgets')
    return plan


REMOTE = r'''
import base64,json,os,subprocess,time
config=json.loads(base64.b64decode(PAYLOAD))
wd=os.path.expanduser('~/wk_backfill')
for p in config['workers']:
 w=p['w'];unit=f'kbw{w}'
 active=subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
 if active and not config['restart']:
  print(json.dumps(dict(w=w,status='active')),flush=True);continue
 if active:
  subprocess.run(['sudo','-n','systemctl','stop',unit],check=True)
 subprocess.run(['sudo','-n','systemctl','reset-failed',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 # systemd transient units are collected after stop. Wait until released.
 for _ in range(40):
  st=subprocess.run(['systemctl','show',unit,'-p','LoadState','--value'],capture_output=True,text=True)
  if st.stdout.strip()=='not-found':break
  time.sleep(.25)
 cmd=['python3','-u',wd+'/kb_backfill.py','--tasks',wd+f'/s{w:03d}.jsonl.gz',
      '--shard','0/1','--manifest',wd+f'/run_kb_w{w}/manifest.jsonl',
      '--proxy',p['proxy'],'--ua',p['ua'],'--rps-start',str(config['start']),
      '--rps-max',str(config['max']),'--hard-cap-mb','64','--lanes',str(config['lanes']),'--transport','pycurl']
 if config['limit']:cmd+=['--limit',str(config['limit'])]
 run=['sudo','-n','systemd-run','--quiet','--collect','--unit='+unit,
      '--uid='+str(os.getuid()),'--gid='+str(os.getgid()),
      '--property=WorkingDirectory='+wd,'--property=Restart=on-failure',
      '--property=RestartSec=30','--property=TimeoutStopSec=600',
      '--property=StandardOutput=append:'+wd+f'/kb_w{w}.log',
      '--property=StandardError=append:'+wd+f'/kb_w{w}.log']+cmd
 r=subprocess.run(run,capture_output=True,text=True)
 # Never print systemd's command description (it contains proxy credentials).
 print(json.dumps(dict(w=w,status='started' if r.returncode==0 else 'failed',code=r.returncode)),flush=True)
 time.sleep(.4)
'''


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--workers',help='comma separated worker IDs, default all')
    ap.add_argument('--restart',action='store_true')
    ap.add_argument('--limit',type=int,default=0)
    ap.add_argument('--rps-start',type=float,default=.45)
    ap.add_argument('--rps-max',type=float,default=.6)
    ap.add_argument('--lanes',type=int,default=2)
    args=ap.parse_args()
    selected=set(map(int,args.workers.split(','))) if args.workers else set(range(len(worker_plan())))
    plan=worker_plan(); failed=False
    for h in fleet_hosts(plan):
        workers=[p for p in plan if p['host']==h and p['w'] in selected]
        if not workers:continue
        payload=base64.b64encode(json.dumps(dict(workers=workers,restart=args.restart,limit=args.limit,
                                               start=args.rps_start,max=args.rps_max,lanes=args.lanes)).encode()).decode()
        code=REMOTE.replace('PAYLOAD',repr(payload))
        try:
            r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',h,
                              'python3 -c '+shlex.quote(code)],capture_output=True,text=True,timeout=900)
            print(h,r.stdout.strip(),flush=True)
            if r.returncode or '"failed"' in r.stdout:
                failed=True;print(h,'deployment failed; inspect units',flush=True)
        except subprocess.TimeoutExpired:
            failed=True;print(h,'SSH timeout: verify units before rerunning',flush=True)
        time.sleep(1)
    raise SystemExit(1 if failed else 0)


if __name__=='__main__':main()
