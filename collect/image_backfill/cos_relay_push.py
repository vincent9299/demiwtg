#!/usr/bin/env python3
"""sg 端 123pan 中继推送器（常驻流式管线）。

SG 桶(只读源) → 环形扫描 → 生产账本比对 → 流式 tar 打包/standalone → GZ 桶 pan123-relay/ 队列。
fleet 下载完全不受影响；背压只作用于本管线自身（扫描/封卷/推送速率）。

用法（sg 机, ~/pan123-relay/ 下）:
  python3 cos_relay_push.py --shard 0/2          # sg1
  python3 cos_relay_push.py --shard 1/2          # sg2
  python3 cos_relay_push.py --dry-run            # 只扫描统计, 不推
  python3 cos_relay_push.py --shard 0/2 --max-units 5 --subtrees docs  # 试点限量

契约要点（collect/MEMORY.md §〇 v2）:
- 闭集子树: datasets/demiwtg/kb/ + datasets/raw/ + datasets/candidate/ + docs/
- kb/blobs 散图进 tar(成员整块入内存,≤MEMBER_CAP; 超限转 standalone);
  其余原文件 standalone 直推, GZ/123pan 镜像原相对路径
- 毒闸门: kb/blobs 下 last_modified < BLOBS_T0 跳过(毒 blob 钉死 09-12~14 窗);
  存量基线阶段(毒清理后)用 --blobs-t0 0 关闸
- 分片: kb/blobs/<sha2>/ 按 int(sha2,16)%M, 其余 key 按 md5(key)%M
- 队列对象: 数据 pan123-relay/<相对路径>; tar 配同目录 .manifest.jsonl;
  每上传单元配边车 _meta/<单元路径>.json (源 md5/size, cn1 端核验用);
  _meta/_status/_ledger 为保留区, 数据对象绝不使用
- 背压: 盘(spool>BUDGET 或 free<FLOOR)停收成员; 队列字节上限停扫;
  cn1 心跳 stale>24h 或积压>50GB 停推(仅上传, 不影响扫描)
- 崩溃安全: 账本只在单元上传+校验成功后落; .tmp 永不上传; 重启清 .tmp、重推已封未记账卷
"""
import argparse
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import threading
import time
import urllib.parse

import requests

# ---------- 常量 ----------
SRC_HOST = "lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com"
DST_HOST = "lhcos-cee54-1256345599.cos.ap-guangzhou.myqcloud.com"
SRC_ROOT = "lhcos-data/demiwtg-data"
DST_ROOT = "pan123-relay"
DEFAULT_SUBTREES = ["datasets/demiwtg/kb", "datasets/raw", "datasets/candidate", "docs"]
BLOBS_MARK = "datasets/demiwtg/kb/blobs/"

TAR_CAP = 4 * 1024 ** 3          # tar 封卷上限(保 ≤5GiB 简单 PUT 线)
MEMBER_CAP = 96 * 1024 ** 2      # tar 成员入内存上限, 超过转 standalone
MULTIPART_TH = 4 * 1024 ** 3     # standalone 超此值走分片 PUT
PART_SIZE = 64 * 1024 ** 2
QUEUE_BYTES_MAX = 2 * 1024 ** 3  # 扫描→打包待处理字节上限(超则扫描阻塞)
BACKLOG_MAX = 50 * 1024 ** 3     # cn1 心跳积压超此停推
STATUS_STALE_S = 24 * 3600
LEDGER_SNAP_S = 1800
DISK_FLOOR = 8 * 1024 ** 3

XML_BLOCK = re.compile(r"<Contents>(.*?)</Contents>", re.S)
XML_KEY = re.compile(r"<Key>([^<]+)</Key>")
XML_LM = re.compile(r"<LastModified>([^<]+)</LastModified>")
XML_ETAG = re.compile(r"<ETag>([^<]+)</ETag>")
XML_SIZE = re.compile(r"<Size>(\d+)</Size>")

_stop = threading.Event()


def log(msg):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


def dead_letter(spool, kind, info):
    with open(os.path.join(spool, "dead_units.jsonl"), "a") as f:
        f.write(json.dumps({"kind": kind, "info": info, "ts": time.time()}) + "\n")


