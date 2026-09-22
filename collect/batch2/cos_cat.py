#!/usr/bin/env python3
# 按 key 列表从 COS 直读到 stdout；单块读取中断时按 Range 续传（不重头，保证流对齐）
import sys, time, hmac, hashlib, urllib.request
BUCKET = "lhcos-368f6-1256345599"; REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"

def creds():
    sid, skey = open("/tmp/cos_creds").read().strip().split(":", 1)
    return sid, skey

def stream_object(key):
    written = 0
    for attempt in range(10):
        try:
            now = int(time.time()); kt = f"{now-60};{now+600}"
            sid, skey = creds()
            sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
            hs = f"get\n/{key}\n\nhost={HOST}\n"
            sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
            sig = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
            url = (f"https://{HOST}/{key}?q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}"
                   f"&q-key-time={kt}&q-header-list=host&q-url-param-list=&q-signature={sig}")
            req = urllib.request.Request(url)
            req.add_header("host", HOST)
            if written:
                req.add_header("range", f"bytes={written}-")
            with urllib.request.urlopen(req, timeout=300) as r:
                while True:
                    b = r.read(1 << 22)
                    if not b: return
                    sys.stdout.buffer.write(b); sys.stdout.buffer.flush()
                    written += len(b)
        except Exception as e:
            print(f"WARN {key} @{written} retry{attempt}: {e}", file=sys.stderr, flush=True)
            time.sleep(2 + attempt * 3)
    print(f"FATAL {key} @{written}", file=sys.stderr, flush=True)

for key in sys.argv[1:]:
    stream_object(key)
