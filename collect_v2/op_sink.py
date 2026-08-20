"""collect_v2 落盘算子：输入 Item（已下载+已标注）→ blob 内容寻址落盘 + 主清单追加。

契约（.qoder/handoff_collect_v2.md §4.3 + 2026-08-20 拍板）：
- sha256 撞车（索引内已存在）→ **直接跳过**：不写 blob、不追加行、不合并 instances；
- 无标注的图照写：标注四字段键存在、值为 null（区分「未标注」与「打分 0」）；
- 写入保护：多 worker 并发必须安全 → fcntl 跨进程锁 + asyncio 进程内锁；
- 字段集为最小兼容集：对齐存量读端已识别字段，V2 无信息源的旧概念字段不写
  （tiers/source_rank/source_score/source_kind/source_authorized/credit）；
  query_langs 随 op_seed 的 lang 字段补写（2026-08-20 拍板：{实例:zh/latin}）；
- 声明尺寸以 orig_width/orig_height 承载（存量字段名，常失真，实测值在 width/height）；
- blob 临时文件放目标目录同盘（os.replace 要求同文件系统；系统盘容量不足的坑在案）；
- 主清单追加写：崩溃最多留一行坏行，读端本就容忍 JSONDecodeError。

跨进程去重设计（探针实证后定稿）：
- 各 worker 进程各持一份内存 sha 索引（load_index 时从主清单全量构建），仅做快路径；
- 权威判定在 fcntl 锁内：先吸收「本进程上次扫描之后其他进程新追加的行」的全部 sha
  进索引（锁内文件状态已定格，推进偏移安全），再判撞车；
- 反例记录：曾因「miss 也推进偏移但不吸收区间内其他 sha」导致共享图双写，
  修复为吸收式推进（见 _absorb_tail）。
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
from typing import Optional, Set

from collect_v2.op_search import Item


class Sink:
    """落盘端：blobs 内容寻址写盘 + images.jsonl 追加。

    多 worker 并发安全（用户拍板）：
    - 进程间：fcntl.flock 排他锁（meta_dir/.meta.lock，§5 白名单唯一允许）；
    - 进程内：asyncio.Lock（文件 I/O 丢线程池，锁覆盖索引检查+写盘+追加全程）。
    """

    def __init__(self, dataset_dir: str):
        """dataset_dir = data/dataset（blobs/ 与 meta/ 在其下）。"""
        self.dataset_dir = dataset_dir
        self.meta_dir = os.path.join(dataset_dir, "meta")
        self.blobs_dir = os.path.join(dataset_dir, "blobs")
        self.manifest = os.path.join(self.meta_dir, "images.jsonl")
        self._lock_path = os.path.join(self.meta_dir, ".meta.lock")  # §5 meta 白名单唯一允许
        self._alock = asyncio.Lock()
        self._known: Optional[Set[str]] = None   # 本进程 sha 索引（load_index 构建）
        self._scan_end = 0    # 索引已覆盖到的清单字节偏移，锁内吸收其后增量
        os.makedirs(self.meta_dir, exist_ok=True)
        os.makedirs(self.blobs_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 去重索引
    # ------------------------------------------------------------------

    def load_index(self) -> int:
        """全量扫主清单建本进程 sha 索引（断点续传的去重依据），返回索引条数。

        443MB/31 万行量级现场扫描可接受；坏行跳过（存量本就存在半行/脏行）。
        索引各进程一份不同步，跨进程新行靠锁内 _absorb_tail 增量吸收。
        """
        known: Set[str] = set()
        if os.path.exists(self.manifest):
            with open(self.manifest, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sha = rec.get("sha256")
                    if sha:
                        known.add(sha)
            self._scan_end = os.path.getsize(self.manifest)
        self._known = known
        return len(known)

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    async def sink(self, item: Item) -> bool:
        """写盘一条已下载 Item。返回 True=落盘；False=跳过。

        跳过条件（用户拍板直接跳过，不合并）：
        - sha 已在索引（存量、本进程已落、或其他进程已落——锁内吸收后可见）；
        - 无字节/无 sha（上游未下载成功，不该进到 sink，防御性跳过）。
        """
        if item.data is None or not item.sha256:
            return False
        if self._known is None:
            self.load_index()   # 防御：未显式 load 也可用
        async with self._alock:
            if item.sha256 in self._known:   # 快路径：本进程索引命中
                return False
            path = await asyncio.to_thread(self._write_disk, item)
            if path is None:                 # 权威判定：锁内吸收后命中，跨进程撞车
                return False
        item.local_path = path
        item.fetched_at = time.time()
        return True

    def _write_disk(self, item: Item) -> Optional[str]:
        """blob 原子写 + 清单行追加（全程持 fcntl 跨进程锁）。

        返回 None = 权威判定撞车：先吸收其他进程新追加的行，再判索引命中。
        """
        shard = item.sha256[:2]
        shard_dir = os.path.join(self.blobs_dir, shard)
        os.makedirs(shard_dir, exist_ok=True)
        final_path = os.path.join(shard_dir, f"{item.sha256}.{item.ext or 'bin'}")
        rel_path = f"blobs/{shard}/{os.path.basename(final_path)}"

        with open(self._lock_path, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                self._absorb_tail()
                if item.sha256 in self._known:
                    return None
                if not os.path.exists(final_path):
                    # 临时文件与目标同目录（同文件系统，os.replace 原子替换；
                    # 绝不放系统盘：旧系统临时目录放错盘导致大盘操作失败的坑在案）
                    tmp = final_path + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(item.data)
                    os.replace(tmp, final_path)
                rec = self._record_for(item, rel_path)
                with open(self.manifest, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._known.add(item.sha256)
                self._scan_end = os.path.getsize(self.manifest)   # 推进含本行
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        return rel_path

    def _absorb_tail(self) -> None:
        """锁内吸收增量尾部：索引偏移之后其他进程新追加的行，全部 sha 进索引。

        吸收式推进是竞态正解（探针实证）：只查特定 sha 且 miss 也推进偏移，
        会把区间内其他进程的新 sha 标记为「已扫」而永久漏检，导致撞车双写。
        锁内文件状态已定格，推进偏移安全；增量区间通常只有几行几 KB。
        容忍坏行；尾部首行可能是残行，解析失败即跳过。
        """
        try:
            size = os.path.getsize(self.manifest)
        except FileNotFoundError:
            return
        if size <= self._scan_end:
            return
        with open(self.manifest, "rb") as f:
            f.seek(self._scan_end)
            tail = f.read().decode("utf-8", errors="replace")
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sha = json.loads(line).get("sha256")
            except json.JSONDecodeError:
                continue
            if sha:
                self._known.add(sha)
        self._scan_end = size

    @staticmethod
    def _record_for(item: Item, rel_path: str) -> dict:
        """Item → 主清单记录（最小兼容集，标注无值写 null）。"""
        return {
            "sha256": item.sha256,
            "ext": item.ext,
            "source": item.source,
            "license": item.license or "",
            "author": item.author,
            "width": item.actual_width,
            "height": item.actual_height,
            "orig_width": item.declared_width,
            "orig_height": item.declared_height,
            "size_bytes": item.size_bytes,
            "mime": item.mime,
            "instances": [item.instance] if item.instance else [],
            "queries": ({item.instance: item.query}
                        if item.instance and item.query else {}),
            "query_langs": ({item.instance: item.lang}
                            if item.instance and item.lang else {}),
            "content_url": item.content_url,
            "landing_url": item.landing_url,
            "fetched_at": time.time(),
            "path": rel_path,
            # 标注四字段：VLM 失败放行时为 null（用户拍板写 null 不省键）
            "kb_match": item.kb_match,
            "richness": item.richness,
            "caption": item.caption,
            "identity": item.identity,
        }