# ---------- COS 签名与调用（算法同 relay_a/cos_util，host/params 参数化） ----------
def creds():
    for p in (os.path.expanduser("~/pan123-relay/.cos_creds"), "/tmp/cos_creds"):
        try:
            sid, skey = open(p).read().strip().split(":", 1)
            if sid and skey:
                return sid, skey
        except (OSError, ValueError):
            continue
    sys.exit("无 COS 凭据: ~/pan123-relay/.cos_creds 与 /tmp/cos_creds 均缺失")


SID, SKEY = creds()
_local = threading.local()


def sig(method, path, params, host):
    now = int(time.time())
    kt = f"{now - 60};{now + 900}"
    sk = hmac.new(SKEY.encode(), kt.encode(), hashlib.sha1).hexdigest()
    p = "&".join(f"{k.lower()}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted(params.items()))
    hs = f"{method.lower()}\n{path}\n{p}\nhost={host}\n"
    sts = f"sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n"
    v = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={SID}&q-sign-time={kt}&q-key-time={kt}"
            f"&q-header-list=host&q-url-param-list={';'.join(sorted(k.lower() for k in params))}"
            f"&q-signature={v}")


def sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def cos_call(host, method, key, params=None, data=None, stream=False, timeout=(15, 600)):
    """key 不带前导斜杠; stream=True 返回 Response 供迭代。"""
    path = ("/" + key) if key else "/"
    q = "&".join(f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted((params or {}).items()))
    url = f"https://{host}{urllib.parse.quote(path)}" + (f"?{q}" if q else "")
    try:
        r = sess().request(method, url, data=data, stream=stream,
                           headers={"authorization": sig(method, path, params or {}, host)},
                           timeout=timeout)
        if stream:
            return r.status_code, r.headers, r
        return r.status_code, r.headers, (r.content if r.status_code < 400 else r.content[:400])
    except requests.RequestException:
        try:
            _local.s.close()
            del _local.s
        except Exception:
            pass
        return 0, {}, b"transport_error"


def cos_list_page(host, prefix, marker):
    params = {"prefix": prefix, "max-keys": "1000"}
    if marker:
        params["marker"] = marker
    st, _, body = cos_call(host, "GET", "", params=params, timeout=(15, 120))
    if st != 200:
        raise RuntimeError(f"list {prefix} 失败: {st} {body[:120]!r}")
    t = body.decode("utf-8", "replace")
    out = []
    for blk in XML_BLOCK.findall(t):
        k, s = XML_KEY.search(blk), XML_SIZE.search(blk)
        if not (k and s):
            continue
        et, lm = XML_ETAG.search(blk), XML_LM.search(blk)
        out.append((k.group(1), int(s.group(1)),
                    et.group(1).replace("&quot;", "").strip('"') if et else "",
                    lm.group(1) if lm else ""))
    trunc = "<IsTruncated>true</IsTruncated>" in t
    nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", t)
    return out, trunc, (nm.group(1) if nm else "")


def md5_stream(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- 账本 ----------
class Ledger:
    def __init__(self, spool):
        os.makedirs(spool, exist_ok=True)
        self.con = sqlite3.connect(os.path.join(spool, "ledger.db"), timeout=60,
                                   check_same_thread=False)  # 自带锁串行化, 跨线程共用一条连接
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("""CREATE TABLE IF NOT EXISTS objects(
            key TEXT PRIMARY KEY, etag TEXT, size INTEGER, state TEXT, unit TEXT, ts REAL)""")
        self.con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        self.con.commit()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            return self.con.execute(
                "SELECT etag,size,state FROM objects WHERE key=?", (key,)).fetchone()

    def seq(self):
        with self.lock:
            row = self.con.execute("SELECT v FROM meta WHERE k='seq'").fetchone()
            n = int(row[0]) + 1 if row else 1
            self.con.execute("INSERT OR REPLACE INTO meta VALUES('seq',?)", (str(n),))
            self.con.commit()
            return n

    def record_unit(self, rows):
        now = time.time()
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO objects VALUES(?,?,?,?,?,?)",
                [(k, e, s, st, u, now) for k, e, s, st, u in rows])
            self.con.commit()

    def snapshot_to(self, path):
        tgt = sqlite3.connect(path)
        with self.lock:
            self.con.backup(tgt)
        tgt.close()


