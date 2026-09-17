#!/usr/bin/env python3
"""SDC ② 机群下载器: fetch_list 分片 → MD5路径URL 下载 → sha256 blob(cosfs) → 双落账本。

契约(与 DF20 融合同构,参考 /tmp/fuse_df20.py 与 flow_images_batch.py):
- 输入行: fname \t mid \t qid:rank,... \t img_size (gz 或裸 tsv;第5列如存在视为
  失败原因,即 failures.tsv 可直接作为 --list 重试)
- URL: https://upload.wikimedia.org/wikipedia/commons/<md5[0]>/<md5[:2]>/<quote(fname)>
  备选: commons.wikimedia.org/wiki/Special:FilePath/<quote(fname)>(手动跟一次重定向)
- blob: <blobs-root>/<sha[:2]>/<sha>.<ext>,先 stat 查重,cosfs tmp+rename 原子落
- 账本行(每 (qid,file) 一行): qid/sha256/blob_path/path/source=sdc/license=null/
  size_bytes/relation_type=depicts_part/external_id=mid/confidence=sdc-p180/
  rank/orig_file/fused_at
- 双落: ①节点投喂 ~/lake/meta/image-shard-extsdcfetch.jsonl(blob_path 字段必须存在)
       ②COS 真相分片 <cos-part>/<host>.jsonl.gz(周期性整备重传,收官合并)
- 幂等: state/ledger.jsonl 里的 fname 重启即跳过;失败行 failures.tsv 可重试
- 礼貌: UA 见下;并发 ≤8/机;令牌桶限速;429 指数退避 30/120/300s
- 坑位应对: http.client 线程本地;MD5 用未转义 UTF-8 原文;URL quote 只作用于路径
"""
import argparse
import gzip
import hashlib
import http.client
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.parse

UA = "ConceptKB/1.0 (mengdebin@bytedance.com)"
BACKOFF = (30, 120, 300)
HARD_CAP = 64 << 20
TLS = ssl.create_default_context()
_local = threading.local()
stat_lock = threading.Lock()
write_lock = threading.Lock()
n_429 = 0
n_429_by_host = {}


class Bucket:
    """跨线程共享令牌桶(无突发);429 时 penalize 集体降速。"""

    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.next_at = 0.0
        self.lock = threading.Lock()

    def take(self):
        while True:
            with self.lock:
                now = time.monotonic()
                if self.next_at <= now:
                    self.next_at = now + self.interval
                    return
                wait = self.next_at - now
            time.sleep(wait)

    def penalize(self, sec):
        with self.lock:
            self.next_at = max(self.next_at,
                               time.monotonic() + sec)


def get_conn(host, timeout=30):
    c = getattr(_local, host, None)
    if c is None:
        c = http.client.HTTPSConnection(host, timeout=timeout, context=TLS)
        setattr(_local, host, c)
    return c


def http_get(host, path, bucket, tries=4, depth=0):
    """线程本地持久连接 GET;返回 (status, body|None)。429/5xx/断连自动退避重试。

    429 随机抖动防全 worker 同步休眠,并给所属桶集体降速(TCP 式拥塞反应)。
    301/302/307/308 跟一次重定向(Special:FilePath 必经)。
    """
    global n_429
    for attempt in range(tries):
        try:
            c = get_conn(host)
            c.request("GET", path, headers={
                "User-Agent": UA, "Accept": "*/*",
                "Connection": "close"})   # 每请求新连接:历史fleet实测
            r = c.getresponse()           # keep-alive 是限流器靶点
            if r.status == 200:
                body = r.read()
                c.close()
                return 200, body
            if r.status in (301, 302, 307, 308) and depth < 3:
                loc = r.getheader("Location")
                r.read()
                if loc:
                    u = urllib.parse.urlsplit(loc)
                    return http_get(u.netloc or host,
                                    u.path + (f"?{u.query}" if u.query else ""),
                                    bucket, tries, depth + 1)
                continue
            if r.status == 429:
                n_429 += 1
                n_429_by_host[host] = n_429_by_host.get(host, 0) + 1
                ra = r.getheader("Retry-After")
                c.close()
                if ra and ra.isdigit() and int(ra) <= 120:
                    sleep = int(ra) + 1          # 服务器明说的等待,照办
                    bucket.penalize(int(ra))
                else:
                    bucket.penalize(5.0)
                    import random
                    sleep = BACKOFF[min(attempt, len(BACKOFF) - 1)] * \
                        random.uniform(0.6, 1.4)
                if n_429 % 20 == 1:
                    print(f"[429] host={host} retry-after={ra} "
                          f"path={path[:80]}", file=sys.stderr, flush=True)
            elif 500 <= r.status < 600:
                sleep = 2 * (attempt + 1)
            else:
                r.read()
                c.close()
                return r.status, None     # 404/403/410 等:不重试
            r.read()
            c.close()
            time.sleep(sleep)
        except (http.client.HTTPException, socket.error, OSError):
            try:
                get_conn(host).close()
            except Exception:
                pass
            if hasattr(_local, host):
                delattr(_local, host)
            time.sleep(1 + attempt * 2)
    return 0, None                      # 重试用尽


