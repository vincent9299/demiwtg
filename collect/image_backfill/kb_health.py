#!/usr/bin/env python3
"""Current fleet health, including host overrides for workers migrated to r hosts.

A failed SSH sample is UNKNOWN, never zero. Seed rows are excluded by timestamp.
--fix only restores missing units; it never resets a throttled/stalled live worker.
"""
import argparse
import base64
import json
from pathlib import Path
import shlex
import subprocess
import time
from rebuild_fleet import HUB, worker_plan, fleet_hosts

REMOTE = r'''
import base64,collections,json,os,subprocess,time
workers=json.loads(base64.b64decode(PAYLOAD)); now=time.time()
wd=os.path.expanduser('~/wk_backfill')
units=subprocess.check_output(['systemctl','list-units','kbw*','--no-legend','--no-pager','--plain']).decode()
active={l.split()[0] for l in units.splitlines() if 'active running' in l}
def tail(path):
 try:
  with open(path,'rb') as f:
   size=os.fstat(f.fileno()).st_size;f.seek(max(0,size-2*1024*1024))
   if f.tell():f.readline()
   data=f.read()
  rows=[]
  for l in data.splitlines():
   try:rows.append(json.loads(l))
   except ValueError:pass
  return rows,size
 except FileNotFoundError:return [],0
out={}
for w in workers:
 root=wd+f'/run_kb_w{w}';m=root+'/manifest.jsonl'
 rows,size=tail(m); recent=[r for r in rows if r.get('fetched_at',0)>=now-300]
 req,_=tail(root+'/meta/requests.jsonl');req=[r for r in req if r.get('ts',0)>=now-300]
 rate={}
 try:rate=json.load(open(root+'/meta/rate_state.json'))
 except (OSError,ValueError):pass
 successes=[r for r in recent if r.get('miss') is None]
 counts=collections.Counter(str(r.get('miss')) for r in recent)
 statuses=collections.Counter(str(r['http_code']) for r in req if r.get('event')=='request')
 requests=[r for r in req if r.get('event')=='request']
 out[w]=dict(active=f'kbw{w}.service' in active,manifest_bytes=size,
             age=round(now-os.path.getmtime(m)) if os.path.exists(m) else None,
             successes300=len(successes),counts300=dict(counts),http300=dict(statuses),
             curl_errors300=sum(bool(r.get('curl_code')) for r in req if r.get('event')=='request'),
             reused300=sum(r.get('num_connects')==0 for r in requests),
             downloaded_bytes300=sum(r.get('size_download',0) for r in requests),
             transfer_seconds300=sum(r.get('time_total',0) for r in requests),
             telemetry=bool(req),rps=rate.get('rps'),cooldown=max(0,rate.get('until',0)-time.time()),
             window_clipped=bool(rows and size>2*1024*1024 and rows[0].get('fetched_at',0)>=now-300))
import glob
network={}
for p in glob.glob('/sys/class/net/*/statistics/rx_bytes'):
 if '/lo/' not in p:network[p.split('/')[4]]=int(open(p).read())
mem={}
for line in open('/proc/meminfo'):
 k,_,v=line.partition(':')
 if k in ('MemTotal','MemAvailable'):mem[k]=int(v.split()[0])
print(json.dumps(dict(ts=now,workers=out,network_rx_bytes=network,load=os.getloadavg(),memory_kb=mem)))
'''


def collect():
    plan=worker_plan(); result={'ts':time.time(),'hosts':{}}
    for h in fleet_hosts(plan):
        ids=[p['w'] for p in plan if p['host']==h]
        code=REMOTE.replace('PAYLOAD',repr(base64.b64encode(json.dumps(ids).encode()).decode()))
        try:
            r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',h,
                              'python3 -c '+shlex.quote(code)],capture_output=True,text=True,timeout=30)
            if r.returncode:raise RuntimeError('ssh_error')
            result['hosts'][h]=json.loads(r.stdout)
        except (subprocess.TimeoutExpired,ValueError,RuntimeError):
            result['hosts'][h]={'unknown':True}
        time.sleep(1)
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--fix',action='store_true')
    ap.add_argument('--output',default=str(HUB/'kb_health_m116.json'));args=ap.parse_args()
    result=collect();missing=[]
    for h,d in result['hosts'].items():
        if d.get('unknown'):
            print(h,'UNKNOWN (SSH failure; excluded from totals)');continue
        workers=d['workers'];active=sum(w['active'] for w in workers.values())
        success=sum(w['successes300'] for w in workers.values())
        throttles=sum(w['http300'].get('429',0) for w in workers.values())
        observed=sum(w['active'] and w['telemetry'] for w in workers.values())
        attempts=sum(sum(w['http300'].values()) for w in workers.values())
        print(h,f'active={active}/{len(workers)} success300={success} rate={success/300:.2f}/s '
                f'HTTP429={throttles}/{attempts} telemetry={observed}/{active}')
        for w,s in workers.items():
            if not s['active']:missing.append(int(w))
            if not s['active'] or s['cooldown'] or (s['age'] or 0)>1200:
                print(' ',w,'missing' if not s['active'] else 'waiting', 'age',s['age'],'cooldown',round(s['cooldown']))
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2))
    if args.fix and missing:
        subprocess.run(['python3',str(Path(__file__).with_name('rebuild_fleet.py')),
                        '--workers',','.join(map(str,missing))],check=True)


if __name__=='__main__':main()
