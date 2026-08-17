"""流式采集常驻进程：缺口驱动任务生成 → 流式检索 → DownloadQueue → 流式下载。

与批处理 run 的关系：两阶段批处理（--shard 多进程分片）原样保留；stream 是
另一条常驻入口，复用同一检索层（pipeline.search_job）、下载层
（downloader.download_and_store）、下载队列（DownloadQueue）与主清单 upsert
（pipeline._update_master_manifest），行为契约完全一致。

三级流水线：
    任务生成器（心跳）── 从标签体系派生 jobs，现场聚合 images.jsonl 已有图数，
        未达标实例进任务队列；任务队列与下载队列双排空后休眠重扫（topup，wave+1）
    检索 workers ── search_job 多别名早停检索 → filterer 过滤 → enqueue
        （背压：下载队列在途过高时暂停检索）
    下载 workers ── claim → download_and_store → 成功进缓冲并投递打标队列
        （curation.annotate_vlm，队列归属消费方）；缓冲定期 flush 进
        images.jsonl + 健康账本
    监督循环 ── 定期 auto.govern（证据驱动生命周期迁移，退休源自动出检索面）；
        discover 链不进本进程（LLM 成本环节保持独立 orchestrate）

用法：
    python3 collect/cli.py stream --taxonomy data/taxonomy/instances.json
    python3 collect/cli.py stream --jobs 初音未来 --dl-workers 4
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from collections import defaultdict
import json

# 命名冲突防护：以 `python3 collect/cli.py` 方式运行时 sys.path[0] 是 collect/
# 目录，`import queue` 会解析到 collect/queue.py 而非 stdlib。导入期间临时摘除
# 该遮蔽路径（并清理可能已污染的缓存），取到 stdlib queue 后立即恢复。
_here = os.path.dirname(os.path.abspath(__file__))
_shadow = [p for p in sys.path if p and os.path.abspath(p) == _here]
for _p in _shadow:
    sys.path.remove(_p)
if "queue" in sys.modules and getattr(sys.modules["queue"], "__file__", "") and \
        os.path.abspath(sys.modules["queue"].__file__) == os.path.join(_here, "queue.py"):
    del sys.modules["queue"]
try:
    import queue as stdqueue
finally:
    sys.path[:] = _shadow + sys.path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from collect import downloader, filterer, models
    from collect import pipeline
    from collect.config import DEFAULTS, EffectiveConfig, load_taxonomy
    from collect.queue import DownloadQueue, is_congestion_fail
    from collect.registry import load_registry
    from collect.util import RateLimiter
    from collect.auto import govern as _govern
else:
    from . import downloader, filterer, models
    from . import pipeline
    from .config import DEFAULTS, EffectiveConfig, load_taxonomy
    from .queue import DownloadQueue, is_congestion_fail
    from .registry import load_registry
    from .util import RateLimiter
    from .auto import govern as _govern

# 打标队列归属消费方模块（curation）；collect 只是生产者。httpx/PIL 缺失时
# 其 import 会 SystemExit——捕获后置 None，不阻塞采集（回填机制兜底）。
try:
    if __package__ in (None, ""):
        from curation.annotate_vlm import enqueue_annotate
    else:
        from curation.annotate_vlm import enqueue_annotate
except (ImportError, SystemExit):
    enqueue_annotate = None

BACKPRESSURE_HI = 2000    # 下载队列在途（pending+claimed）超此值暂停检索
BACKPRESSURE_LO = 500     # 回落到此值以下恢复检索
FLUSH_EVERY = 50          # 成功缓冲满 N 条 flush 一次主清单
FLUSH_SEC = 60            # 或每 N 秒 flush 一次（取先到者）
GOVERN_SEC = 1800         # 监督循环：每 30 分钟跑一次 govern
RESCAN_SEC = 300          # 双排空后休眠重扫间隔（topup 语义）


def _repo_root(meta_dir: str) -> str:
    """AGENTS.md 约定：仓库根由 --meta 向上三级推导（与 cli/pipeline 一致）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(os.path.normpath(meta_dir)))))