def fetch_bytes(fname, dl_bucket, fp_bucket):
    """主路径 MD5 命名 → 备选 Special:FilePath(独立慢桶,API 集群限 ~1rps)。"""
    md5 = hashlib.md5(fname.encode("utf-8")).hexdigest()
    quoted = urllib.parse.quote(fname, safe="")
    dl_bucket.take()
    st, body = http_get(
        "upload.wikimedia.org",
        f"/wikipedia/commons/{md5[0]}/{md5[:2]}/{quoted}", dl_bucket)
    if st == 200 and body:
        return body, "orig"
    fp_bucket.take()
    st2, body2 = http_get(
        "commons.wikimedia.org",
        f"/wiki/Special:FilePath/{quoted}?redirect=yes", fp_bucket)
    if st2 == 200 and body2:
        return body2, "redirect"
    return None, f"md5:{st}/fp:{st2}"


def safe_ext(fname):
    e = os.path.splitext(fname)[1].lower().lstrip(".")
    return e if e.isalnum() and len(e) <= 5 else "bin"


def write_blob(root, sha, ext, data):
    rel = f"{sha[:2]}/{sha}.{ext}"
    path = f"{root}/{rel}"
    if os.path.exists(path):
        return path, True                      # 查重跳过
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path, False


def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["orig_file"])
                except Exception:
                    continue
    return done