# ---------- 水位/背压 ----------
def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class Gates:
    def __init__(self, spool, budget_bytes, strict_status):
        self.spool = spool
        self.budget = budget_bytes
        self.strict = strict_status
        self.q_bytes = 0
        self.cond = threading.Condition()
        self._status_ts = 0.0
        self._status = None

    def disk_ok(self):
        try:
            du = shutil.disk_usage(self.spool)
        except OSError:
            return True
        if du.free < DISK_FLOOR:
            return False
        return dir_size(self.spool) < self.budget

    def gz_ok(self):
        """cn1 心跳检查(缓存 60s)。missing 时默认放行, --strict-status 改为拦。"""
        now = time.time()
        if now - self._status_ts >= 60:
            st, _, body = cos_call(DST_HOST, "GET", f"{DST_ROOT}/_status/cn1.json",
                                   timeout=(10, 30))
            if st == 200:
                try:
                    self._status = json.loads(body)
                except ValueError:
                    self._status = None
            elif st == 404:
                self._status = "missing"
            else:
                self._status = None  # 读不到: 不拦(自身网络抖动不该停推)
            self._status_ts = now
        s = self._status
        if s == "missing":
            return not self.strict
        if not isinstance(s, dict):
            return True
        return (now - float(s.get("ts", 0)) < STATUS_STALE_S
                and float(s.get("backlog_bytes", 0)) < BACKLOG_MAX)

    def wait_gz(self):
        while not self.gz_ok() and not _stop.is_set():
            time.sleep(30)

    def acquire_queue(self, nbytes):
        with self.cond:
            while self.q_bytes > QUEUE_BYTES_MAX and not _stop.is_set():
                self.cond.wait(5)
            self.q_bytes += nbytes

    def release_queue(self, nbytes):
        with self.cond:
            self.q_bytes -= nbytes
            self.cond.notify_all()


# ---------- 上传单元 ----------
def put_unit(dst_key, local_path, md5):
    """简单 PUT + ETag 校验(≤5GiB, ETag=内容 md5)。"""
    with open(local_path, "rb") as f:
        st, h, body = cos_call(DST_HOST, "PUT", dst_key, data=f)
    if st != 200:
        log(f"PUT {dst_key} 失败 {st} {body[:120]!r}")
        return False
    return (h.get("ETag") or "").strip('"').lower() == md5


def put_unit_multipart(dst_key, local_path):
    """分片 PUT(>4GiB)。ETag 非 md5(坑#2), 完成后 HEAD 对 size 核验。"""
    st, _, body = cos_call(DST_HOST, "POST", dst_key, params={"uploads": ""})
    if st != 200:
        log(f"initiate {dst_key} 失败 {st}")
        return False
    m = re.search(r"<UploadId>([^<]+)</UploadId>", body.decode("utf-8", "replace"))
    if not m:
        return False
    uid, parts, no = m.group(1), [], 1
    with open(local_path, "rb") as f:
        while True:
            chunk = f.read(PART_SIZE)
            if not chunk:
                break
            for attempt in range(3):
                st, h, _ = cos_call(DST_HOST, "PUT", dst_key,
                                    params={"partNumber": str(no), "uploadId": uid}, data=chunk)
                if st == 200:
                    parts.append(f"<Part><PartNumber>{no}</PartNumber>"
                                 f"<ETag>{h.get('ETag')}</ETag></Part>")
                    break
                time.sleep(2 * (attempt + 1))
            else:
                cos_call(DST_HOST, "DELETE", dst_key, params={"uploadId": uid})
                return False
            no += 1
    xml = f"<CompleteMultipartUpload>{''.join(parts)}</CompleteMultipartUpload>"
    st, _, _ = cos_call(DST_HOST, "POST", dst_key, params={"uploadId": uid}, data=xml.encode())
    if st != 200:
        log(f"complete {dst_key} 失败 {st}")
        return False
    st, h, _ = cos_call(DST_HOST, "HEAD", dst_key)
    return st == 200 and int(h.get("Content-Length", -1)) == os.path.getsize(local_path)