def _existing_counts(meta_dir: str) -> dict:
    """从 images.jsonl 现场聚合各实例已有图数（不建派生索引）。"""
    counts = defaultdict(int)
    mpath = os.path.join(meta_dir, "images.jsonl")
    if not os.path.exists(mpath):
        return counts
    with open(mpath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for t in rec.get("instances") or []:
                counts[t] += 1
    return counts


def _inflight(q: DownloadQueue) -> int:
    n = 0
    for stats in q.counts().values():
        n += stats.get("pending", 0) + stats.get("claimed", 0)
    return n


def stream(meta_dir: str, taxonomy_path: str, jobs_substr: list,
           search_workers: int = 2, dl_workers: int = 8,
           queue_id: str = "stream") -> None:
    repo_root = _repo_root(meta_dir)
    images_dir = os.path.join(repo_root, "data", "dataset", "blobs")
    state_dir = os.path.join(repo_root, "state", "collect")
    os.makedirs(meta_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    q = DownloadQueue(os.path.join(state_dir, f".dlq_{queue_id}.sqlite3"))
    run_id = "stream-%s" % time.strftime("%Y%m%d-%H%M%S")

    # ---------- 源注册表（L2）+ 检索/下载共用设施 ----------
    holder = {"reg": load_registry(meta_dir)}   # govern 后重载，检索面实时收缩
    eff0 = EffectiveConfig.resolve(dict(DEFAULTS), None)
    min_images = getattr(eff0, "min_images_per_instance", None) or 4
    known_dead = list(getattr(eff0, "known_dead_sources", None) or [])
    dead = set(known_dead)
    rate_interval = getattr(eff0, "per_host_min_interval_sec", 1.0) or 1.0
    rate_limiter = RateLimiter(rate_interval)

    def get_adapter(name: str):
        return holder["reg"].get_adapter(name)

    stop = threading.Event()

    def _on_signal(signum, _frame):
        print(f"[stream] 收到信号 {signum}，停止取件并收尾…", flush=True)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # ---------- 共享计数/缓冲（锁保护） ----------
    health_lock = threading.Lock()
    src_health: dict = defaultdict(lambda: defaultdict(int))

    def _health(source: str, key: str, n: int = 1) -> None:
        with health_lock:
            src_health[source][key] += n

    buf_lock = threading.Lock()
    success_buf: list = []
    job_by_instance: dict = {}

    def _flush(force: bool = False) -> None:
        """成功缓冲 → images.jsonl（meta_lock 内 upsert）；健康计数 → 账本。"""
        with buf_lock:
            if not force and len(success_buf) < FLUSH_EVERY:
                batch = []
            else:
                batch = success_buf[:]
                success_buf.clear()
        with health_lock:
            health_snap = {s: dict(c) for s, c in src_health.items() if c}
            for s in health_snap:
                src_health.pop(s, None)
        if batch:
            pipeline._update_master_manifest(meta_dir, batch, run_id)
            print(f"[stream] flush {len(batch)} 张进 images.jsonl", flush=True)
        if health_snap:
            pipeline._merge_health(meta_dir, health_snap)

    def _annotate_enqueue(d: "models.Candidate") -> None:
        """下载成功即投递打标队列（同 sha256 去重；投递失败仅告警不阻塞）。"""
        if enqueue_annotate is None:
            return
        try:
            enqueue_annotate(meta_dir, d.sha256, [d.instance] if d.instance else [],
                             pipeline._rel_path(d.local_path))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 打标队列投递失败（回填兜底）: {e}", flush=True)

    # ---------- 任务生成器（心跳线程，topup 语义） ----------
    task_q: "stdqueue.Queue" = stdqueue.Queue()

    def _generator() -> None:
        wave = 0
        while not stop.is_set():
            wave += 1
            jobs, _ = load_taxonomy(taxonomy_path)
            jobs = [j for j in jobs
                    if not jobs_substr or any(t in j.instance for t in jobs_substr)]
            job_by_instance.update({j.instance: j for j in jobs})
            existing = _existing_counts(meta_dir)
            tasks = [j for j in jobs if existing.get(j.instance, 0) < min_images]
            for j in tasks:
                task_q.put((j, existing.get(j.instance, 0), wave))
            print(f"[stream] 轮次 {wave}: 实例 {len(jobs)}，未达标 {len(tasks)} "
                  f"进任务队列", flush=True)
            if not tasks:
                if stop.wait(RESCAN_SEC):
                    return
                continue
            # 双排空后休眠重扫（期间新下载的图会在下轮聚合中体现）
            while not stop.is_set():
                if task_q.empty() and q.drained():
                    break
                time.sleep(5.0)
            if stop.wait(RESCAN_SEC):
                return

    # ---------- 检索 workers ----------
    def _search_worker() -> None:
        paused = False
        while not stop.is_set():
            try:
                job, existing, wave = task_q.get(timeout=2.0)
            except stdqueue.Empty:
                continue
            # 背压：下载队列在途过高时暂停检索，回落后恢复
            while not stop.is_set() and (_inflight(q) > BACKPRESSURE_HI or paused):
                paused = _inflight(q) > BACKPRESSURE_LO
                if stop.wait(2.0):
                    return
            if stop.is_set():
                return
            local_health: dict = defaultdict(lambda: defaultdict(int))
            cands = pipeline.search_job(get_adapter, holder["reg"], job, dead,
                                        local_health)
            with health_lock:          # += 非原子，合并进共享账本需持锁
                for s, c in local_health.items():
                    for k, v in c.items():
                        src_health[s][k] += v
            cap = job.effective.max_per_source
            inst_min = max(1, min_images - existing)
            kept = 0
            for c in cands:
                if not c.content_url:
                    continue
                try:
                    adapter = get_adapter(c.source)
                except KeyError:
                    continue
                if c.source_authorized:
                    ok, _ = filterer.filter_candidate(
                        c, job.effective, adapter.allowed_suffixes)
                else:
                    ok, _ = filterer.filter_candidate_unauthorized(
                        c, job.effective, None)
                if not ok:
                    continue
                q.enqueue(wave, job.instance, c.source, c.content_url,
                          c.source_rank or 0, cap, inst_min, c.to_dict())
                kept += 1
                if cap and cap > 0 and kept >= cap:
                    break

    # ---------- 下载 workers（与批处理 _process_queue_item 同规则） ----------
    def _process_item(item: dict) -> None:
        instance = item["instance"]
        job = job_by_instance.get(instance)
        rec = q.reuse_rec(item["content_url"])
        if rec:
            # 同 URL 已下载成功：复用记录、补本标签关联（不重抓、不重打标）
            cand = pipeline._rec_to_candidate(rec, instance, images_dir)
            with buf_lock:
                success_buf.append(cand)
            q.mark_done(item["id"], rec)
            return
        c = models.Candidate.from_dict(item["payload"])
        c.instance = instance
        cfg = job.effective if job else eff0
        try:
            adapter = get_adapter(c.source)
        except KeyError:
            q.mark_skipped(item["id"])      # 源已退休/消失：弃件
            return
        allowed = None if not c.source_authorized else adapter.allowed_suffixes
        ok_dl, downloaded = downloader.download_and_store(
            c, cfg, allowed, images_dir, rate_limiter,
            headers=getattr(adapter, "download_headers", None))
        if ok_dl and downloaded:
            d = downloaded[0]
            with buf_lock:
                success_buf.append(d)
                n_buf = len(success_buf)
            _health(c.source, "dl_ok")
            _annotate_enqueue(d)
            q.mark_done(item["id"], pipeline._candidate_to_rec(d, images_dir))
            q.bump_cap(c.source, up=True)   # AIMD：成功 +1 试探加并发
            if n_buf >= FLUSH_EVERY:
                _flush(force=True)
        else:
            if c.status == models.STATUS_GATE_REJECTED:
                q.mark_skipped(item["id"])  # 分辨率门拒绝：重试无意义
            elif getattr(c, "fail_kind", None) in pipeline.DETERMINISTIC_FAIL:
                _health(c.source, "dl_dead")
                q.mark_skipped(item["id"])
            else:
                fk = getattr(c, "fail_kind", None)
                _health(c.source, "dl_timeout" if fk == "timeout" else "dl_fail")
                if is_congestion_fail(fk):
                    q.bump_cap(c.source, up=False)   # AIMD：拥堵减半降温
                q.release(item["id"])       # <3 次回 pending 退避重试；>=3 跳过

    def _dl_worker() -> None:
        while not stop.is_set():
            item = q.claim(0.0)
            if item is None:
                if stop.wait(1.0):
                    return
                continue
            try:
                _process_item(item)
            except Exception as e:  # noqa: BLE001 - worker 不因单件异常退出
                print(f"[warn] 下载件 {item.get('id')} 处理异常: {e}", flush=True)
                q.release(item["id"])

    # ---------- 定时 flush + 监督循环 ----------
    def _flusher() -> None:
        last_govern = time.time()
        while not stop.is_set():
            if stop.wait(FLUSH_SEC):
                return
            _flush(force=True)
            if time.time() - last_govern >= GOVERN_SEC:
                last_govern = time.time()
                try:
                    _govern.govern(meta_dir)
                    holder["reg"] = load_registry(meta_dir)
                    print("[stream] govern 完成，注册表已重载", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] govern 失败（不影响采集）: {e}", flush=True)

    # ---------- 启动 ----------
    threads = [threading.Thread(target=_generator, name="gen", daemon=True),
               threading.Thread(target=_flusher, name="flusher", daemon=True)]
    threads += [threading.Thread(target=_search_worker, name=f"search-{i}",
                                 daemon=True) for i in range(max(1, search_workers))]
    threads += [threading.Thread(target=_dl_worker, name=f"dl-{i}",
                                 daemon=True) for i in range(max(1, dl_workers))]
    print(f"[stream] 启动：检索 {max(1, search_workers)} / 下载 {max(1, dl_workers)} "
          f"workers，队列 {q.db_path}，run_id {run_id}", flush=True)
    if enqueue_annotate is None:
        print("[warn] 打标队列不可用（curation.annotate_vlm 依赖缺失），"
              "下载产物由存量回填兜底", flush=True)
    for t in threads:
        t.start()

    while not stop.is_set():
        time.sleep(1.0)

    # ---------- 优雅停机：停止取件 → flush → 合并账本 ----------
    stop.set()
    for t in threads:
        t.join(timeout=30)
    _flush(force=True)
    q.close()
    print("[stream] 已停机（缓冲与账本均已落盘）", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="流式采集常驻进程（缺口驱动检索 → DownloadQueue → 流式下载）")
    ap.add_argument("--taxonomy", default="data/taxonomy/instances.json",
                    help="标签体系实例元文件（默认 data/taxonomy/instances.json）")
    ap.add_argument("--meta", default="data/dataset/meta",
                    help="元数据根目录（默认 data/dataset/meta）")
    ap.add_argument("--jobs", default="",
                    help="实例名子串过滤（逗号分隔，试点用）")
    ap.add_argument("--search-workers", type=int, default=2, help="检索线程数（默认 2）")
    ap.add_argument("--dl-workers", type=int, default=8, help="下载线程数（默认 8）")
    ap.add_argument("--queue-id", default="stream",
                    help="下载队列 ID（默认 stream；state/collect/.dlq_<id>.sqlite3）")
    args = ap.parse_args(argv)
    jobs_substr = [t for t in args.jobs.split(",") if t]
    stream(args.meta, args.taxonomy, jobs_substr,
           search_workers=args.search_workers, dl_workers=args.dl_workers,
           queue_id=args.queue_id)


if __name__ == "__main__":
    main()
