#!/usr/bin/env python3
# DF20 融合：COS tar 流式解包 -> sha256 blob 入池 -> qid_images_ext/df20.jsonl + 节点投喂清单
# 依赖: /tmp/cos_creds, DF20-metadata(已解包在 /tmp/df20m 或重新从 COS 拉取)
import sys, os, io, csv, gzip, time, json, hashlib, hmac, subprocess, tarfile, urllib.request, urllib.parse
import xml.etree.ElementTree as ET

BUCKET = "lhcos-368f6-1256345599"; REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
PART = 8 << 20

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

import http.client, threading
_TLS = threading.local()
def _conn():
    c = getattr(_TLS, "c", None)
    if c is None:
        c = _TLS.c = http.client.HTTPSConnection(HOST, timeout=300)
    return c

def call(method, path, params, data=None, tries=3):
    last = None
    for t in range(tries):
        try:
            sid, skey = creds()
            q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted(params.items()))
            c = _conn()
            c.request(method, path + (f"?{q}" if q else ""), body=data, headers={
                "host": HOST, "authorization": sig(method, path, params, sid, skey)})
            r = c.getresponse()
            body = r.read()
            if r.status >= 500:
                raise RuntimeError(f"5xx {r.status}")
            return r.status, body, dict(r.getheaders())
        except Exception as e:
            last = e
            try: _TLS.c.close()
            except Exception: pass
            _TLS.c = None
            time.sleep(1 + t * 2)
    raise last

def put_simple(key, body):
    """小对象单 PUT(走持久连接)"""
    path = "/" + key
    sid, skey = creds()
    call("PUT", path, {}, data=body)
    return len(body)

def head_len(key):
    try:
        _, _, h = call("HEAD", "/" + key, {})
        return int(h.get("Content-Length") or 0)
    except Exception:
        return -1

def upload(key, body):
    if len(body) < (16 << 20):
        return put_simple(key, body)
    parts = [body[i:i+PART] for i in range(0, len(body), PART)]
    st, resp, _ = call("POST", "/" + key, {"uploads": ""}, data=b"")
    up = ET.fromstring(resp).find("UploadId").text
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

def fetch_gen(key):
    """真生成器: 按序 yield 单个对象的字节块(带断点续读)"""
    written = 0
    for attempt in range(10):
        try:
            sid, skey = creds()
            now = int(time.time()); kt = f"{now-60};{now+600}"
            sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
            hs = f"get\n/{key}\n\nhost={HOST}\n"
            sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
            sigv = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
            url = (f"https://{HOST}/{key}?q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}"
                   f"&q-key-time={kt}&q-header-list=host&q-url-param-list=&q-signature={sigv}")
            req = urllib.request.Request(url); req.add_header("host", HOST)
            if written: req.add_header("range", f"bytes={written}-")
            with urllib.request.urlopen(req, timeout=300) as r:
                while True:
                    b = r.read(1 << 22)
                    if not b: return
                    written += len(b); yield b
        except Exception as e:
            print(f"WARN {key} @{written} rt{attempt}: {e}", flush=True); time.sleep(2 + attempt * 3)

def cos_stream(paths):
    """顺序读 cosfs 路径(全流探测已验证此路径干净)"""
    class R:
        def __init__(self):
            self.files = [open(p, "rb") for p in paths]
            self.idx = 0
        def read(self, sz=-1):
            if sz == 0: return b""
            while self.idx < len(self.files):
                b = self.files[self.idx].read(sz if sz and sz > 0 else 1 << 30)
                if b: return b
                self.files[self.idx].close(); self.idx += 1
            return b""
        def close(self):
            for f in self.files: 
                try: f.close()
                except Exception: pass
    return R()

