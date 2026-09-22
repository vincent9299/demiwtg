#!/usr/bin/env python3
# 通用图采器：URL 清单 -> sha256 内容寻址 blob 直传 COS(kb/blobs) + jsonl 账本，幂等可续
# 输入清单: url \t extid \t landing_url \t license \t author   (tsv)
# 用法: python3 fetch_generic.py <list.tsv> <shard_idx> <shard_total> <source_name> [max_bytes]
import sys, os, time, json, hashlib, hmac, threading, urllib.request, urllib.parse, queue
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

BUCKET = "lhcos-368f6-1256345599"; REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
PART = 8 << 20
MAXB = int(sys.argv[5]) if len(sys.argv) > 5 else (64 << 20)

def creds():
    sid, skey = open("/tmp/cos_creds").read().strip().split(":", 1)
    return sid, skey

def sig(method, path, params, sid, skey):
    now = int(time.time()); kt = f"{now-60};{now+900}"
    sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
    p = "&".join(f"{k.lower()}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
    hs = f"{method.lower()}\n{path}\n{p}\nhost={HOST}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    return (f"q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list={';'.join(sorted(k.lower() for k in params))}"
            f"&q-signature={hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()}")

def call(method, path, params, data=None, tries=3):
    last = None
    for t in range(tries):
        try:
            sid, skey = creds()
            q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
            url = f"https://{HOST}{path}" + (f"?{q}" if q else "")
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("host", HOST)
            req.add_header("authorization", sig(method, path, params, sid, skey))
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.status, r.read(), dict(r.headers)
        except Exception as e:
            last = e; time.sleep(2 + t * 4)
    raise last

def head_len(key):
    try:
        _, _, h = call("HEAD", "/" + key, {})
        return int(h.get("Content-Length") or 0)
    except Exception:
        return -1

def upload(key, gen, total):
    """gen: 生成器产出 bytes; multipart 上传"""
    st, body, _ = call("POST", "/" + key, {"uploads": ""}, data=b"")
    up = ET.fromstring(body).find("UploadId").text
    path = "/" + key
    try:
        etags, n, got = [], 1, 0
        for buf in gen:
            got += len(buf)
            st, _, h = call("PUT", path, {"partNumber": str(n), "uploadId": up}, data=buf)
            etags.append((n, h.get("ETag") or h.get("etag"))); n += 1
        if got != total: raise RuntimeError(f"short {got}/{total}")
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{p}</PartNumber><ETag>{e}</ETag></Part>" for p, e in etags) + "</CompleteMultipartUpload>"
        call("POST", path, {"uploadId": up}, data=xml.encode())
    except Exception:
        try: call("DELETE", path, {"uploadId": up})
        except Exception: pass
        raise

def gen_and_hash(resp, hasher):
    while True:
        b = resp.read(PART)
        if not b: break
        hasher.update(b)
        yield b

def fetch_one(job):
    url, extid, landing, lic, author = job
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1][:5] or ".bin"
    req = urllib.request.Request(url, headers={"User-Agent": "ConceptKB/1.0 (mengdebin@bytedance.com)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if (r.headers.get("Content-Type") or "").startswith("text/"): return ("err", url, "html-not-image")
            body = r.read(MAXB + 1)
        if len(body) > MAXB: return ("big", url, len(body))
        if len(body) < 100: return ("err", url, f"tiny {len(body)}B")
        sha = hashlib.sha256(body).hexdigest()
        key = f"demiwtg-data/datasets/demiwtg/kb/blobs/{sha[:2]}/{sha}{ext}"
        if head_len(key) != len(body):
            parts = [body[i:i+PART] for i in range(0, len(body), PART)]
            upload(key, iter(parts), len(body))
        return ("ok", url, sha, len(body), extid, landing, lic, author)
    except Exception as e:
        return ("err", url, str(e)[:80])

def main():
    lst, idx, tot, source = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    jobs = []
    with open(lst, encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) >= 2: jobs.append(tuple(c[:5]))
    mine = jobs[idx::tot]
    ledger = os.path.expanduser(f"~/ledger_{source}_{idx}.jsonl")
    done = set()
    if os.path.exists(ledger):
        for line in open(ledger):
            try: done.add(json.loads(line)["content_url"])
            except Exception: pass
    todo = [j for j in mine if j[0] not in done]
    print(f"shard {idx}/{tot}: {len(mine)} rows, {len(done)} done, {len(todo)} todo", flush=True)
    n_ok = n_err = 0
    with open(ledger, "a", buffering=1) as lf, ThreadPoolExecutor(8) as ex:
        for res in ex.map(fetch_one, todo):
            if res[0] == "ok":
                n_ok += 1
                _, url, sha, size, extid, landing, lic, author = res
                lf.write(json.dumps({"sha256": sha, "source": source, "license": lic,
                                     "author": author, "content_url": url, "landing_url": landing,
                                     "external_id": extid, "size_bytes": size,
                                     "fetched_at": time.time()}, ensure_ascii=False) + "\n")
            else:
                n_err += 1
            if (n_ok + n_err) % 200 == 0:
                print(f"[{time.strftime('%H:%M:%S')}] ok={n_ok} err={n_err}", flush=True)
    print(f"DONE ok={n_ok} err={n_err}", flush=True)

if __name__ == "__main__":
    main()
