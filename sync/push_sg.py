#!/usr/bin/env python3
"""sg 端中继节点（业务薄入口）：扫描闭集 → 毒闸门/分片过滤 → 散图封卷 /
大文件分片 → 推 GZ 队列。

部署：``~/pan123-relay/``（creds: ``.cos_creds``，格式 sid:skey）。
运行：``python3 push_sg.py --shard 0/2``（supervisor 托管见 README）。
账本/队列格式与 2026-09-20 首夜部署版兼容——可直接接管现役 spool 续跑。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                    # import config
sys.path.insert(0, os.path.dirname(_HERE))   # import demiflow (部署根)
import config as C

from demiflow.collect.cosio import COSCreds, COSIO
from demiflow.collect.relay import (DeadLetter, Gates, PushLedger, SealSpec,
                                    TarPacker, Watchdog, md5_stream,
                                    recover_pairs)


def _log(msg):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


class PushNode:
    def __init__(self, args):
        self.args = args
        self.spool = args.spool
        self.host_tag = os.uname().nodename.split(".")[0]
        creds = COSCreds.discover((os.path.join(self.spool, ".cos_creds"),))
        self.src = COSIO(creds, C.SRC_HOST_FULL)
        self.dst = COSIO(creds, C.DST_HOST_FULL)
        self.ledger = PushLedger(self.spool)
        self.dead = DeadLetter(self.spool)
        self.gates = Gates(
            self.spool, C.BUDGET_GB * 1024 ** 3,
            status_reader=self._read_status,
            backlog_max=C.BACKLOG_MAX_GB * 1024 ** 3,
            stale_max=C.STALE_MAX_H * 3600,
            strict_status=args.strict_status)
        self.packer = TarPacker(
            self.spool, self.ledger, self.host_tag, C.BLOBS_UNIT_PREFIX,
            on_seal=self._push_tar_unit,
            spec=SealSpec(cap_bytes=C.TAR_CAP_GB * 1024 ** 3,
                          linger_s=C.LINGER_MIN * 60,
                          member_name=self._member_name))
        self.stats = {"scanned": 0, "skip": 0, "todo": 0, "tar_units": 0,
                      "file_units": 0, "fail": 0}
        self.units_done = 0
        self.fails: dict = {}

    # ---- 心跳读（供积压闸） ----

    def _read_status(self):
        body = self.dst.get_bytes(C.STATUS_KEY)
        if body is None:
            return "missing"
        try:
            return json.loads(body)
        except ValueError:
            return None

    @staticmethod
    def _member_name(src_key: str) -> str:
        rel = src_key[len(C.SRC_ROOT) + 1:]
        return rel[len(C.BLOBS_MARK):] if rel.startswith(C.BLOBS_MARK) else rel

    # ---- 边车 ----

    def _sidecar(self, unit_rel, key, size, md5, etag=""):
        self.dst.put_bytes(
            f"{C.META_PREFIX}{unit_rel}.json",
            json.dumps({"key": key, "size": size, "md5": md5, "etag": etag,
                        "unit": unit_rel,
                        "pushed_at": time.time()}).encode())

    # ---- 上传单元 ----

    def _push_tar_unit(self, tar_path, man_path, unit_rel, manifest_lines):
        members = [json.loads(x) for x in manifest_lines]
        md5 = md5_stream(tar_path)
        for attempt in range(3):
            self.gates.wait_open()
            wd = Watchdog(C.WATCHDOG_UNIT_S)

            def _feed(rate, _wd=wd):
                # 进度喂狗: 分片完成即续期死线(稳态慢推自然推完);
                # 零进展(发送阻塞盲区)无喂, 到期照杀换新连接。
                _wd.feed()

            with wd:
                ok = self.dst.put_smart(
                    f"{C.QUEUE_ROOT}/{unit_rel}", tar_path, md5,
                    multipart_th=C.MULTIPART_TH_MB << 20,
                    part_size=C.PART_SIZE_MB << 20, on_progress=_feed)
            if not ok:
                self.stats["fail"] += 1
                time.sleep(30 * (attempt + 1))
                continue
            man_key = f"{C.QUEUE_ROOT}/{unit_rel[:-4]}.manifest.jsonl"
            st, _, _ = self.dst.call("PUT", man_key,
                                     data=open(man_path, "rb").read())
            if st != 200:
                time.sleep(30 * (attempt + 1))
                continue
            self._sidecar(unit_rel, unit_rel, os.path.getsize(tar_path), md5)
            self.ledger.record_unit(
                [(x["key"], x.get("etag", ""), x["size"], "pushed",
                  os.path.basename(tar_path)) for x in members])
            for p in (tar_path, man_path):
                os.remove(p)
            self.units_done += 1
            self.stats["tar_units"] += 1
            self.stats["todo"] += len(members)
            return
        self.dead.add("unit", {"tar": tar_path, "manifest": man_path})

    def _push_file_unit(self, src_key, size, etag):
        unit_rel = src_key[len(C.SRC_ROOT) + 1:]
        qkey = f"{C.QUEUE_ROOT}/{unit_rel}"
        os.makedirs(os.path.join(self.spool, "files"), exist_ok=True)
        local = os.path.join(
            self.spool, "files",
            hashlib.md5(unit_rel.encode()).hexdigest()[:16] + ".bin")
        for attempt in range(3):
            self.gates.wait_open()
            wd = Watchdog(C.WATCHDOG_UNIT_S)
            feed = wd.feed          # 分片进度喂狗(与 _push_tar_unit 同理)
            with wd:
                if size <= C.MULTIPART_TH_MB << 20:
                    data = self.src.get_bytes(src_key)
                    if data is None:
                        time.sleep(30 * (attempt + 1))
                        continue
                    md5 = hashlib.md5(data).hexdigest()
                    st, hdrs, _ = self.dst.call("PUT", qkey, data=data)
                    ok = st == 200 and (hdrs.get("ETag") or "").strip('"') == md5
                else:                                  # 大文件: 落盘分片, 内存 O(part)
                    if not self.src.download_to(src_key, local):
                        time.sleep(30 * (attempt + 1))
                        continue
                    md5 = md5_stream(local)
                    ok = self.dst.put_smart(
                        qkey, local, md5,
                        multipart_th=C.MULTIPART_TH_MB << 20,
                        part_size=C.PART_SIZE_MB << 20, on_progress=feed)
            if not ok:
                self.stats["fail"] += 1
                time.sleep(30 * (attempt + 1))
                continue
            self._sidecar(unit_rel, src_key, size, md5, etag)
            self.ledger.record_unit([(src_key, etag, size, "pushed", unit_rel)])
            try:
                os.remove(local)
            except OSError:
                pass
            self.units_done += 1
            self.stats["file_units"] += 1
            self.stats["todo"] += 1
            return
        self.dead.add("file", {"key": src_key})

    # ---- 扫描 ----

    def scan_once(self):
        for sub in C.SUBTREES:
            if self.gates.stopped:
                return
            _log(f"扫描 {C.SRC_ROOT}/{sub} ...")
            for key, size, etag, lm in self.src.list_entries(
                    f"{C.SRC_ROOT}/{sub}"):
                if self.gates.stopped:
                    return
                self.stats["scanned"] += 1
                self.handle_key(key, size, etag, lm)
                if self.args.max_units and self.units_done >= self.args.max_units:
                    _log("已达 --max-units, 收线")
                    return
            self.gates.wait_open()
            self.packer.maybe_linger_seal()

    def handle_key(self, key, size, etag, lm):
        if C.shard_of(key) != self.args.shard_n:
            self.stats["skip"] += 1
            return
        row = self.ledger.get(key)
        if row:
            if row[2] == "dead":
                self.stats["skip"] += 1
                return
            if row[0] == etag and row[1] == size:   # 已推且未变(可变文件按 etag 刷新)
                self.stats["skip"] += 1
                return
        if not C.in_closed_set(key):
            self.stats["skip"] += 1
            return
        is_blob = (key.startswith(C.SRC_ROOT + "/" + C.BLOBS_MARK)
                   and size <= C.MEMBER_CAP_MB << 20)
        if key.startswith(C.SRC_ROOT + "/" + C.BLOBS_MARK) and not C.blobs_gate_ok(lm):
            self.stats["skip"] += 1
            return
        self.gates.wait_open()
        if self.args.dry_run:
            self.stats["todo"] += 1
            return
        if is_blob:
            self._pack_member(key, size, etag)
        else:
            self._push_file_unit(key, size, etag)

    def _pack_member(self, key, size, etag):
        """blob 成员：下载入内存→进卷；连续失败标死防每圈重查。"""
        buf = None
        for attempt in range(3):
            with Watchdog(C.WATCHDOG_UNIT_S):
                buf = self.src.get_bytes(key)
            if buf is not None:
                break
            time.sleep(2 * (attempt + 1))
        if buf is None:
            self.fails[key] = self.fails.get(key, 0) + 1
            self.stats["fail"] += 1
            if self.fails[key] >= C.BLOB_FAIL_DEAD:
                self.ledger.record_unit([(key, etag, size, "dead", "-")])
                self.dead.add("member", {"key": key})
            return
        self.packer.add(key, etag, buf)
        self.stats["todo"] += 1

    # ---- 恢复与主循环 ----

    def recover(self):
        blobs_dir = os.path.join(self.spool, "blobs")
        for tar_path, man_path in recover_pairs(blobs_dir):
            unit_rel = os.path.relpath(tar_path, blobs_dir).replace(os.sep, "/")
            _log(f"恢复重推 {unit_rel}")
            with open(man_path) as f:
                lines = f.read().splitlines()
            self._push_tar_unit(tar_path, man_path,
                                f"{C.BLOBS_UNIT_PREFIX}/{unit_rel}", lines)

    def _snapshot(self):
        try:
            snap = os.path.join(self.spool, "ledger.snapshot.db")
            self.ledger.snapshot_to(snap)
            st, _, _ = self.dst.call(
                "PUT",
                f"{C.QUEUE_ROOT}/_ledger/{self.host_tag}/snapshot.db",
                data=open(snap, "rb").read())
            _log(f"账本快照 st={st} {self.stats}")
        except Exception as e:
            _log(f"快照失败(下轮再试): {e}")

    def run(self):
        if not self.args.dry_run:
            self.recover()
        while not self.gates.stopped:
            self.scan_once()
            self._snapshot()
            if self.args.once or self.args.dry_run:
                break
            time.sleep(self.args.sweep_gap)
        if len(self.packer.manifest) >= C.TAIL_MIN_MEMBERS:
            self.packer.seal()                # 尾卷: 成员够才值得传
        elif self.packer.tar is not None:     # 微量弃(未记账, 下轮重收)
            self.packer.tar.close()
            try:
                os.remove(self.packer.path)
            except OSError:
                pass
            self.packer._reset()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1", help="N/M")
    ap.add_argument("--spool", default=os.path.expanduser("~/pan123-relay/spool"))
    ap.add_argument("--sweep-gap", type=int, default=60)
    ap.add_argument("--max-units", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict-status", action="store_true")
    args = ap.parse_args()
    n, m = args.shard.split("/", 1)
    C.SHARDS = int(m)
    args.shard_n = int(n)
    PushNode(args).run()


if __name__ == "__main__":
    main()
