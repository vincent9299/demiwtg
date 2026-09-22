#!/usr/bin/env python3
"""cn1 侧 123pan 消费 daemon：GZ 桶 pan123-relay/ 队列 → 123pan 镜像树。

循环：list 数据对象(跳过 _ 保留区) → 读边车 _meta/<rel>.json → cos-internal 下载(边下边 md5)
→ md5 对边车核验 → 上传 123pan(etag=md5, 秒传幂等; 同名冲突=trash 旧再传) → 抽样读回对 md5
→ DELETE GZ 数据+边车(前缀硬校验) → 记账 → 心跳 _status/cn1.json。

内存纪律(1G)：list 分页不聚合、单单元串行、sqlite 账本、下载流式落盘。
崩溃续跑：账本 done 的单元直接补 DELETE；123pan 上传幂等(秒传)。

用法(cn1, ~/pan123-relay/)：
  python3 cos123_relay.py                    # 常驻
  python3 cos123_relay.py --max-units 3      # 试点限量
  python3 cos123_relay.py --readback-pct 100 # 试点全读回
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pan123  # 同目录部署, 复用 http/upload_file/dir_id/trash/get_url

# ---------- COS(cos-internal 免费内网, 签名) ----------
HOST = "lhcos-cee54-1256345599.cos-internal.ap-guangzhou.myqcloud.com"
SRC_ROOT = "pan123-relay"
META_PREFIX = f"{SRC_ROOT}/_meta/"
STATUS_KEY = f"{SRC_ROOT}/_status/cn1.json"
LEDGER_KEY_PREFIX = f"{SRC_ROOT}/_ledger/cn1/"
DELETE_GUARD = ("pan123-relay/",)   # DELETE 前缀白名单(硬校验)

READBACK_MIN_BYTES = 1  # 读回抽样仅对 ≥ 此值单元(全开)

_local = threading.local()


def log(msg):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


def creds():
    for p in (os.path.expanduser("~/pan123-relay/.cos_creds"), "/tmp/cos_creds"):
        try:
            sid, skey = open(p).read().strip().split(":", 1)
            if sid and skey:
                return sid, skey
        except (OSError, ValueError):
            continue
    sys.exit("无 COS 凭据: ~/pan123-relay/.cos_creds 与 /tmp/cos_creds 均缺失")


import hmac
import urllib.parse

SID, SKEY = creds()


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


def cos_call(method, key, params=None, data=None, stream=False, timeout=(15, 600)):
    path = ("/" + key) if key else "/"
    q = "&".join(f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
                 for k, v in sorted((params or {}).items()))
    url = f"https://{HOST}{urllib.parse.quote(path)}" + (f"?{q}" if q else "")
    try:
        r = sess().request(method, url, data=data, stream=stream,
                           headers={"authorization": sig(method, path, params or {}, HOST)},
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


XML_BLOCK = re.compile(r"<Contents>(.*?)</Contents>", re.S)
XML_KEY = re.compile(r"<Key>([^<]+)</Key>")
XML_SIZE = re.compile(r"<Size>(\d+)</Size>")


def list_data_units():
    """分页列举数据单元(跳过 _ 保留区), 返回 [(key, size)]。"""
    out, marker = [], ""
    while True:
        params = {"prefix": f"{SRC_ROOT}/", "max-keys": "1000"}
        if marker:
            params["marker"] = marker
        st, _, body = cos_call("GET", "", params=params, timeout=(15, 120))
        if st != 200:
            raise RuntimeError(f"list 失败 {st}")
        t = body.decode("utf-8", "replace")
        for blk in XML_BLOCK.findall(t):
            k, s = XML_KEY.search(blk), XML_SIZE.search(blk)
            if not (k and s):
                continue
            key = k.group(1)
            seg = key[len(SRC_ROOT) + 1:].split("/", 1)[0]
            if seg.startswith("_"):  # _meta/_status/_ledger 保留区
                continue
            out.append((key, int(s.group(1))))
        if "<IsTruncated>true</IsTruncated>" not in t:
            return out
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", t)
        marker = nm.group(1) if nm else ""
        if not marker:
            return out


def cos_delete(key):
    """带前缀硬校验的 DELETE(两道保险之一)。"""
    if not key.startswith(DELETE_GUARD[0]):
        raise RuntimeError(f"拒绝删除非队列前缀对象: {key}")
    st, _, body = cos_call("DELETE", key)
    if st not in (200, 204, 404):
        raise RuntimeError(f"DELETE {key} 失败 {st} {body[:80]!r}")


def md5_stream(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- 账本 ----------
class Ledger:
    def __init__(self, path):
        self.con = sqlite3.connect(path, timeout=60, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("""CREATE TABLE IF NOT EXISTS units(
            rel TEXT PRIMARY KEY, size INTEGER, md5 TEXT, pan_file_id INTEGER,
            state TEXT, ts REAL)""")
        self.con.commit()
        self.lock = threading.Lock()

    def get(self, rel):
        with self.lock:
            return self.con.execute(
                "SELECT state, size, md5 FROM units WHERE rel=?", (rel,)).fetchone()

    def record(self, rel, size, md5, pan_fid, state):
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO units VALUES(?,?,?,?,?,?)",
                             (rel, size, md5, pan_fid, state, time.time()))
            self.con.commit()

    def counts(self):
        with self.lock:
            return self.con.execute("SELECT state, COUNT(*) FROM units GROUP BY state").fetchall()

    def snapshot_to(self, path):
        tgt = sqlite3.connect(path)
        with self.lock:
            self.con.backup(tgt)
        tgt.close()


# ---------- 123pan 目录树 ----------
class PanTree:
    def __init__(self, root_name):
        self.root_name = root_name
        self.cache = {}
        self.root_id = self._ensure_root()

    def _ensure_root(self):
        for x in pan123.list_dir(0):
            if x["type"] == 1 and x["filename"] == self.root_name:
                return x["fileId"]
        return pan123.mkdir(0, self.root_name)

    def ensure(self, dir_path):
        """dir_path 形如 datasets/demiwtg/kb/blobs/20260920, 返回 123pan 目录 ID。"""
        if dir_path in self.cache:
            return self.cache[dir_path]
        parts = dir_path.strip("/").split("/")
        cur = self.root_id
        for i, p in enumerate(parts):
            partial = "/".join(parts[:i + 1])
            if partial in self.cache:
                cur = self.cache[partial]
                continue
            cur = pan123.dir_id(cur, p)
            self.cache[partial] = cur
        return cur


def pan_upload_with_collision(path, dir_id, name):
    """上传; 同名(可变文件重推)则 trash 旧文件再传。返回 (fileID, reuse)。"""
    try:
        return pan123.upload_file(path, dir_id, name)
    except pan123.Pan123Error as e:
        if "重复" not in str(e):
            raise
    for x in pan123.list_dir(dir_id):
        if x["type"] == 0 and x["filename"] == name:
            log(f"  同名冲突, trash 旧 fileID={x['fileId']} 后重传")
            pan123.trash(x["fileId"])
            break
    return pan123.upload_file(path, dir_id, name)


def pan_readback(file_id, md5):
    """从 123pan 读回对 md5(两道保险之二, 含秒传路径)。"""
    url = pan123.get_url(file_id)
    m = hashlib.md5()
    with requests.get(url, stream=True, timeout=(15, 600)) as r:
        for chunk in r.iter_content(1 << 20):
            m.update(chunk)
    return m.hexdigest() == md5


# ---------- 主循环 ----------
class Daemon:
    def __init__(self, args):
        self.args = args
        self.spool = args.spool
        os.makedirs(self.spool, exist_ok=True)
        self.ledger = Ledger(os.path.join(self.spool, "ledger.db"))
        self.tree = PanTree(args.pan_root)
        self.fails = {}
        self.units_done = 0
        self._token_ok = True

    def pan_guard(self, fn, *a, **kw):
        """token 过期自动重登后重试一次。"""
        for i in range(2):
            try:
                return fn(*a, **kw)
            except pan123.Pan123Error as e:
                if i == 0 and ("过期" in str(e) or "不存在" in str(e)):
                    pan123.cmd_login()
                    continue
                raise

    def heartbeat(self, backlog_bytes):
        body = json.dumps({"ts": time.time(), "backlog_bytes": backlog_bytes,
                           "host": os.uname().nodename}).encode()
        cos_call("PUT", STATUS_KEY, data=body)

    def consume(self, key, size):
        rel = key[len(SRC_ROOT) + 1:]
        spool_bin = os.path.join(self.spool, hashlib.md5(rel.encode()).hexdigest()[:16] + ".bin")

        # 边车先行：md5/size 是消费单元的代标识（R3）
        meta = None
        st, _, body = cos_call("GET", f"{META_PREFIX}{rel}.json")
        if st == 200:
            try:
                meta = json.loads(body)
            except ValueError:
                pass

        # 已完成：仅当账本代 == 当前来源代才补删（R3：同路径重推的新版本
        # 不得被旧 done 记录直接删除，需按新一代完整重消费）。
        row = self.ledger.get(rel)
        if row and row[0] == "done":
            _, ledger_size, ledger_md5 = row
            gen_md5 = (meta or {}).get("md5")
            gen_size = (meta or {}).get("size")
            if gen_md5 is not None:
                same_generation = (ledger_md5 == gen_md5)
            elif gen_size is not None:
                same_generation = (ledger_size == gen_size)
            else:
                same_generation = (ledger_size == size)
            if same_generation:
                self.finish_delete(key, rel, spool_bin)
                return
            log(f"  同路径新版本, 旧 done 不适用: {rel} "
                f"(ledger md5={ledger_md5[:8] if ledger_md5 else '?'} "
                f"边车 md5={gen_md5[:8] if gen_md5 else '?'})")

        # 下载(流式落盘+md5)
        for attempt in range(3):
            st, h, r = cos_call("GET", key, stream=True)
            if st == 200:
                got, m = 0, hashlib.md5()
                try:
                    with open(spool_bin, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            m.update(chunk)
                            got += len(chunk)
                except (requests.RequestException, OSError):
                    r.close()
                    time.sleep(5 * (attempt + 1))
                    continue
                r.close()
                if got == size:
                    break
            time.sleep(5 * (attempt + 1))
        else:
            self.mark_fail(key, rel, "download")
            return
        md5 = m.hexdigest()

        # md5 对边车核验(SG→GZ 段完整性)
        if meta and meta.get("md5") and meta["md5"] != md5:
            log(f"  md5 与边书不符, 弃单元重等重推: {rel}")
            os.remove(spool_bin)
            self.mark_fail(key, rel, "md5_mismatch")
            return  # 不 DELETE, sg 侧不重推同 etag... 记 fail 人工介入

        # 上传 123pan(镜像目录)
        dir_rel, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
        dir_id = self.tree.ensure(dir_rel) if dir_rel else self.tree.root_id
        try:
            r2 = self.pan_guard(pan_upload_with_collision, spool_bin, dir_id, name)
        except pan123.Pan123Error as e:
            log(f"  123pan 上传失败: {e}")
            self.mark_fail(key, rel, "upload")
            return
        # 读回抽样
        if self.args.readback_pct >= 100 or (
                self.args.readback_pct > 0
                and int(hashlib.md5(rel.encode()).hexdigest(), 16) % 100
                < self.args.readback_pct):
            if not pan_readback(r2["fileID"], md5):
                log(f"  读回 md5 不符! {rel}")
                self.mark_fail(key, rel, "readback")
                return
        self.ledger.record(rel, size, md5, r2["fileID"], "done")
        self.finish_delete(key, rel, spool_bin)
        self.units_done += 1
        log(f"消费 {rel} ({size/1e6:.1f}MB md5={md5[:8]} pan={r2['fileID']} "
            f"{'秒传' if r2['reuse'] else ''})")

    def finish_delete(self, key, rel, spool_bin):
        cos_delete(key)
        cos_delete(f"{META_PREFIX}{rel}.json")
        try:
            os.remove(spool_bin)
        except OSError:
            pass

    def mark_fail(self, key, rel, why):
        self.fails[rel] = self.fails.get(rel, 0) + 1
        log(f"  失败[{why}] 第{self.fails[rel]}次: {rel}")
        if self.fails[rel] >= 5:
            self.ledger.record(rel, 0, "", None, f"dead:{why}")
            with open(os.path.join(self.spool, "dead_units.jsonl"), "a") as f:
                f.write(json.dumps({"rel": rel, "why": why, "ts": time.time()}) + "\n")
            log(f"  死信: {rel}")

    def run(self):
        log(f"启动 pan_root={self.args.pan_root} 读回抽样={self.args.readback_pct}%")
        while not _stop.is_set():
            try:
                units = list_data_units()
            except Exception as e:
                log(f"list 异常: {e}")
                time.sleep(30)
                continue
            backlog = sum(s for _, s in units)
            try:
                self.heartbeat(backlog)
            except Exception:
                pass
            if not units:
                if self.args.once:
                    break
                time.sleep(self.args.poll_gap)
                continue
            todo = []
            for u in units:
                row = self.ledger.get(u[0][len(SRC_ROOT) + 1:])
                if row and row[0].startswith("dead"):
                    continue
                todo.append(u)
            log(f"队列 {len(units)} 单元 / {backlog/1e9:.2f}GB, 本轮处理 {len(todo)}")
            last_hb = time.time()
            for key, size in todo:
                if _stop.is_set():
                    break
                self.consume(key, size)
                if time.time() - last_hb > 300:  # 长批次中刷新心跳, 防 sg 侧误判 stale
                    try:
                        self.heartbeat(backlog)
                        last_hb = time.time()
                    except Exception:
                        pass
                if self.args.max_units and self.units_done >= self.args.max_units:
                    log(f"已达 --max-units={self.args.max_units}")
                    _stop.set()
                    break
            if self.args.once:
                break
            self.snapshot()
        self.snapshot()

    def snapshot(self):
        try:
            tmp = os.path.join(self.spool, "ledger_snap.db")
            gz = tmp + ".gz"
            self.ledger.snapshot_to(tmp)
            with open(tmp, "rb") as fi, gzip.open(gz, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.remove(tmp)
            with open(gz, "rb") as f:
                st, _, _ = cos_call("PUT", f"{LEDGER_KEY_PREFIX}ledger.db.gz", data=f.read())
            os.remove(gz)
            log(f"账本快照上传 st={st} 单元统计 {self.ledger.counts()}")
        except Exception as e:
            log(f"快照失败(下轮再试): {e}")


_stop = threading.Event()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spool", default=os.path.expanduser("~/pan123-relay/spool"))
    ap.add_argument("--pan-root", default="demiwtg-data")
    ap.add_argument("--readback-pct", type=int, default=2)
    ap.add_argument("--poll-gap", type=int, default=30)
    ap.add_argument("--max-units", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    try:  # token 引导: 缺失/过期自动重登
        pan123.list_dir(0)
    except pan123.Pan123Error as e:
        if "过期" in str(e) or "不存在" in str(e):
            pan123.cmd_login()
        else:
            raise
    d = Daemon(args)
    try:
        d.run()
    except KeyboardInterrupt:
        _stop.set()
    finally:
        log(f"退出 units_done={d.units_done}")


if __name__ == "__main__":
    main()