def iter_tasks(list_path, shard_i, shard_n, done):
    opener = gzip.open if list_path.endswith(".gz") else open
    idx = 0
    with opener(list_path, "rt", encoding="utf-8", errors="surrogateescape") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            fname, mid, pairs = parts[0], parts[1], parts[2]
            if idx % shard_n == shard_i and fname not in done:
                yield fname, mid, pairs, int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            idx += 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--list", required=True)
    p.add_argument("--shard", required=True, metavar="I/N")
    p.add_argument("--dl-conc", type=int, default=8)
    p.add_argument("--dl-rate", type=float, default=6.0)
    p.add_argument("--state", default=os.path.expanduser("~/sdc_fetch"))
    p.add_argument("--blobs-root",
                   default="/lhcos-data/demiwtg-data/datasets/demiwtg/blobs")
    p.add_argument("--feed", default=os.path.expanduser(
        "~/lake/meta/image-shard-extsdcfetch.jsonl"))
    p.add_argument("--cos-part", default="/lhcos-data/demiwtg-data/"
                   "datasets/demiwtg/kb/qid_images_ext/sdc_fetch/parts")
    p.add_argument("--ua", default=None,
                   help="A/B 探测:覆盖默认 UA(正式跑勿用)")
    args = p.parse_args()
    if args.ua:
        global UA
        UA = args.ua
    shard_i, shard_n = (int(x) for x in args.shard.split("/"))
    host = os.uname().nodename
    os.makedirs(args.state, exist_ok=True)
    os.makedirs(os.path.dirname(args.feed), exist_ok=True)
    os.makedirs(args.cos_part, exist_ok=True)
    ledger_path = f"{args.state}/ledger.jsonl"
    fail_path = f"{args.state}/failures.tsv"
    log = open(f"{args.state}/run.log", "a", buffering=1)

    def say(msg):
        log.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    dl_bucket = Bucket(args.dl_rate)
    fp_bucket = Bucket(1.0)               # Special:FilePath=API 集群,独立慢桶
    done = load_done(ledger_path)
    say(f"start shard={args.shard} host={host} done={len(done):,} "
        f"conc={args.dl_conc} rate={args.dl_rate}")

    n_ok = n_fail = n_dedup = 0
    t0 = time.time()
    feed_f = open(args.feed, "a", encoding="utf-8")
    led_f = open(ledger_path, "a", encoding="utf-8")

    def upload_part():
        tmp = f"{args.cos_part}/.{host}.tmp.gz"
        dst = f"{args.cos_part}/{host}.jsonl.gz"
        os.system(f"gzip -c {ledger_path} > {tmp} 2>/dev/null && mv {tmp} {dst}")

    task_lock = threading.Lock()

    def next_task():
        with task_lock:
            return next(tasks, None)

    def worker():
        nonlocal n_ok, n_fail, n_dedup
        while True:
            task = next_task()
            if task is None:
                return
            fname, mid, pairs, size = task
            t_dl0 = time.monotonic()
            data, why = fetch_bytes(fname, dl_bucket, fp_bucket)
            t_dl = time.monotonic() - t_dl0
            t_bl0 = time.monotonic()
            if data is None or len(data) > HARD_CAP:
                with write_lock:
                    with open(fail_path, "a", encoding="utf-8") as ff:
                        ff.write(f"{fname}\t{mid}\t{pairs}\t{size}\t{why}\n")
                    n_fail += 1
                    if n_fail % 100 == 0:
                        say(f"fail={n_fail:,} last={fname[:60]} {why}")
                continue
            sha = hashlib.sha256(data).hexdigest()
            ext = safe_ext(fname)
            _, dedup = write_blob(args.blobs_root, sha, ext, data)
            n_dedup += dedup
            t_bl = time.monotonic() - t_bl0
            if t_dl > 3 or t_bl > 3:
                say(f"SLOW {fname[:50]} dl={t_dl:.1f}s blob={t_bl:.1f}s 429={n_429}")
            now = time.time()
            with write_lock:
                for pair in pairs.split(","):
                    qid, _, rank = pair.partition(":")
                    row = {"qid": qid, "sha256": sha,
                           "blob_path": f"blobs/{sha[:2]}/{sha}.{ext}",
                           "path": f"blobs/{sha[:2]}/{sha}.{ext}",
                           "source": "sdc", "license": None,
                           "size_bytes": len(data),
                           "relation_type": "depicts_part",
                           "external_id": mid, "confidence": "sdc-p180",
                           "rank": rank or "normal", "orig_file": fname,
                           "fused_at": now}
                    line = json.dumps(row, ensure_ascii=False)
                    led_f.write(line + "\n")
                    feed_f.write(line + "\n")
                led_f.flush()
                feed_f.flush()
                n_ok += 1
                if n_ok % 200 == 0:
                    el = time.time() - t0
                    say(f"sunk={n_ok:,} fail={n_fail:,} dedup={n_dedup:,} "
                        f"429={n_429:,} ({n_ok/el:.2f}/s)")
                if n_ok % 5000 == 0:
                    upload_part()

    tasks = iter_tasks(args.list, shard_i, shard_n, done)
    threads = [threading.Thread(target=worker, daemon=True)
               for _ in range(args.dl_conc)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    upload_part()
    el = time.time() - t0
    say(f"DONE sunk={n_ok:,} fail={n_fail:,} dedup={n_dedup:,} "
        f"elapsed={el/60:.1f}min")
    print(f"DONE sunk={n_ok:,} fail={n_fail:,}")


if __name__ == "__main__":
    main()