def push_sidecar(unit_rel, src_key, size, md5, etag):
    meta = {"key": src_key, "size": size, "md5": md5, "etag": etag,
            "unit": unit_rel, "pushed_at": time.time()}
    st, _, _ = cos_call(DST_HOST, "PUT", f"{DST_ROOT}/_meta/{unit_rel}.json",
                        data=json.dumps(meta).encode())
    return st == 200


def rel_of(src_key):
    return src_key[len(SRC_ROOT) + 1:]


def shard_of(src_key, m):
    if src_key.startswith(SRC_ROOT + "/" + BLOBS_MARK):
        sha2 = src_key[len(SRC_ROOT) + 1 + len(BLOBS_MARK):].split("/", 1)[0]
        try:
            return int(sha2, 16) % m
        except ValueError:
            pass
    return int(hashlib.md5(src_key.encode()).hexdigest(), 16) % m


# ---------- tar 写入器(单 packer 线程独占) ----------
class TarWriter:
    def __init__(self, relay):
        self.r = relay
        self.tar = None
        self.path = self.base = None
        self.manifest = []
        self.size = 0
        self.last_add = 0.0

    def _open(self):
        date = time.strftime("%Y%m%d")
        seq = self.r.ledger.seq()
        base = os.path.join(self.r.spool, "blobs", date, f"{self.r.host_tag}-part-{seq:06d}")
        os.makedirs(os.path.dirname(base), exist_ok=True)
        self.base = base
        self.path = base + ".tar.tmp"
        self.tar = tarfile.open(self.path, "w")
        self.manifest = []
        self.size = 0

    def add(self, key, etag, size, buf):
        if self.tar is None:
            self._open()
        rel = rel_of(key)
        name = rel[len(BLOBS_MARK):] if rel.startswith(BLOBS_MARK) else rel
        md5 = hashlib.md5(buf).hexdigest()
        ti = tarfile.TarInfo(name)
        ti.size = len(buf)
        self.tar.addfile(ti, io.BytesIO(buf))
        self.manifest.append(json.dumps(
            {"key": key, "md5": md5, "size": len(buf), "etag": etag}))
        self.size += 1024 + len(buf)
        self.last_add = time.time()
        if self.size >= TAR_CAP:
            self.seal()

    def maybe_linger_seal(self, idle_s):
        if self.tar and self.manifest and time.time() - self.last_add > idle_s:
            self.seal()

    def seal(self):
        if self.tar is None:
            return
        self.tar.close()
        tar_path = self.base + ".tar"
        os.rename(self.path, tar_path)
        man_path = self.base + ".manifest.jsonl"
        with open(man_path, "w") as f:
            f.write("\n".join(self.manifest) + "\n")
        date_dir = os.path.basename(os.path.dirname(tar_path))
        fname = os.path.basename(tar_path)
        unit_rel = f"datasets/demiwtg/kb/blobs/{date_dir}/{fname}"
        self.r.upload_tar_unit(tar_path, man_path, unit_rel, self.manifest)
        self.tar = None