def main():
    t0 = time.time()
    # ① 桥: P225/P846 -> qid
    by_name, by_gbif = {}, {}
    z = subprocess.Popen(["zcat", "/tmp/concept_xref_p225846.tsv.gz"], stdout=subprocess.PIPE, bufsize=1<<22)
    for line in z.stdout:
        qid, prop, val = line.decode().rstrip("\n").split("\t")
        if prop == "P225": by_name[val] = qid
        else: by_gbif[val] = qid
    z.wait()
    print(f"[{time.strftime('%H:%M:%S')}] 桥就绪 P225={len(by_name):,} P846={len(by_gbif):,}", flush=True)
    # 概念集过滤(783万)
    concepts = set()
    p2 = subprocess.Popen(["zcat", "/tmp/qid_concepts.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1<<22)
    for line in p2.stdout:
        try: concepts.add(line.decode().split('"')[3])
        except Exception: pass
    p2.wait()
    print(f"[{time.strftime('%H:%M:%S')}] 概念集 {len(concepts):,}", flush=True)
    # ② DF20 元数据: 文件名 -> (qid, 学名)
    csv.field_size_limit(10**9)
    want = {}
    for fn in ["/tmp/df20m/DF20-train_metadata_PROD-2.csv", "/tmp/df20m/DF20-public_test_metadata_PROD-2.csv"]:
        with open(fn, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                nm = row["scientificName"]; toks = nm.split()
                key = " ".join(toks[:2]) if len(toks) > 2 else nm
                qid = by_name.get(key) or by_name.get(nm) or by_gbif.get(row.get("gbifID", ""))
                if qid and qid in concepts:
                    img = "DF20/" + row["filename"] if "filename" in row else None
                    if not img:
                        # 元数据里图片路径字段探测
                        for k in ("image_path", "ImageUniqueID", "filename", "image_file_name"):
                            if row.get(k): img = "DF20/" + os.path.basename(row[k]); break
                    if img: want[img] = (qid, nm)
    print(f"[{time.strftime('%H:%M:%S')}] 可挂载文件映射(概念集内): {len(want):,}", flush=True)
    del by_name, by_gbif, concepts  # 释放内存再流 tar
    # ③ 流式过 tar
    keys = [f"/lhcos-data/demiwtg-data/datasets/raw/df20/DF20-train_val.tar.gz.part-{i:05d}" for i in range(108)]
    tf = tarfile.open(fileobj=cos_stream(keys), mode="r|gz")
    ledger_local = os.path.expanduser("~/lake/meta/image-shard-extdf20.jsonl")
    os.makedirs(os.path.dirname(ledger_local), exist_ok=True)
    ext_out = gzip.open(os.path.expanduser("~/qid_images_ext_df20.jsonl.gz"), "wt", compresslevel=1)
    n_seen = n_matched = n_uploaded = n_dedup = 0
    import threading, queue as _q
    tq = _q.Queue(maxsize=64)
    lock = threading.Lock()
    def worker():
        nonlocal n_uploaded, n_dedup
        while True:
            item = tq.get()
            if item is None: return
            body, sha, ext, qid, nm, base = item
            key = f"lhcos-data/demiwtg-data/datasets/demiwtg/blobs/{sha[:2]}/{sha}.{ext}"
            try:
                if head_len(key) == len(body): n_dedup += 1
                else:
                    upload(key, body); n_uploaded += 1
                row = {"qid": qid, "sha256": sha, "blob_path": f"blobs/{sha[:2]}/{sha}.{ext}", "path": f"blobs/{sha[:2]}/{sha}.{ext}",
                       "source": "df20", "license": "CC BY", "size_bytes": len(body),
                       "relation_type": "definitional", "external_id": nm, "confidence": "double-bridge",
                       "orig_path": base, "fused_at": time.time()}
                line = json.dumps(row, ensure_ascii=False)
                with lock:
                    ext_out.write(line + "\n")
                    with open(ledger_local, "a") as lf: lf.write(line + "\n")
            except Exception as e:
                print(f"ERR {base}: {e}", flush=True)
            tq.task_done()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    for t in threads: t.start()
    for m in tf:
        if not m.isfile(): continue
        n_seen += 1
        base = os.path.basename(m.name)
        hit = want.get(m.name.lstrip("./")) or want.get("DF20/" + base)
        if not hit: continue
        qid, nm = hit
        f = tf.extractfile(m)
        body = f.read()
        sha = hashlib.sha256(body).hexdigest()
        ext = os.path.splitext(base)[1].lower().lstrip(".") or "jpg"
        n_matched += 1
        tq.put((body, sha, ext, qid, nm, base))
        if n_matched % 20000 == 0:
            tq.join()
            ext_out.flush()
            print(f"[{time.strftime('%H:%M:%S')}] tar过{n_seen/1e3:.0f}K 挂{n_matched/1e3:.0f}K 传{n_uploaded/1e3:.0f}K 重{n_dedup/1e3:.0f}K {time.time()-t0:.0f}s", flush=True)
    tq.join()
    for t in threads: tq.put(None)
    ext_out.close()
    print(f"DONE tar={n_seen} matched={n_matched} uploaded={n_uploaded} dedup={n_dedup} {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
