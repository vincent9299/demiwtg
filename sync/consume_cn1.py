#!/usr/bin/env python3
"""cn1 消费节点（业务薄入口）：GZ 队列 → 下载核 md5 → 123pan 镜像 →
读回抽样 → 守卫 DELETE → 账本 + 心跳。

部署：``~/pan123-relay/``（``.cos_creds``、``creds.json``/``token.json``
123pan 凭证）。运行：``python3 consume_cn1.py``（cos-internal 内网免费）。
单文件 >123pan 上限 → 死信 ``dead_oversize``（分卷方案由人拍板，勿机器重试）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                    # import config
sys.path.insert(0, os.path.dirname(_HERE))   # import demiflow (部署根)
import config as C

from demiflow.collect.cosio import COSCreds, COSIO
from demiflow.collect.pan123 import (Pan123Client, Pan123Error,
                                     Pan123FileTooLarge, Pan123SlowPath,
                                     install_upload_ip_filter)
from demiflow.collect.relay import (ConsumeLedger, DeadLetter, Heartbeat,
                                    Watchdog, md5_stream)


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _log(msg):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


class ConsumeNode:
    def __init__(self, args):
        self.args = args
        self.spool = args.spool
        creds = COSCreds.discover((os.path.join(self.spool, ".cos_creds"),))
        host = os.environ.get("GZ_INTERNAL_HOST", C.DST_INTERNAL_HOST)
        self.gz = COSIO(creds, host)
        self.pan = Pan123Client(
            os.path.join(os.path.dirname(self.spool), "creds.json"),
            os.path.join(os.path.dirname(self.spool), "token.json"))
        self.ledger = ConsumeLedger(self.spool)
        self.dead = DeadLetter(self.spool)
        self.heartbeat = Heartbeat(
            lambda key, d: self.gz.put_bytes(
                key, json.dumps(d).encode()), C.STATUS_KEY)
        self.root_id = None
        self.dir_cache: dict[str, int] = {}
        self.dlock = threading.Lock()
        self._warm_ts: dict[str, float] = {}

    # ---- 123pan 目录（逐级幂等建造, 带缓存） ----

    def _ensure_root(self):
        if self.root_id is None:
            self.root_id = self.pan.dir_id(0, C.PAN_ROOT)
        return self.root_id

    def _dir_id_for(self, rel_dir: str) -> int:
        with self.dlock:                     # 并发 worker 下目录建造互斥
            if not rel_dir:
                return self._ensure_root()
            if rel_dir in self.dir_cache:
                return self.dir_cache[rel_dir]
            parent = self._ensure_root()
            for seg in rel_dir.split("/"):
                parent = self.pan.dir_id(parent, seg)
            self.dir_cache[rel_dir] = parent
            return parent

    # ---- 队列 ----

    def list_units(self):
        out = []
        for key, size, _, _ in self.gz.list_entries(C.QUEUE_ROOT + "/"):
            seg = key[len(C.QUEUE_ROOT) + 1:].split("/", 1)[0]
            if seg.startswith("_"):        # _meta/_status/_ledger 保留区
                continue
            out.append((key, size))
        return out

    def _sidecar(self, unit_rel):
        body = self.gz.get_bytes(f"{C.META_PREFIX}{unit_rel}.json")
        try:
            return json.loads(body) if body else None
        except ValueError:
            return None

    # ---- 消费一个单元 ----

    def consume(self, key, size):
        rel = key[len(C.QUEUE_ROOT) + 1:]
        row = self.ledger.get(rel)
        bin_ = os.path.join(self.spool,
                            hashlib.md5(rel.encode()).hexdigest()[:16] + ".bin")
        if row and row[0] == "done":
            # 同 md5=崩溃于 DELETE 前的真重复(补删即收); md5 变=同路径新版本(重消费删旧传新)
            meta = self._sidecar(rel)
            if row[1] and meta and meta.get("md5") == row[1]:
                self._finish(key, rel, bin_)
                return
            if meta is not None:
                _log(f"  同路径新版本(边车 md5 变): {rel} → 删旧传新")
        for attempt in range(5):
            try:
                wd = Watchdog(C.WATCHDOG_UNIT_S)

                def _feed(rate, _wd=wd):
                    # 进度喂狗: 片速率达慢速阈值的稳态慢传续期死线(自然传完);
                    # 掉到阈值下停喂 → 30min 内仍被击穿换路(保慢路径自适应性)。
                    if rate >= self.pan.min_slice_rate:
                        _wd.feed()

                with wd:
                    if not self.gz.download_to(key, bin_):
                        raise RuntimeError("download 失败")
                    md5 = md5_stream(bin_)
                    meta = self._sidecar(rel)
                    if meta and meta.get("md5") and meta["md5"] != md5:
                        raise RuntimeError(f"md5 与边书不符 {md5} vs {meta['md5']}")
                    self._mirror(rel, bin_, size, md5, _feed)
                self.ledger.set(rel, size, md5, None, "done")
                self._finish(key, rel, bin_)
                return
            except Pan123SlowPath as e:
                _log(f"  慢窗口({e}), 单元留队 5 分钟后再战")
                _rm(bin_)                     # 慢窗口期 bin 不滞留(重试重下载), 防 17×4.3G 憋爆小盘
                time.sleep(300)               # 让路: 不烧死信额度, 不磨看门狗
                return                        # 本轮放弃, 下轮队列重见
            except Pan123FileTooLarge as e:
                _log(f"  超平台单文件上限: {rel} ({e})")
                self.ledger.set(rel, size, None, None, "dead_oversize")
                self.dead.add("oversize", {"key": key, "size": size})
                self._finish(key, rel, bin_)   # 出队; 源在 SG, 分卷另议
                return
            except Exception as e:
                _log(f"  失败[attempt{attempt + 1}] {rel}: {e}")
                time.sleep(30 * (attempt + 1))
        # 瞬态故障(超时/慢窗/网络)不永久死信: 留队下轮再战, 防镜像留洞+队列积尸;
        # 确定性失败(超限 Pan123FileTooLarge)已在上文单独 dead_oversize 处理。
        _log(f"  {rel} 5 次未过(瞬态), 留队下轮再战")
        _rm(bin_)
        return

    def _warmup(self, parent_id):
        """冷启动暖场: 每个目录的首传被服务端压到 ~0.1MB/s, 随后恢复 ~10MB/s
        (2026-09-21 实测, 按目标目录隔离)。大文件前在同目录传 256KB 微件焐热
        (冷速率 ~2s), trash 即撤。"""
        p = os.path.join(self.spool, "warmup.bin")
        with open(p, "wb") as f:
            f.write(b"w" * (256 * 1024))
        try:
            t0 = time.time()
            r = self.pan.upload_file(
                p, parent_id,
                f"_warmup_{int(time.time())}_{random.randrange(1 << 24):06x}.bin")
            self.pan.trash(r["fileID"])
            _log(f"  暖场(目录{parent_id}) {time.time() - t0:.1f}s")
        except Exception as e:
            _log(f"  暖场失败(忽略): {e}")
        finally:
            _rm(p)

    def _mirror(self, rel, bin_, size, md5, on_progress=None):
        rel_path = C.pan_relpath(rel)
        dirname, name = rel_path.rsplit("/", 1) if "/" in rel_path else ("", rel_path)
        parent = self._dir_id_for(dirname)
        warm_key = dirname
        if (size >= C.MEMBER_CAP_MB << 20
                and time.time() - self._warm_ts.get(warm_key, 0) > 1800):
            self._warmup(parent)                 # 每目录每 30 分钟焐一次
            self._warm_ts[warm_key] = time.time()
        if size > C.PAN_MAX_FILE:
            raise Pan123FileTooLarge(f"{name} {size}B 超镜像单文件上限")
        try:
            r = self.pan.upload_file(bin_, parent, name,
                                     on_progress=on_progress)
        except Pan123Error as e:
            if "code=1" not in str(e):
                raise                          # 非同名冲突: 交上层重试/死信
            old_id = self.pan.find_by_name(parent, name)
            if old_id is not None:
                _log(f"  同名冲突: trash 旧 fileID={old_id} 后重传 {name}")
            self.pan.trash(old_id)
            r = self.pan.upload_file(bin_, parent, name,
                                     on_progress=on_progress)
        fid = r["fileID"]
        if random.random() * 100 < self.args.readback_pct:
            url = self.pan.get_url(fid)
            _log(f"  读回抽样命中 {name} url 就绪")
        _log(f"消费 {rel} ({size / 1e6:.1f}MB md5={md5[:8]} pan={fid}"
             f"{' 秒传' if r['reuse'] else ''})")

    def _finish(self, key, rel, bin_):
        # DELETE 守卫：只删队列前缀下的对象（配置口径，硬校验）
        assert key.startswith(C.QUEUE_ROOT + "/"), key
        self.gz.delete(key)
        mkey = f"{C.META_PREFIX}{rel}.json"
        self.gz.delete(mkey)
        _rm(bin_)

    # ---- 主循环 ----

    def run(self):
        if C.PAN123_BAD_IPS:
            install_upload_ip_filter(frozenset(C.PAN123_BAD_IPS))
            _log(f"坏IP过滤已装: {sorted(C.PAN123_BAD_IPS)}")
        _log(f"启动 pan_root={C.PAN_ROOT} 读回抽样={self.args.readback_pct}%"
             f" workers={self.args.workers}")
        while True:
            units = self.list_units()
            backlog = sum(s for _, s in units)
            self.heartbeat.emit(backlog)
            todo = [u for u in units
                    if not (self.ledger.get(u[0][len(C.QUEUE_ROOT) + 1:])
                            or ("", ""))[0].startswith("dead")]
            todo.sort(key=lambda u: u[1])   # 小单元优先: 慢窗口下轻件先过, 不被大 tar 卡队
            if todo:
                _log(f"队列 {len(units)} 单元 / {backlog / 1e9:.2f}GB, "
                     f"本轮处理 {len(todo)}")
            last_hb = time.time()
            if self.args.workers > 1:
                with ThreadPoolExecutor(self.args.workers) as ex:
                    futs = [ex.submit(self.consume, k, z) for k, z in todo]
                    for f in as_completed(futs):
                        f.result()
                        if time.time() - last_hb > 300:
                            self.heartbeat.emit(backlog)
                            last_hb = time.time()
            else:
                for key, size in todo:
                    self.consume(key, size)
                    if time.time() - last_hb > 300:
                        self.heartbeat.emit(backlog)
                        last_hb = time.time()
                if self.args.max_units and self.units_n() >= self.args.max_units:
                    return
            if self.args.once:
                return
            time.sleep(self.args.poll_gap)

    _done = 0

    def units_n(self):
        return self._done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spool", default=os.path.expanduser("~/pan123-relay/spool"))
    ap.add_argument("--readback-pct", type=int, default=C.READBACK_PCT)
    ap.add_argument("--poll-gap", type=int, default=30)
    ap.add_argument("--workers", type=int, default=2,
                    help="并发上传单元数(QPS 提升后建议 2-3)")
    ap.add_argument("--max-units", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ConsumeNode(ap.parse_args()).run()


if __name__ == "__main__":
    main()
