#!/usr/bin/env python3
# Met 两段式采集：objects API 拿 primaryImage -> 图字节 -> sha256 blob 直传 COS + 账本(带 QID)
# 用法: python3 met_fetch.py <shard_idx> <shard_total>
import sys, os, time, json, csv, hashlib, hmac, urllib.request, urllib.parse, threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

BUCKET = "lhcos-368f6-1256345599"; REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
PART = 8 << 20; MAXB = 64 << 20
UA = {"User-Agent": "ConceptKB/1.0 (mengdebin@bytedance.com)"}
csv.field_size_limit(10**9)

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

def upload(key, parts, total):
    st, body, _ = call("POST", "/" + key, {"uploads": ""}, data=b"")
    up = ET.fromstring(body).find("UploadId").text
    path = "/" + key
    try:
        etags = []
        for n, buf in enumerate(parts, 1):
            st, _, h = call("PUT", path, {"partNumber": str(n), "uploadId": up}, data=buf)
            etags.append((n, h.get("ETag") or h.get("etag")))
        xml = ("<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{p}</PartNumber><ETag>{e}</ETag></Part>" for p, e in etags)
            + "</CompleteMultipartUpload>")
        call("POST", path, {"uploadId": up}, data=xml.encode())
    except Exception:
        try: call("DELETE", path, {"uploadId": up})
        except Exception: pass
        raise

api_cache = {}
def met_api_image(oid):
    if oid in api_cache: return api_cache[oid]
    try:
        req = urllib.request.Request(
            f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        img = d.get("primaryImage") or ""
        api_cache[oid] = img
        return img
    except Exception:
        api_cache[oid] = ""
        return ""

def work(row):
    oid, qid, landing, lic, artist, title = row
    img = met_api_image(oid)
    if not img: return ("noimg", oid)
    try:
        req = urllib.request.Request(img, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            if (r.headers.get("Content-Type") or "").startswith("text/"): return ("err", oid, "html")
            body = r.read(MAXB + 1)
        if len(body) > MAXB or len(body) < 100: return ("skip", oid, len(body))
        sha = hashlib.sha256(body).hexdigest()
        ext = os.path.splitext(urllib.parse.urlparse(img).path)[1][:5] or ".jpg"
        key = f"lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs/{sha[:2]}/{sha}{ext}"
        if head_len(key) != len(body):
            upload(key, [body[i:i+PART] for i in range(0, len(body), PART)], len(body))
        return ("ok", {"qid": qid, "object_id": oid, "sha256": sha, "ext": ext.lstrip("."),
                       "source": "metmuseum", "license": lic, "artist": artist, "title": title,
                       "content_url": img, "landing_url": landing,
                       "size_bytes": len(body), "fetched_at": time.time()})
    except Exception as e:
        return ("err", oid, str(e)[:60])

def main():
    idx, tot = int(sys.argv[1]), int(sys.argv[2])
    rows = []
    with open(os.path.expanduser("~/met_pd_all.tsv"), encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) >= 6: rows.append(tuple(c[:6]))
    mine = rows[idx::tot]
    ledger = os.path.expanduser(f"~/ledger_met_{idx}.jsonl")
    done = set()
    if os.path.exists(ledger):
        for line in open(ledger):
            try: done.add(json.loads(line)["object_id"])
            except Exception: pass
    todo = [r for r in mine if r[0] not in done]
    print(f"met shard {idx}/{tot}: {len(mine)} rows, done {len(done)}, todo {len(todo)}", flush=True)
    n = e = 0
    with open(ledger, "a", buffering=1) as lf, ThreadPoolExecutor(8) as ex:
        for res in ex.map(work, todo):
            if res[0] == "ok":
                lf.write(json.dumps(res[1], ensure_ascii=False) + "\n"); n += 1
            else:
                e += 1
            if (n + e) % 500 == 0:
                print(f"[{time.strftime('%H:%M:%S')}] ok={n} err={e}", flush=True)
    print(f"DONE ok={n} err={e}", flush=True)

if __name__ == "__main__":
    main()