# ---------- 管线主体 ----------
class Relay:
    def __init__(self, args):
        self.args = args
        self.spool = args.spool
        self.host_tag = os.uname().nodename.split(".")[0]
        self.ledger = Ledger(self.spool)
        self.gates = Gates(self.spool, args.budget_gb * 1024 ** 3, args.strict_status)
        self.writer = TarWriter(self)
        self.blob_q, self.file_q = [], []
        self.qlock = threading.Condition()
        self.fails = {}
        self.stats = {"scanned": 0, "skip_pushed": 0, "skip_gate": 0, "skip_shard": 0,
                      "skip_dead": 0, "todo": 0, "tar_units": 0, "file_units": 0, "fail": 0}
        self.units_done = 0

    # ---- 扫描 ----
    def scan_forever(self):
        while not _stop.is_set():
            for sub in self.args.subtrees:
                if _stop.is_set():
                    break
                self.scan_subtree(f"{SRC_ROOT}/{sub}")
            if self.args.dry_run or self.args.once:
                break
            time.sleep(self.args.sweep_gap)

    def scan_subtree(self, prefix):
        marker, pages = "", 0
        log(f"扫描 {prefix} ...")
        while not _stop.is_set():
            try:
                items, trunc, nm = cos_list_page(SRC_HOST, prefix, marker)
            except RuntimeError as e:
                log(f"list 错误重试: {e}")
                time.sleep(10)
                continue
            for key, size, etag, lm in items:
                self.stats["scanned"] += 1
                self.handle_key(key, size, etag, lm)
            pages += 1
            if pages % 200 == 0:
                log(f"  {prefix} 已翻 {pages} 页, 待处理 {self.gates.q_bytes / 1e9:.1f}GB {self.stats}")
            if not trunc:
                break
            marker = nm
        log(f"扫描完 {prefix}: {self.stats}")

    def handle_key(self, key, size, etag, lm):
        if shard_of(key, self.args.shard_m) != self.args.shard_n:
            self.stats["skip_shard"] += 1
            return
        if (self.args.blobs_t0 and key.startswith(SRC_ROOT + "/" + BLOBS_MARK)
                and lm[:10] < self.args.blobs_t0):
            self.stats["skip_gate"] += 1
            return
        row = self.ledger.get(key)
        if row:
            if row[2] == "dead":
                self.stats["skip_dead"] += 1
                return
            if row[0] == etag and row[1] == size:
                self.stats["skip_pushed"] += 1
                return
        if self.args.dry_run:
            self.stats["todo"] += 1
            return
        is_blob = key.startswith(SRC_ROOT + "/" + BLOBS_MARK) and size <= MEMBER_CAP
        self.gates.acquire_queue(size)
        with self.qlock:
            (self.blob_q if is_blob else self.file_q).append((key, size, etag, lm))
            self.qlock.notify_all()

    # ---- 打包(blob 单线程持 tar) ----
    def packer(self):
        while not _stop.is_set():
            item = None
            with self.qlock:
                if self.blob_q:
                    item = self.blob_q.pop(0)
                else:
                    self.qlock.wait(10)
                    self.writer.maybe_linger_seal(self.args.linger_min * 60)
            if item:
                self.pack_member(item)
        # 停机: 不取新成员(未记账下轮重收), 只封存当前卷并上传
        if self.writer.tar is not None and len(self.writer.manifest) >= 10:
            self.writer.seal()
        elif self.writer.tar is not None:  # 微量尾卷不值得传, 弃
            try:
                self.writer.tar.close()
            except Exception:
                pass
            try:
                os.remove(self.writer.path)
            except OSError:
                pass
            self.writer.tar = None

    def pack_member(self, item):
        key, size, etag, lm = item
        try:
            while not self.gates.disk_ok() and not _stop.is_set():
                time.sleep(30)
            buf = self.fetch_blob(key, size)
        finally:
            self.gates.release_queue(size)
        if buf is None:
            self.fails[key] = self.fails.get(key, 0) + 1
            self.stats["fail"] += 1
            if self.fails[key] >= 5:
                self.ledger.record_unit([(key, etag, size, "dead", "-")])
                dead_letter(self.spool, "member", key)
            return
        self.writer.add(key, etag, size, buf)
        self.stats["todo"] += 1

    def fetch_blob(self, key, size):
        for attempt in range(3):
            st, h, r = cos_call(SRC_HOST, "GET", key, stream=True)
            if st == 200:
                buf, got = io.BytesIO(), 0
                try:
                    for chunk in r.iter_content(1 << 20):
                        buf.write(chunk)
                        got += len(chunk)
                except requests.RequestException:
                    r.close()
                    time.sleep(2 * (attempt + 1))
                    continue
                r.close()
                if got == size:
                    return buf.getvalue()
                if got > MEMBER_CAP:
                    return None
            time.sleep(2 * (attempt + 1))
        return None

    # ---- standalone 文件 ----
    def file_worker(self):
        while not _stop.is_set():
            item = None
            with self.qlock:
                if self.file_q:
                    item = self.file_q.pop(0)
                else:
                    self.qlock.wait(10)
            if item:
                log(f"推送文件 {item[0]} ({item[1]}B)")
                self.push_standalone(*item)  # 单元一旦开始必做完(无中途 stop 检查)

    def push_standalone(self, key, size, etag, lm, spool_bin=None):
        try:
            while not self.gates.disk_ok() and not _stop.is_set():
                time.sleep(30)
            rel = rel_of(key)
            if spool_bin is None:
                h = hashlib.md5(key.encode()).hexdigest()[:16]
                spool_bin = os.path.join(self.spool, "files", f"{h}.bin")
                jobp = spool_bin[:-4] + ".job.json"
                os.makedirs(os.path.dirname(spool_bin), exist_ok=True)
                with open(jobp, "w") as f:
                    json.dump({"key": key, "size": size, "etag": etag}, f)
                if not self.download_file(key, size, spool_bin):
                    self.stats["fail"] += 1
                    dead_letter(self.spool, "file", key)
                    return
            md5 = md5_stream(spool_bin)
            dst = f"{DST_ROOT}/{rel}"
            for attempt in range(3):
                self.gates.wait_gz()
                ok = (put_unit(dst, spool_bin, md5) if size <= MULTIPART_TH
                      else put_unit_multipart(dst, spool_bin))
                if ok and push_sidecar(rel, key, size, md5, etag):
                    self.ledger.record_unit([(key, etag, size, "pushed", "-")])
                    for p in (spool_bin, spool_bin[:-4] + ".job.json"):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                    self.stats["file_units"] += 1
                    self.unit_done()
                    return
                time.sleep(30 * (attempt + 1))
            dead_letter(self.spool, "unit", {"key": key, "spool": spool_bin})
        finally:
            self.gates.release_queue(size)

    def download_file(self, key, size, dest):
        for attempt in range(3):
            st, h, r = cos_call(SRC_HOST, "GET", key, stream=True)
            if st == 200:
                got = 0
                try:
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            got += len(chunk)
                except (requests.RequestException, OSError):
                    r.close()
                    time.sleep(5 * (attempt + 1))
                    continue
                r.close()
                if got == size:
                    return True
            time.sleep(5 * (attempt + 1))
        return False

    # ---- tar 单元上传 ----
    def upload_tar_unit(self, tar_path, man_path, unit_rel, manifest_lines):
        members = [json.loads(x) for x in manifest_lines]
        md5 = md5_stream(tar_path)
        man_rel = unit_rel[:-4] + ".manifest.jsonl"
        for attempt in range(3):
            self.gates.wait_gz()
            if not put_unit(f"{DST_ROOT}/{unit_rel}", tar_path, md5):
                time.sleep(30 * (attempt + 1))
                continue
            st, _, _ = cos_call(DST_HOST, "PUT", f"{DST_ROOT}/{man_rel}",
                                data=open(man_path, "rb").read())
            if st != 200:
                time.sleep(30 * (attempt + 1))
                continue
            if not push_sidecar(unit_rel, unit_rel, os.path.getsize(tar_path), md5, ""):
                time.sleep(30 * (attempt + 1))
                continue
            self.ledger.record_unit(
                [(x["key"], x.get("etag", ""), x["size"], "pushed",
                  os.path.basename(tar_path)) for x in members])
            for p in (tar_path, man_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
            self.stats["tar_units"] += 1
            self.stats["todo"] += len(members)
            self.unit_done()
            return
        dead_letter(self.spool, "unit", {"tar": tar_path, "manifest": man_path})

    def unit_done(self):
        self.units_done += 1
        if self.args.max_units and self.units_done >= self.args.max_units:
            log(f"已达 --max-units={self.args.max_units}, 收线")
            _stop.set()

    # ---- 账本快照 ----
    def snapshot_loop(self):
        while not _stop.wait(LEDGER_SNAP_S):
            try:
                tmp = os.path.join(self.spool, "ledger_snap.db")
                gz = tmp + ".gz"
                self.ledger.snapshot_to(tmp)
                with open(tmp, "rb") as fi, gzip.open(gz, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                os.remove(tmp)
                with open(gz, "rb") as f:
                    st, _, _ = cos_call(DST_HOST, "PUT",
                                        f"{DST_ROOT}/_ledger/{self.host_tag}/ledger.db.gz",
                                        data=f.read())
                os.remove(gz)
                if st == 200:
                    log("账本快照已上传")
            except Exception as e:
                log(f"快照失败(下轮再试): {e}")

    # ---- 崩溃恢复 ----
    def recover(self):
        blobs_dir = os.path.join(self.spool, "blobs")
        if os.path.isdir(blobs_dir):
            # 先成对收集再处理: 逐文件迭代会因 manifest 字母序在前被误判孤儿、连带删掉已封 tar
            tars, mans = {}, set()
            for root, _, files in os.walk(blobs_dir):
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if f.endswith(".tar.tmp"):
                        os.remove(p)
                    elif f.endswith(".tar"):
                        tars[p] = True
                    elif f.endswith(".manifest.jsonl"):
                        mans.add(p[:-len(".manifest.jsonl")])
            for p in sorted(tars):
                base = p[:-4]
                man = base + ".manifest.jsonl"
                if base not in mans:
                    os.remove(p)  # 真孤儿 tar(无 manifest)
                    continue
                lines = [x for x in open(man).read().splitlines() if x]
                date_dir = os.path.basename(os.path.dirname(p))
                unit_rel = f"datasets/demiwtg/kb/blobs/{date_dir}/{os.path.basename(p)}"
                log(f"恢复重推 {unit_rel}")
                self.upload_tar_unit(p, man, unit_rel, lines)
            for base in mans:
                if base + ".tar" not in tars:
                    try:
                        os.remove(base + ".manifest.jsonl")
                    except OSError:
                        pass
        files_dir = os.path.join(self.spool, "files")
        if os.path.isdir(files_dir):
            for f in sorted(os.listdir(files_dir)):
                if f.endswith(".job.json"):
                    jobp = os.path.join(files_dir, f)
                    try:
                        j = json.load(open(jobp))
                    except ValueError:
                        os.remove(jobp)
                        continue
                    spool_bin = jobp[:-9] + ".bin"
                    if os.path.exists(spool_bin):
                        log(f"恢复重推 {j['key']}")
                        self.gates.acquire_queue(j["size"])
                        self.push_standalone(j["key"], j["size"], j.get("etag", ""),
                                             "", spool_bin=spool_bin)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1", help="N/M")
    ap.add_argument("--spool", default=os.path.expanduser("~/pan123-relay/spool"))
    ap.add_argument("--budget-gb", type=int, default=35)
    ap.add_argument("--subtrees", nargs="+", default=DEFAULT_SUBTREES)
    ap.add_argument("--blobs-t0", default="2026-09-17",
                    help="kb/blobs 时间闸门(毒过滤); 0=关(存量基线阶段)")
    ap.add_argument("--linger-min", type=int, default=10)
    ap.add_argument("--sweep-gap", type=int, default=60)
    ap.add_argument("--max-units", type=int, default=0, help="推满 N 个单元后退出(试点)")
    ap.add_argument("--once", action="store_true", help="扫一轮退出")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict-status", action="store_true",
                    help="cn1 心跳缺失时拦推(默认放行)")
    args = ap.parse_args()
    n, m = args.shard.split("/")
    args.shard_n, args.shard_m = int(n), int(m)
    if args.blobs_t0 in ("0", ""):
        args.blobs_t0 = ""

    r = Relay(args)
    if args.dry_run:
        r.scan_forever()
        log(f"DRY-RUN {r.stats}")
        return

    r.recover()
    workers = [threading.Thread(target=r.packer, daemon=True),
               threading.Thread(target=r.file_worker, daemon=True),
               threading.Thread(target=r.file_worker, daemon=True),
               threading.Thread(target=r.snapshot_loop, daemon=True)]
    for w in workers:
        w.start()
    try:
        r.scan_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        for w in workers:
            w.join(timeout=600)  # 尾卷封存+上传可能要 ~5-8 分钟
        try:
            tmp = os.path.join(args.spool, "ledger_snap.db")
            gz = tmp + ".gz"
            r.ledger.snapshot_to(tmp)
            with open(tmp, "rb") as fi, gzip.open(gz, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.remove(tmp)
            with open(gz, "rb") as f:
                cos_call(DST_HOST, "PUT", f"{DST_ROOT}/_ledger/{r.host_tag}/ledger.db.gz",
                         data=f.read())
            os.remove(gz)
        except Exception as e:
            log(f"退出快照失败: {e}")
        log(f"退出 {r.stats} units_done={r.units_done}")


if __name__ == "__main__":
    main()
