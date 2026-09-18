#!/usr/bin/env python3
# 预检：所有块的 HEAD（尺寸正确才放行扫描）
import sys, time, hmac, hashlib, urllib.request
BUCKET="lhcos-368f6-1256345599"; REGION="ap-singapore"
HOST=f"{BUCKET}.cos.{REGION}.myqcloud.com"
sid,skey=open("/tmp/cos_creds").read().strip().split(":",1)
G=1073741824; TOTAL=43422182003; HEAD_END=18000000000
def head_len(key):
    now=int(time.time()); kt=f"{now-60};{now+300}"
    sk=hmac.new(skey.encode(),kt.encode(),hashlib.sha1).hexdigest()
    hs=f"head\n/{key}\n\nhost={HOST}\n"
    sts=f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    sig=hmac.new(sk.encode(),sts.encode(),hashlib.sha1).hexdigest()
    url=(f"https://{HOST}/{key}?q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}"
         f"&q-key-time={kt}&q-header-list=host&q-url-param-list=&q-signature={sig}")
    req=urllib.request.Request(url,method="HEAD"); req.add_header("host",HOST)
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return int(r.headers.get("Content-Length") or 0)
    except Exception: return -1
bad=[]
for i in range(10000,10017):
    off=(i-10000)*G; exp=min(G,HEAD_END-off)
    n=head_len(f"demiwtg-data/datasets/raw/wikimedia/latest-truthy.nt.bz2.part-{i:05d}")
    if n!=exp: bad.append((i,exp,n))
for i in range(0,24):
    off=HEAD_END+i*G; exp=min(G,TOTAL-off)
    n=head_len(f"demiwtg-data/datasets/raw/wikimedia/latest-truthy.nt.bz2.part-{i:05d}")
    if n!=exp: bad.append((i,exp,n))
print("BAD:",bad if bad else "无,全部就绪")
