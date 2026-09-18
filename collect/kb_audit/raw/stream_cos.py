#!/usr/bin/env python3
# stdin -> COS multipart 直传（纯标准库）
# 用法: cat bytes | python3 stream_cos.py <cos_key> <expect_bytes>
import sys, time, hmac, hashlib, urllib.request, urllib.parse, xml.etree.ElementTree as ET

BUCKET = "lhcos-368f6-1256345599"
REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
PART = 16 << 20

def creds():
    sid, skey = open("/tmp/cos_creds").read().strip().split(":", 1)
    return sid, skey

def sig(method, path, params, sid, skey):
    now = int(time.time())
    kt = f"{now-60};{now+900}"
    sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
    p = "&".join(f"{k.lower()}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
    hs = f"{method.lower()}\n{path}\n{p}\nhost={HOST}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    sigv = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list={';'.join(sorted(k.lower() for k in params))}&q-signature={sigv}")

def call(method, path, params, data=None, tries=4):
    last = None
    for t in range(tries):
        try:
            sid, skey = creds()
            q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
            url = f"https://{HOST}{path}" + (f"?{q}" if q else "")
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("host", HOST)
            req.add_header("authorization", sig(method, path, params, sid, skey))
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.status, r.read(), dict(r.headers)
        except Exception as e:
            detail = b""
            if hasattr(e, "read"):
                try: detail = e.read()[:400]
                except Exception: pass
            last = RuntimeError(f"{e} | {detail.decode('utf-8','replace')}")
            time.sleep(3 + t * 5)
    raise last

def main():
    key, expect = sys.argv[1], int(sys.argv[2])
    path = "/" + key
    st, body, _ = call("POST", path, {"uploads": ""}, data=b"")
    up = ET.fromstring(body).find("UploadId").text
    try:
        etags, n, total = [], 1, 0
        while True:
            buf = sys.stdin.buffer.read(PART)
            if not buf: break
            total += len(buf)
            st, _, hdr = call("PUT", path, {"partNumber": str(n), "uploadId": up}, data=buf)
            etags.append((n, hdr.get("ETag") or hdr.get("etag"))); n += 1
        if total != expect:
            raise RuntimeError(f"stdin short {total}/{expect}")
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{p}</PartNumber><ETag>{e}</ETag></Part>" for p, e in etags) + "</CompleteMultipartUpload>"
        for t in range(3):
            try:
                call("POST", path, {"uploadId": up}, data=xml.encode())
                break
            except Exception:
                if t == 2: raise
        st, _, hdr = call("HEAD", path, {})
        clen = int(hdr.get("Content-Length") or 0)
        if clen != expect: raise RuntimeError(f"HEAD len {clen}/{expect}")
        print(f"UPLOADED {key} {total}")
    except Exception as e:
        call("DELETE", path, {"uploadId": up})
        print(f"FAILED {e}", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
