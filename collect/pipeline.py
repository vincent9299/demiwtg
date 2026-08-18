"""采集管线（docs 第 6 节两阶段 + 第 7 节产物）。

阶段一：逐 job 遍历所有启用来源（授权源 sources + 未授权源 unauthorized_sources，
        排除运行期动态剔除的死源），各源按自身语言（en/zh）取对应 query 检索 →
        统一 Candidate（带上游原生次序 source_rank / 原生分数 source_score）→
        写 candidates.jsonl。
阶段二：按 source 类型做【基础校验】（CC 源走许可证白名单；未授权源跳过许可证校验，
        仅做 URL/MIME/体积检查）→ 通过基础校验的候选下载原图（内容寻址去重，不改分辨率），
        并在 downloader 解码后用【实际分辨率门】(min_resolution) 拦截低分辨率原图（不落盘）。

本版增强（2026-08-13）：
- 增量消费模式（consume_mode）：delta 只采新实例；replay 全量重放（达标跳过）；
  replay-rules 两级身份匹配（全路径 + 父分支/叶子组合）跳过已消费实例、未达标 topup
  补采缺口（改大 min_images_per_instance 即触发补采）。
- 断点续传：启动加载本湖 images.jsonl → 构建 url_index(content_url→rec)；下载前若
  content_url 已在索引且 blob 仍在，直接复用、跳过网络抓取。
- labels 增量落盘：每下载成功 1 张即追写 images.jsonl（含 content_url）；
  实例名↔图 关系直接由 images.jsonl 的 instances 字段承载（不另建派生索引）。
- 采集溯源字段（2026-08-16）：queries/query_langs 按实例对齐、asset_ids 按来源对齐，
  记录实际检索词与来源内资产 ID；同 sha 合并时取映射并集。
- 太少动态扩源：某实例成功图 < min_images_per_instance 时，用 expansion_sources 补搜并用
  starved_max_per_source 放宽每源上限，直到达标或候选耗尽。
- 队列模式（--queue）：阶段二改为共享下载队列（SQLite 于 meta_dir），各分片进程投递
  候选后全体转 worker 取件下载：同一 URL 不并发重复下载、每候选最多 3 次重试后跳过、
  取件时按 (instance,source)/instance 封顶实时判定；死链/防盗链（401/403/404/410）确定性失败直接
  跳过不重试。下载与获取新候选【并行】：worker 下载的同时，各片每 10s 从健康源池
  （弱源除外）为未达标实例补搜一轮新候选投递队列，直到扩源耗尽。动态剔除死源机制
  已移除，改用来源健康账本（state/source_health.json，跨 run 累积）记录可用下载源。

每张候选的上游原生信号都会落库：source_rank / source_score。
单流存储：授权与未授权候选都下载到【同一个】images_dir，写入【同一个】downloads_success.jsonl。
数据湖布局：本批次过程产物写 state/collect/runs/<run_id>/（由 meta_dir 推导）；主清单 <meta_dir>/images.jsonl
（按 sha256 去重，跨批次累积，含 instances 字段）作为全局元数据唯一真相源；
state/collect/runs/_latest 软链指向本批次。
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional

from . import models
from .http_scope import BudgetExceeded
from .config import DEFAULTS, EffectiveConfig, Job, load_config
from . import filterer
from . import downloader
from .incremental import classify, split_instance, summarize
from .registry import load_registry
from .util import RateLimiter, meta_lock


def _is_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in (s or ""))

# 确定性失败（防盗链/死链）：重试无意义，队列模式直接跳过、不再 3 次重试。
DETERMINISTIC_FAIL = {"hotlink_forbidden", "dead_link"}
# 扩源补搜轮起始波次：90=复用存量候选，100+ = 动态补搜新候选（晚于基础轮 1 与历史波次）。
WAVE_LEFTOVERS = 90
WAVE_REFILL_BASE = 100
REFILL_SEC = 10          # 每轮补搜间隔（与下载并行）
REFILL_BATCH = 30        # 每轮补搜最多处理实例数（摊薄搜索负载）
REFILL_IDLE_LIMIT = 6    # 连续 N 轮零产出 → 扩源耗尽


def _state_dir(meta_dir: str) -> str:
    """collect 运行时状态目录：仓库根 state/collect/（不进数据湖）。"""
    state_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(meta_dir))),
        "state", "collect")
    os.makedirs(state_dir, exist_ok=True)
    return state_dir


def _health_path(meta_dir: str) -> str:
    return os.path.join(_state_dir(meta_dir), "source_health.json")


def _load_health(meta_dir: Optional[str]) -> dict:
    """加载来源健康账本（跨 run 累积的可用下载源记录）。"""
    if not meta_dir:
        return {}
    p = _health_path(meta_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_health(meta_dir: str, counters: dict) -> None:
    """把本 run 的来源健康计数增量合并进账本（meta_lock 串行，多分片安全）。"""
    if not meta_dir or not counters:
        return
    with meta_lock(meta_dir):
        p = _health_path(meta_dir)
        h = {}
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    h = json.load(f)
            except (json.JSONDecodeError, OSError):
                h = {}
        for s, c in counters.items():
            e = h.setdefault(s, {})
            for k, v in c.items():
                e[k] = e.get(k, 0) + v
            e["runs"] = e.get("runs", 0) + 1
            e["updated_at"] = time.time()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)


def _weak_sources_from_health(health: dict) -> set:
    """历史账本判定弱源：下载尝试 >=30 次、0 成功、确定性失败（死链/防盗链/超时）占比 >=60%。"""
    weak = set()
    for s, e in health.items():
        tried = e.get("dl_ok", 0) + e.get("dl_fail", 0) + e.get("dl_dead", 0)
        if tried >= 30 and e.get("dl_ok", 0) == 0:
            deterministic = e.get("dl_dead", 0) + e.get("dl_timeout", 0)
            if deterministic >= 0.6 * max(e.get("dl_fail", 1), 1):
                weak.add(s)
    return weak


# ---------------------------------------------------------------------------
# 检索层（模块级：批处理 run 与流式 stream 模式共用同一实现）
# ---------------------------------------------------------------------------
def active_sources(reg, job: Job, dead: set) -> List[str]:
    """本 job 可用来源：配置源表 ∩ 生命周期可用（active/probation）∩ 除死源种子。"""
    usable = reg.usable_names()
    return [s for s in (list(job.sources) + list(job.unauthorized_sources))
            if s not in dead and s in usable]


def search_source(get_adapter, s: str, job: Job,
                  src_health: dict) -> List["models.Candidate"]:
    """对单个来源做【多别名早停检索】：

    - 按来源语言选别名池（en 源用 en_aliases、zh 源用 zh_aliases、both 源两者交替）；
    - best-first：先搜最优别名，累计候选 >= target_count*alias_stop_factor 即停；
    - 跨别名按 content_url 去重；source_rank 按追加顺序重排（先搜的别名整体靠前）。
    """
    adapter = get_adapter(s)
    threshold = max(1, int((job.effective.target_count or 4)
                           * (getattr(job.effective, "alias_stop_factor", None) or 4)))
    if adapter.lang == "zh":
        pools = [list(job.zh_aliases or [job.zh_query])]
    elif adapter.lang == "both":
        pools = [list(job.en_aliases or [job.query]),
                 list(job.zh_aliases or [job.zh_query])]
    else:
        pools = [list(job.en_aliases or [job.query])]
    out: List["models.Candidate"] = []
    seen_urls: set = set()
    budget_hit = False
    for pool in pools:
        for alias in pool:
            sub = copy.copy(job)
            sub.query = alias
            sub.zh_query = alias if _is_cjk(alias) else job.zh_query
            try:
                raws = adapter.search(sub)
                src_health[s]["search_ok"] += 1
            except BudgetExceeded as e:
                # 预算早停：不计 search_fail（不污染健康账本失败率），
                # 本源本 run 不再继续检索（预算是源级硬约束）
                print(f"[warn] 标签 {job.instance}: 来源 {s} 预算耗尽，提前停止: {e}",
                      flush=True)
                budget_hit = True
                break
            except Exception as e:  # noqa: BLE001
                src_health[s]["search_fail"] += 1
                print(f"[warn] 标签 {job.instance}: 来源 {s} 别名 {alias!r} 检索失败: {e}")
                continue
            added = 0
            for raw in raws:
                try:
                    c = adapter.to_candidate(raw, sub)
                except Exception:  # noqa: BLE001
                    continue
                if c.content_url and c.content_url in seen_urls:
                    continue
                if c.content_url:
                    seen_urls.add(c.content_url)
                out.append(c)
                added += 1
            if added == 0 or len(out) >= threshold:
                break
        if budget_hit or len(out) >= threshold:
            break
    for idx, c in enumerate(out):
        if c.source_rank is None:
            c.source_rank = idx
    return out


def search_job(get_adapter, reg, job: Job, dead: set, src_health: dict,
               searched_per_source: dict = None) -> List["models.Candidate"]:
    """一个实例任务的全源检索（active_sources 逐源 search_source 汇总）。"""
    new = []
    for s in active_sources(reg, job, dead):
        out = search_source(get_adapter, s, job, src_health)
        if searched_per_source is not None:
            searched_per_source[s] += len(out)
        src_health[s]["candidates"] += len(out)
        new.extend(out)
    return new


def _job_index(jobs: List[Job]) -> Dict[str, Job]:
    return {j.instance: j for j in jobs}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _blob_path(rec: dict, images_dir: str) -> str:
    sha = rec.get("sha256", "")
    ext = rec.get("ext", "") or "jpg"
    return os.path.join(images_dir, sha[:2], f"{sha}.{ext}") if sha else ""


def _blob_exists(rec: dict, images_dir: str) -> bool:
    p = _blob_path(rec, images_dir)
    return bool(p) and os.path.exists(p)


# 续传索引紧凑条目：(sha256, ext, source, source_authorized)。
# 不驻留完整 rec 字典（278k 条完整记录曾实测 ~1.5GB/进程，32 分片直接顶爆容器限额）；
# 续传补关联行的其余字段留空，由块末去重合并从同 sha 完整行回填（None 不覆盖已有值）。
_LakeEntry = tuple


def _lake_candidate(entry: _LakeEntry, instance: str, content_url: str,
                    images_dir: str) -> "models.Candidate":
    """从紧凑续传索引条目重建复用 Candidate（仅携带合并/落盘所需最小字段）。

    local_path 按 (sha, ext) 直接重建 blob 路径（加载索引时已验 blob 存在），
    使 _candidate_to_rec 的 ext/path 字段投影正确。
    """
    sha, ext, source, auth = entry
    return models.Candidate(
        source=source,
        source_kind="",
        asset_id=sha,
        instance=instance,
        query=instance,
        landing_url="",
        content_url=content_url,
        source_authorized=auth,
        status=models.STATUS_DOWNLOADED,
        sha256=sha,
        local_path=os.path.join(images_dir, sha[:2], f"{sha}.{ext}") if sha else "",
    )


def _candidate_to_rec(c: "models.Candidate", images_dir: str) -> dict:
    """把成功 Candidate 投影成 images.jsonl 记录（含 content_url 以便续传）。"""
    sha = c.sha256 or ""
    ext = os.path.splitext(c.local_path or "")[1].lstrip(".") if c.local_path else ""
    tier = c.selected_tier if c.selected_tier is not None else 0
    queries = ({c.instance: c.query} if c.instance and c.query else {})
    query_langs = ({c.instance: c.query_lang} if c.instance and c.query_lang else {})
    asset_ids = ({c.source: c.asset_id} if c.source and c.asset_id else {})
    return {
        "sha256": sha,
        "ext": ext,
        "source": c.source,
        "source_kind": c.source_kind,
        "source_authorized": c.source_authorized,
        "license": c.license_raw or "",
        "author": c.author,
        "credit": c.credit,
        "width": c.actual_width,
        "height": c.actual_height,
        "orig_width": c.orig_width,
        "orig_height": c.orig_height,
        "size_bytes": c.actual_size,
        "mime": c.actual_mime,
        "instances": [c.instance] if c.instance else [],
        "tiers": [tier],
        "source_rank": c.source_rank,
        "source_score": c.source_score,
        "queries": queries,
        "query_langs": query_langs,
        "asset_ids": asset_ids,
        "landing_url": c.landing_url,
        "content_url": c.content_url,
        "fetched_at": c.fetched_at,
        "path": _rel_path(c.local_path),
    }


def _rec_to_candidate(rec: dict, instance: str, images_dir: str) -> "models.Candidate":
    """从 images.jsonl 记录重建一个 success Candidate（用于续传复用，避免重抓）。"""
    sha = rec.get("sha256", "")
    local = _blob_path(rec, images_dir)
    return models.Candidate(
        source=rec.get("source", ""),
        source_kind=rec.get("source_kind", ""),
        asset_id=sha,
        instance=instance or (rec.get("instances") or [""])[0],
        query=(rec.get("queries") or {}).get(instance or "") or instance or "",
        landing_url=rec.get("landing_url", ""),
        content_url=rec.get("content_url", ""),
        source_authorized=rec.get("source_authorized", True),
        license_raw=rec.get("license", ""),
        source_rank=rec.get("source_rank"),
        source_score=rec.get("source_score"),
        status=models.STATUS_DOWNLOADED,
        sha256=sha,
        local_path=local,
        actual_width=rec.get("width"),
        actual_height=rec.get("height"),
        actual_size=rec.get("size_bytes"),
        actual_mime=rec.get("mime"),
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(jobs: List[Job], out_dir: str, images_dir: str,
        meta_dir: Optional[str] = None, run_id: Optional[str] = None,
        metadata_only: bool = False,
        only_instances: Optional[set] = None,
        exact_instances: Optional[set] = None,
        consume_mode: str = "replay",
        taxonomy_name: Optional[str] = None,
        queue_mode: bool = False,
        n_shards: int = 1,
        shard_index: int = 1,
        queue_id: Optional[str] = None,
        ignore_dead_seed: bool = False,
        reuse_phase1: bool = False,
        queue_threads: int = 1) -> dict:

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    if meta_dir:
        os.makedirs(meta_dir, exist_ok=True)

    # 限速器间隔取配置（默认 1.0s）。分片并发时各片把 per_host_min_interval_sec 调大，
    # 使对同一 host 的【聚合】请求速率不超过单流，避免触发 429/封禁。
    _rate_interval = 1.0
    if jobs:
        _rate_interval = getattr(jobs[0].effective, "per_host_min_interval_sec", 1.0) or 1.0
    rate_limiter = RateLimiter(_rate_interval)

    # ---------- 源注册表（L2）：统一适配器加载 + 生命周期过滤 ----------
    # 手写模块 ∪ 手写 spec（collect/specs/）∪ 生成源（state/collect/）；
    # 仅 active/probation 可参与采集（retired/degraded 由覆盖层证据驱动）。
    reg = load_registry(meta_dir)

    def get_adapter(name: str):
        return reg.get_adapter(name)

    # --jobs 支持子串匹配，便于按实例名试点；--jobs-file 为精确匹配
    jobs = [j for j in jobs if (not only_instances or any(t in j.instance for t in only_instances))
            and (not exact_instances or j.instance in exact_instances)]

    # 运行期配置（带默认值，兼容旧配置无新键）；无 jobs 的分片（纯队列 worker）用内置默认。
    eff0 = (jobs[0].effective if jobs else
            EffectiveConfig.resolve(dict(DEFAULTS), None))
    min_images = getattr(eff0, "min_images_per_instance", None) or 4
    expansion_sources = list(getattr(eff0, "expansion_sources", None) or [])
    starved_cap = getattr(eff0, "starved_max_per_source", None)
    # 死源「种子」跳过（配置静态列表，非运行期剔除；--ignore-dead-seed 关闭）。
    # 动态剔除机制已移除：来源失败由候选级 3 次重试规则兜底，不再整源摘除。
    known_dead = list(getattr(eff0, "known_dead_sources", None) or [])
    dead = set() if ignore_dead_seed else set(known_dead)
    if dead:
        print(f"[config] 种子跳过来源 {sorted(dead)}（--ignore-dead-seed 可关闭）", flush=True)

    # 来源健康账本：跨 run 累积的可用下载源记录（state/source_health.json）。
    health = _load_health(meta_dir) if meta_dir else {}
    weak_from_health = _weak_sources_from_health(health)
    if weak_from_health:
        print(f"[health] 历史弱源（扩源将跳过）: {sorted(weak_from_health)}", flush=True)

    # ---------- 加载本湖已有状态：断点续传 + 增量实例 ----------
    # 紧凑索引（不驻留完整 rec 字典，避免大批次分片内存膨胀）：
    url_index: Dict[str, _LakeEntry] = {}   # content_url -> (sha, ext, source, authorized)，仅 blob 仍存在
    instance_map: Dict[str, set] = {}       # instance -> sha256 集合（达标计数/基数去重只需 sha）
    _persisted_keys: set = set()        # 本 run 已追写过的 (content_url, instance)，_persist 防重复行
    mpath = os.path.join(meta_dir, "images.jsonl") if meta_dir else None
    if mpath and os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 进程被杀时残留的半行，跳过
                sha = rec.get("sha256")
                if not sha:
                    continue
                # 续传索引：仅保留 blob 仍在磁盘的记录（只存 4 元组，不存完整 rec）
                if _blob_exists(rec, images_dir):
                    cu = rec.get("content_url")
                    if cu:
                        url_index[cu] = (sha, rec.get("ext", "") or "",
                                         rec.get("source", "") or "",
                                         rec.get("source_authorized", True))
                for t in rec.get("instances", []):
                    instance_map.setdefault(t, set()).add(sha)
    print(f"[state] 载入本湖已下载 {len(url_index)} 条（续传索引），实例 {len(instance_map)} 个",
          flush=True)

    # ---------- 增量消费分类（delta / replay / replay-rules，见 incremental.py） ----------
    # 实例标签即实例名（不含路径）；精确命中即跳过/补采，历史路径格式仍有 (父,叶) 兜底。
    total_jobs = len(jobs)
    jobs, existing_counts, classify_report = classify(
        jobs, instance_map, consume_mode, min_images)
    print(f"[consume] 模式={consume_mode} 输入 {total_jobs} 标签 -> 执行 {len(jobs)} "
          f"({summarize(classify_report)})", flush=True)
    job_by_instance = _job_index(jobs)

    # 计数器（跨整个 run）。success/failed/rejected/candidates 不再驻留内存列表：
    # 运行产物即时流式落盘（见下方 _emit_*），候选统计增量计入 cand_stat，
    # 长时大批次 RSS 不再随下载量增长（32 分片 reuse-phase1 曾实测 ~2GB/进程）。
    C = {
        # candidates 是阶段一/二的工作列表：投递入队（队列模式）或串行消费完即释放，
        # 不再长期驻留；success/failed/rejected 则完全不建列表（流式落盘）。
        "candidates": [],
        "n_success": 0, "n_failed": 0, "n_rejected": 0, "n_candidates": 0,
        "cc": 0, "unauth": 0, "bytes": 0, "capped": 0,
        "instance_success": defaultdict(int),
        "q_done": 0,
        "src_health": defaultdict(lambda: {
            "search_ok": 0, "search_fail": 0, "candidates": 0,
            "dl_ok": 0, "dl_fail": 0, "dl_dead": 0, "dl_timeout": 0,
        }),
    }
    # 每实例候选统计（候选数/中文检索词数）：增量构建，替代块末对驻留候选列表的遍历
    cand_stat: Dict[str, dict] = defaultdict(lambda: {"candidates": 0, "zh": 0})

    def _note_cand(c: "models.Candidate") -> None:
        st = cand_stat[c.instance]
        st["candidates"] += 1
        if c.query_lang == "zh":
            st["zh"] += 1
        C["n_candidates"] += 1
    # topup 基数：已有图数计入 instance_success，使 扩源触发/stop_at 的目标是
    # 「总数达到 min_images」而不是「本轮再下 min_images 张」（补采只补缺口）。
    for _instance, _n in existing_counts.items():
        C["instance_success"][_instance] = _n
    # 基数图的 sha 集合：续传分支若再命中这些图不得重复计数（否则 n→2n 误判达标）。
    # （实体名标签无路径，split_instance 兜底自然退化为名字匹配；保留以兼容旧格式标签。）
    baseline_shas: Dict[str, set] = {}
    if existing_counts:
        _fuzzy_shas: Dict[tuple, set] = defaultdict(set)
        for _t, _shas in instance_map.items():
            _fuzzy_shas[split_instance(_t)].update(_shas)
        baseline_shas = {
            _instance: set(instance_map.get(_instance, ())) | _fuzzy_shas[split_instance(_instance)]
            for _instance in existing_counts
        }
    searched_per_source = defaultdict(int)

    REFRESH_EVERY = 5  # 每 N 个实例打一次进度

    def _persist(c: "models.Candidate") -> None:
        """追写 images.jsonl（增量、崩溃安全）+ 更新内存 url_index / instance_map。

        续传命中/扩源重复命中同样追写一行：新实例关联必须崩溃安全地即时落地
        （重复 sha 行由块末 _update_master_manifest 统一折叠，与同图异 URL 的跨批命中
        同规则）；仅同 (content_url, instance) 在本 run 内重复命中时去重，避免重复行。
        """
        rec = _candidate_to_rec(c, images_dir)
        rec["content_url"] = c.content_url
        pkey = (c.content_url, c.instance)
        if mpath and pkey not in _persisted_keys:
            with meta_lock(meta_dir):
                with open(mpath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _persisted_keys.add(pkey)
        url_index[c.content_url] = (c.sha256 or "",
                                    os.path.splitext(c.local_path or "")[1].lstrip(".")
                                    if c.local_path else "",
                                    c.source or "", c.source_authorized)
        instance_map.setdefault(c.instance, set()).add(c.sha256 or "")

    def _active_sources(job: Job) -> List[str]:
        return active_sources(reg, job, dead)

    def _search_source(s: str, job: Job, alias_scope: str = "all") -> List["models.Candidate"]:
        return search_source(get_adapter, s, job, C["src_health"])

    def _search_job(job: Job) -> List["models.Candidate"]:
        return search_job(get_adapter, reg, job, dead, C["src_health"],
                          searched_per_source)

    def _process_groups(groups, cap, job, stop_at=None):
        """下载 groups（(instance,source)->[cands]），带 url_index 续传跳过 + max_per_source 封顶。
        返回新增 success 数。"""
        local_new = 0
        for (instance, src), cs in groups.items():
            if instance not in job_by_instance:
                continue
            cs.sort(key=lambda c: (c.source_rank if c.source_rank is not None else 0))
            succ = 0
            for c in cs:
                if cap and cap > 0 and succ >= cap:
                    C["capped"] += 1
                    continue
                # —— 断点续传：URL 已下载过则直接复用，跳过网络抓取 ——
                if c.content_url and c.content_url in url_index:
                    cand = _lake_candidate(url_index[c.content_url], instance,
                                           c.content_url, images_dir)
                    if cand.sha256 in baseline_shas.get(instance, ()):
                        # 已计入 topup 基数：只补标签关联，不重复计数
                        _persist(cand)
                        continue
                    _emit_success(cand)
                    C["instance_success"][instance] += 1
                    if cand.sha256 and cand.source_authorized:
                        C["cc"] += 1
                    elif cand.sha256:
                        C["unauth"] += 1
                    _persist(cand)
                    succ += 1
                    local_new += 1
                    continue
                # —— 正常下载 ——
                cfg = job.effective
                adapter = get_adapter(c.source)
                allowed = None if not c.source_authorized else adapter.allowed_suffixes
                ok_dl, downloaded = downloader.download_and_store(
                    c, cfg, allowed, images_dir, rate_limiter,
                    headers=getattr(adapter, "download_headers", None),
                )
                if ok_dl and downloaded:
                    d = downloaded[0]
                    if d.sha256 in baseline_shas.get(instance, ()):
                        # 与基数图同内容（不同 URL 重复命中）：不重复计数
                        _persist(d)
                        continue
                    _emit_success(d)
                    C["instance_success"][instance] += 1
                    C["bytes"] += d.actual_size or 0
                    if d.source_authorized:
                        C["cc"] += 1
                    else:
                        C["unauth"] += 1
                    C["src_health"][src]["dl_ok"] += 1
                    _persist(d)
                    succ += 1
                    local_new += 1
                else:
                    if c.status == models.STATUS_GATE_REJECTED:
                        _emit_rejected(c)
                    else:
                        _emit_failed(c)
                        fk = getattr(c, "fail_kind", None)
                        if fk in DETERMINISTIC_FAIL:
                            C["src_health"][src]["dl_dead"] += 1
                        elif fk == "timeout":
                            C["src_health"][src]["dl_timeout"] += 1
                        else:
                            C["src_health"][src]["dl_fail"] += 1
                if stop_at and C["instance_success"][instance] >= stop_at:
                    break
            if stop_at and C["instance_success"][instance] >= stop_at:
                break
        return local_new

    # ---------- 阶段一：多源检索 + 候选（增量落盘 candidates.jsonl） ----------
    cand_path = os.path.join(out_dir, "candidates.jsonl")
    total = len(jobs)
    if reuse_phase1 and os.path.exists(cand_path):
        # 调参重启：跳过检索，直接复用本 run 目录候选（省去重搜时间）
        loaded = []
        with open(cand_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    loaded.append(models.Candidate.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        for c in loaded:
            searched_per_source[c.source] += 1
            _note_cand(c)
        C["candidates"] = loaded
        print(f"[阶段一] 复用既有候选 {len(C['candidates'])} 条（--reuse-phase1，跳过检索）",
              flush=True)
    else:
        with open(cand_path, "w", encoding="utf-8") as cf:
            for i, job in enumerate(jobs, 1):
                new_for_job = _search_job(job)
                C["candidates"].extend(new_for_job)
                for c in new_for_job:
                    _note_cand(c)
                    cf.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
                if i % 50 == 0 or i == total:
                    cf.flush()
                    print(f"[阶段一] 进度 {i}/{total} 任务，已累积候选 {len(C['candidates'])} 条")

    print(f"[阶段一] 检索完成：候选 {len(C['candidates'])} 条（{total} 个任务）")
    print(f"[阶段一] 各来源候选数: {dict(searched_per_source)}")

    if metadata_only:
        _write_stats(out_dir, jobs, cand_stat, C["n_candidates"], searched_per_source,
                     rejected=0, downloaded=0, failed=0, bytes_=0)
        print("[阶段一] 已完成（--metadata-only，未下载）")
        return {"candidates": C["n_candidates"]}

    # ---------- 运行产物流式落盘：success/failed/rejected 即写即弃，不驻留内存 ----------
    f_success = open(os.path.join(out_dir, "downloads_success.jsonl"), "w", encoding="utf-8")
    f_failed = open(os.path.join(out_dir, "downloads_failed.jsonl"), "w", encoding="utf-8")
    f_rejected = open(os.path.join(out_dir, "candidates_rejected.jsonl"), "w", encoding="utf-8")
    emit_lock = threading.Lock()  # 队列模式下多 worker 并发追写

    def _emit(fh, c: "models.Candidate", key: str) -> None:
        with emit_lock:
            fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
            fh.flush()
            C[key] += 1

    def _emit_success(c: "models.Candidate") -> None:
        _emit(f_success, c, "n_success")

    def _emit_failed(c: "models.Candidate") -> None:
        _emit(f_failed, c, "n_failed")

    def _emit_rejected(c: "models.Candidate") -> None:
        _emit(f_rejected, c, "n_rejected")

    # ---------- 阶段二：筛选 + 分组 + 下载（含续传/扩源；--queue 走共享队列） ----------
    cands_by_instance: Dict[str, list] = defaultdict(list)
    for c in C["candidates"]:
        job = job_by_instance.get(c.instance)
        if job is None:
            c.status = models.STATUS_REJECTED
            c.reject_reason = "找不到对应任务配置"
            _emit_rejected(c)
            continue
        if c.source_authorized:
            ok, reason = filterer.filter_candidate(
                c, job.effective, get_adapter(c.source).allowed_suffixes)
        else:
            ok, reason = filterer.filter_candidate_unauthorized(
                c, job.effective, None)
        if not ok:
            c.status = models.STATUS_REJECTED
            c.reject_reason = reason
            _emit_rejected(c)
            continue
        cands_by_instance[c.instance].append(c)

    if queue_mode and meta_dir:
        # —— 队列模式：共享下载队列（见 queue.py）——
        from .queue import DownloadQueue, is_congestion_fail

        qid = queue_id or run_id or "run"
        state_dir = _state_dir(meta_dir)
        qdb = os.path.join(state_dir, f".dlq_{qid}.sqlite3")
        flags_dir = os.path.join(state_dir, f".dlq_flags_{qid}")
        os.makedirs(flags_dir, exist_ok=True)
        my_flag = os.path.join(flags_dir, f"done.{shard_index}")
        q = DownloadQueue(qdb)
        q_since = time.time() - 300.0  # done 计数只算本轮（同 run-id 重启防误判）
        reused_instance = defaultdict(int)

        def _reuse_lake_candidate(entry: _LakeEntry, instance: str, content_url: str) -> None:
            """湖内已有该 URL：复用记录并补标签关联（不再联网）。"""
            cand = _lake_candidate(entry, instance, content_url, images_dir)
            if cand.sha256 in baseline_shas.get(instance, ()):
                _persist(cand)
                return
            _emit_success(cand)
            C["instance_success"][instance] += 1
            reused_instance[instance] += 1
            if cand.sha256 and cand.source_authorized:
                C["cc"] += 1
            elif cand.sha256:
                C["unauth"] += 1
            _persist(cand)

        def _enqueue_wave1() -> None:
            """基础轮：每 (instance,source) 最多 max_per_source 张（rank 最小者优先）。"""
            n_q = n_reuse = 0
            batch = []  # 批量投递缓冲：凑够一批一个写事务，避免逐条抢锁
            for instance, cs in cands_by_instance.items():
                job = job_by_instance[instance]
                cap = job.effective.max_per_source
                instance_min = ((min_images - existing_counts[instance])
                           if instance in existing_counts else None)
                by_src = defaultdict(list)
                for c in cs:
                    if c.content_url and c.content_url in url_index:
                        _reuse_lake_candidate(url_index[c.content_url], instance, c.content_url)
                        n_reuse += 1
                        continue
                    by_src[c.source].append(c)
                for src, lst in by_src.items():
                    lst.sort(key=lambda c: (c.source_rank if c.source_rank is not None else 0))
                    take = lst if (not cap or cap <= 0) else lst[:cap]
                    for c in take:
                        batch.append((1, instance, src, c.content_url or "",
                                      c.source_rank or 0, cap, instance_min, c.to_dict()))
                        n_q += 1
                        if len(batch) >= 2000:
                            q.enqueue_many(batch)
                            batch = []
            q.enqueue_many(batch)
            print(f"[queue] wave1 投递 {n_q} 条候选 / 复用湖内 {n_reuse} 条", flush=True)

        # —— 弱源识别：本 run 队列证据 + 历史账本 ——
        weak_sources = set(weak_from_health)
        for s, st in q.wave_source_stats(1).items():
            if st["total"] >= 30 and st["done"] == 0 and st["exhausted"] >= 30:
                weak_sources.add(s)
        if weak_sources:
            print(f"[queue] 扩源跳过弱源: {sorted(weak_sources)}", flush=True)

        def _enqueue_leftovers() -> int:
            """扩源第 1 层（一次）：复用已检索候选、放宽每源上限（wave1 未投递/封顶跳过的）。"""
            n_q = 0
            batch = []  # 同 wave1：批量投递避免逐条抢写锁
            for instance, cs in cands_by_instance.items():
                job = job_by_instance[instance]
                existing = existing_counts.get(instance, 0)
                total_now = existing + q.instance_done_count(instance, q_since) + reused_instance[instance]
                if total_now >= min_images:
                    continue
                w1 = q.instance_wave_rows(instance, 1)
                remaining = []
                for c in cs:
                    if not c.content_url or c.content_url in url_index:
                        continue
                    if c.source in weak_sources:
                        continue
                    row = w1.get(c.content_url)
                    if row is None:
                        remaining.append(c)          # wave1 未投递（超出基础轮封顶）
                        continue
                    if row["attempts"] >= 3:
                        continue                    # 3 次无果，跳过
                    if row["status"] in ("pending", "claimed", "done"):
                        continue
                    remaining.append(c)              # 封顶跳过 → 放宽上限后重试
                cap2 = starved_cap or job.effective.max_per_source
                instance_min2 = min_images - existing
                if remaining:
                    by_src = defaultdict(list)
                    for c in remaining:
                        by_src[c.source].append(c)
                    for src, lst in by_src.items():
                        lst.sort(key=lambda c: (c.source_rank if c.source_rank is not None else 0))
                        take = lst if (not cap2 or cap2 <= 0) else lst[:cap2]
                        for c in take:
                            batch.append((WAVE_LEFTOVERS, instance, src, c.content_url,
                                          c.source_rank or 0, cap2, instance_min2, c.to_dict()))
                            n_q += 1
                            if len(batch) >= 2000:
                                q.enqueue_many(batch)
                                batch = []
            q.enqueue_many(batch)
            return n_q

        # —— 动态补搜（与下载并行）：轮转自家未达标标签，从健康源池补搜新候选 ——
        refill_wave = WAVE_REFILL_BASE
        refill_tried = defaultdict(set)   # instance -> 已补搜过的来源
        refill_cursor = 0
        refill_idle = 0
        refill_done = False

        def _refill_once(limit: int = REFILL_BATCH) -> int:
            """每轮补搜：最多 limit 个未达标标签，每个补搜 1 个健康源。返回新投递数。"""
            nonlocal refill_wave, refill_cursor, refill_idle
            insts = refill_instances
            if not insts:
                return 0
            n_q = 0
            for _ in range(limit):
                if refill_cursor >= len(insts):
                    refill_cursor = 0
                instance = insts[refill_cursor]
                refill_cursor += 1
                job = job_by_instance[instance]
                existing = existing_counts.get(instance, 0)
                total_now = existing + q.instance_done_count(instance, q_since) + reused_instance[instance]
                if total_now >= min_images:
                    continue
                if expansion_sources:
                    pool = [s for s in expansion_sources
                            if s not in weak_sources and s not in refill_tried[instance]]
                else:
                    pool = [s for s in _active_sources(job)
                            if s not in weak_sources and s not in refill_tried[instance]]
                if not pool:
                    continue
                s = pool[0]
                refill_tried[instance].add(s)
                cands = _search_source(s, job)
                C["src_health"][s]["candidates"] += len(cands)
                cap2 = starved_cap or job.effective.max_per_source
                instance_min2 = min_images - existing
                kept = []
                for c in cands:
                    if c.content_url and c.content_url in url_index:
                        continue
                    if c.source_authorized:
                        ok, _ = filterer.filter_candidate(
                            c, job.effective, get_adapter(c.source).allowed_suffixes)
                    else:
                        ok, _ = filterer.filter_candidate_unauthorized(
                            c, job.effective, None)
                    if ok:
                        kept.append(c)
                for c in kept[: (cap2 if cap2 and cap2 > 0 else len(kept))]:
                    q.enqueue(refill_wave, instance, s, c.content_url or "",
                              c.source_rank or 0, cap2, instance_min2, c.to_dict())
                    n_q += 1
                refill_wave += 1
            return n_q

        def _process_queue_item(item: dict) -> None:
            instance = item["instance"]
            job = job_by_instance.get(instance)
            rec = q.reuse_rec(item["content_url"])
            if rec:
                # 同 URL 已被其它 worker 下载成功：复用记录、补本标签关联
                cand = _rec_to_candidate(rec, instance, images_dir)
                if cand.sha256 in baseline_shas.get(instance, ()):
                    _persist(cand)
                else:
                    _emit_success(cand)
                    _count_instance(instance)
                    if cand.sha256 and cand.source_authorized:
                        _count("cc")
                    elif cand.sha256:
                        _count("unauth")
                    _persist(cand)
                q.mark_done(item["id"], rec)
                return
            c = models.Candidate.from_dict(item["payload"])
            c.instance = instance
            cfg = job.effective if job else eff0
            adapter = get_adapter(c.source)
            allowed = None if not c.source_authorized else adapter.allowed_suffixes
            ok_dl, downloaded = downloader.download_and_store(
                c, cfg, allowed, images_dir, rate_limiter,
                headers=getattr(adapter, "download_headers", None))
            if ok_dl and downloaded:
                d = downloaded[0]
                if d.sha256 in baseline_shas.get(instance, ()):
                    _persist(d)
                    q.mark_done(item["id"], _candidate_to_rec(d, images_dir))
                    return
                _emit_success(d)
                _count_instance(instance)
                _count("bytes", d.actual_size or 0)
                if d.source_authorized:
                    _count("cc")
                else:
                    _count("unauth")
                _count_src(c.source, "dl_ok")
                _persist(d)
                q.mark_done(item["id"], _candidate_to_rec(d, images_dir))
                q.bump_cap(c.source, up=True)      # AIMD：成功 +1 试探加并发
            else:
                if c.status == models.STATUS_GATE_REJECTED:
                    _emit_rejected(c)
                    q.mark_skipped(item["id"])   # 分辨率门拒绝：重试无意义
                elif getattr(c, "fail_kind", None) in DETERMINISTIC_FAIL:
                    # 死链/防盗链：确定性失败，重试无意义，直接跳过
                    _emit_failed(c)
                    _count_src(c.source, "dl_dead")
                    q.mark_skipped(item["id"])
                else:
                    _emit_failed(c)
                    fk = getattr(c, "fail_kind", None)
                    if fk == "timeout":
                        _count_src(c.source, "dl_timeout")
                    else:
                        _count_src(c.source, "dl_fail")
                    if is_congestion_fail(fk):
                        q.bump_cap(c.source, up=False)  # AIMD：拥堵减半降温
                    q.release(item["id"])        # <3 次回 pending 退避重试；>=3 跳过

        _enqueue_wave1()
        n_left = _enqueue_leftovers()
        print(f"[queue] 存量候选放宽投递 {n_left} 条（与下载并行）", flush=True)
        # 内存释放：候选已全部序列化进 sqlite 队列（payload 字段），驻留池即刻释放，
        # 避免大批次 reuse-phase1 下 RSS 随候选规模膨胀；补搜只需要实例名清单。
        refill_instances = list(cands_by_instance)
        C["candidates"].clear()
        cands_by_instance.clear()
        stop = threading.Event()
        stats_lock = threading.Lock()

        def _count(key: str, n: int = 1) -> None:
            with stats_lock:
                C[key] += n

        def _count_instance(instance: str, n: int = 1) -> None:
            with stats_lock:
                C["instance_success"][instance] += n

        def _count_src(source: str, key: str, n: int = 1) -> None:
            with stats_lock:
                C["src_health"][source][key] += n

        def _worker_loop() -> None:
            buf = []  # 批量领取的本地缓冲：减少 claim 频次（Q1 选源扫描很贵）
            while not stop.is_set():
                try:
                    if not buf:
                        buf = q.claim_many(q_since, limit=8)
                        if not buf:
                            if stop.wait(1.0):
                                return
                            continue
                    item = buf.pop()
                    _process_queue_item(item)
                except sqlite3.OperationalError:
                    # 队列库瞬时锁争用/磁盘压力：退避后重试，不拖死整个进程。
                    # buf 里未处理的件仍是 claimed，掉线兜底 600s 后自动回池，不丢。
                    if stop.wait(5.0):
                        return
                    continue
                with stats_lock:
                    C["q_done"] += 1
                    if C["q_done"] % 50 == 0:
                        print(f"[queue] worker 处理 {C['q_done']} 件 / 本进程成功 "
                              f"{C['n_success']} 张，队列 {q.counts()}", flush=True)

        threads = [threading.Thread(target=_worker_loop, daemon=True)
                   for _ in range(max(1, queue_threads))]
        for t in threads:
            t.start()

        # —— 并行扩源：worker 下载的同时，主线程每 REFILL_SEC 秒从健康源池补搜一轮 ——
        # （不再串行等 wave 排空；下载与获取新候选同时进行，见 _refill_once）
        flagged = False
        last_refill = time.time() - REFILL_SEC  # 启动即来一轮
        while True:
            if not refill_done and time.time() - last_refill >= REFILL_SEC:
                n_q = _refill_once()
                if n_q == 0:
                    refill_idle += 1
                    if refill_idle >= REFILL_IDLE_LIMIT:
                        refill_done = True
                        print("[queue] 扩源耗尽（连续无新候选），停止补搜", flush=True)
                else:
                    refill_idle = 0
                    print(f"[queue] 补搜轮投递 {n_q} 条新候选（下载并行中）", flush=True)
                last_refill = time.time()
            if refill_done and not flagged:
                with open(my_flag, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
                print("[queue] 本片扩源耗尽，收尾完成，等待其它分片", flush=True)
                flagged = True
            flags = len([f for f in os.listdir(flags_dir) if f.startswith("done.")])
            if flagged and flags >= n_shards and q.drained():
                break
            time.sleep(2.0)
        stop.set()
        for t in threads:
            t.join(timeout=10)
        q.close()
    else:
        # —— 逐标签串行下载（非队列模式，原逻辑）——
        processed = 0
        for job in jobs:
            instance = job.instance
            # 基础分组（跳过种子死源）
            groups = defaultdict(list)
            for c in cands_by_instance.get(instance, []):
                if c.source in dead:
                    continue
                groups[(instance, c.source)].append(c)
            # topup 标签基础轮也限到 min_images（补采只补缺口）；新标签不限
            _process_groups(groups, job.effective.max_per_source, job,
                            stop_at=min_images if instance in existing_counts else None)

            # —— 太少动态扩源（优化：优先复用已检索候选、放宽每源上限，避免重新联网检索）——
            if C["instance_success"][instance] < min_images:
                before = C["instance_success"][instance]
                # 第 1 层：复用本标签已检索到的候选（base 轮只下了每源 1 张，余下候选仍在内存），
                # 放宽每源上限到 starved_max_per_source，直到达标或候选耗尽。不再重新检索。
                # 关键：剔除本运行已下载的候选（content_url 已入 url_index），否则 base 轮已下的图
                # 会在扩源轮被「续传分支」再次计入 / 再次落盘，造成重复记录与计数膨胀。
                remaining = defaultdict(list)
                for (inst, src), cs in groups.items():
                    if inst != instance:
                        continue
                    for c in cs:
                        if c.content_url and c.content_url in url_index:
                            continue
                        remaining[(inst, src)].append(c)
                _process_groups(remaining, starved_cap or job.effective.max_per_source,
                                job, stop_at=min_images)
                # 第 2 层：仍不足且配置了「额外扩源池」（非基础源的其它源）才补搜。
                if C["instance_success"][instance] < min_images and expansion_sources:
                    extra = [s for s in expansion_sources
                             if s not in dead and s not in _active_sources(job)]
                    if extra:
                        exp_cands = []
                        for s in extra:
                            exp_cands.extend(_search_source(s, job))
                        exp_cands = [c for c in exp_cands if c.content_url not in url_index]
                        kept = []
                        for c in exp_cands:
                            if c.source_authorized:
                                ok, _ = filterer.filter_candidate(
                                    c, job.effective, get_adapter(c.source).allowed_suffixes)
                            else:
                                ok, _ = filterer.filter_candidate_unauthorized(
                                    c, job.effective, None)
                            if ok:
                                kept.append(c)
                        if kept:
                            eg2 = defaultdict(list)
                            for c in kept:
                                eg2[(instance, c.source)].append(c)
                            _process_groups(eg2, starved_cap or job.effective.max_per_source,
                                            job, stop_at=min_images)
                after = C["instance_success"][instance]
                if after > before:
                    print(f"[扩源] {instance}: {before} -> {after} 张", flush=True)

            processed += 1
            if processed % REFRESH_EVERY == 0:
                print(f"[阶段二] 进度 {processed}/{total} 标签，已下载 {C['n_success']} 张",
                      flush=True)

    # 末尾干净去重重写主清单：success 记录已在下载时逐条追写 images.jsonl
    # （_persist，含续传命中的实例关联），此处只做全文件 sha 去重合并，故传空列表。
    if meta_dir:
        _update_master_manifest(meta_dir, [], run_id or "")
        _merge_health(meta_dir, C["src_health"])
    # 运行产物（success/failed/rejected）已流式落盘，收尾关闭句柄
    for _fh in (f_success, f_failed, f_rejected):
        _fh.close()

    _write_stats(out_dir, jobs, cand_stat, C["n_candidates"], searched_per_source,
                 rejected=C["n_rejected"],
                 downloaded=C["n_success"],
                 failed=C["n_failed"],
                 bytes_=C["bytes"],
                 cc_downloaded=C["cc"],
                 unauth_downloaded=C["unauth"],
                 capped_per_source=C["capped"])

    print(f"[阶段二] 拒绝 {C['n_rejected']} / "
          f"下载成功 {C['n_success']} (授权 {C['cc']} + 未授权 {C['unauth']}) / "
          f"失败 {C['n_failed']} / 封顶跳过 {C['capped']}")
    return {
        "candidates": C["n_candidates"],
        "rejected": C["n_rejected"],
        "downloaded": C["n_success"],
        "cc_downloaded": C["cc"],
        "unauthorized_downloaded": C["unauth"],
        "failed": C["n_failed"],
        "bytes": C["bytes"],
    }


def _rel_path(local_path: Optional[str]) -> str:
    """把 local_path 规整为相对 data/dataset/ 的路径（blobs/<aa>/<sha>.<ext>）。"""
    if not local_path:
        return ""
    p = local_path
    for prefix in ("data/dataset/", "dataset/"):  # 兼容旧路径
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    return p


def _cand_attr(c: "models.Candidate", field: str):
    return getattr(c, field, None)


def _update_master_manifest(meta_dir: str, success: list, run_id: str) -> None:
    """upsert 主清单 images.jsonl（按 sha256 去重，跨批次累积），
    并维护 state/collect/runs/_latest 软链。全程持 meta_lock（分片并发安全）。"""
    with meta_lock(meta_dir):
        _update_master_manifest_locked(meta_dir, success, run_id)


def _update_master_manifest_locked(meta_dir: str, success: list, run_id: str) -> None:
    os.makedirs(meta_dir, exist_ok=True)
    mpath = os.path.join(meta_dir, "images.jsonl")
    existing: Dict[str, dict] = {}
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sha = rec.get("sha256")
                if not sha:
                    continue
                prev = existing.get(sha)
                if prev is None:
                    existing[sha] = rec
                    continue
                # 重复 sha 行（同图异 URL/跨批命中）：合并 instances/tiers 并集，
                # 其余字段保留首条；直接覆盖会丢实例关联（已验证存在数千条差异行）
                prev.setdefault("instances", [])
                for t in rec.get("instances") or []:
                    if t not in prev["instances"]:
                        prev["instances"].append(t)
                prev.setdefault("tiers", [0])
                for tier in rec.get("tiers") or []:
                    if tier not in prev["tiers"]:
                        prev["tiers"].append(tier)
                # 溯源映射取并集（按实例/来源对齐，冲突时保留先落盘值）
                for fld in ("queries", "query_langs", "asset_ids"):
                    add = rec.get(fld) or {}
                    if not add:
                        continue
                    base = prev.get(fld)
                    if not isinstance(base, dict):
                        base = {}
                    base.update({k: v for k, v in add.items() if k not in base})
                    if base:
                        prev[fld] = base

    for c in success:
        sha = c.sha256
        if not sha:
            continue
        ext = os.path.splitext(c.local_path or "")[1].lstrip(".") if c.local_path else ""
        tier = c.selected_tier if c.selected_tier is not None else 0
        rec = existing.get(sha)
        if rec is None:
            rec = {
                "sha256": sha,
                "ext": ext,
                "source": c.source,
                "source_kind": c.source_kind,
                "source_authorized": c.source_authorized,
                "license": c.license_raw or "",
                "author": c.author,
                "credit": c.credit,
                "width": c.actual_width,
                "height": c.actual_height,
                "orig_width": c.orig_width,
                "orig_height": c.orig_height,
                "size_bytes": c.actual_size,
                "mime": c.actual_mime,
                "instances": [c.instance] if c.instance else [],
                "tiers": [tier],
                "source_rank": c.source_rank,
                "source_score": c.source_score,
                "queries": ({c.instance: c.query} if c.instance and c.query else {}),
                "query_langs": ({c.instance: c.query_lang} if c.instance and c.query_lang else {}),
                "asset_ids": ({c.source: c.asset_id} if c.source and c.asset_id else {}),
                "landing_url": c.landing_url,
                "fetched_at": c.fetched_at,
                "path": _rel_path(c.local_path),
            }
        else:
            if c.instance and c.instance not in rec["instances"]:
                rec["instances"].append(c.instance)
            if tier not in rec["tiers"]:
                rec["tiers"].append(tier)
            # 溯源映射并集合并（与重复 sha 行合并同规则）
            for fld, add in (
                ("queries", {c.instance: c.query} if c.instance and c.query else {}),
                ("query_langs", {c.instance: c.query_lang} if c.instance and c.query_lang else {}),
                ("asset_ids", {c.source: c.asset_id} if c.source and c.asset_id else {}),
            ):
                if not add:
                    continue
                base = rec.get(fld)
                if not isinstance(base, dict):
                    base = {}
                base.update({k: v for k, v in add.items() if k not in base})
                rec[fld] = base
            for fld, cf in (
                ("source", "source"), ("source_kind", "source_kind"),
                ("license", "license_raw"), ("author", "author"),
                ("credit", "credit"), ("mime", "actual_mime"),
                ("landing_url", "landing_url"), ("path", "local_path"),
                ("source_rank", "source_rank"), ("source_score", "source_score"),
            ):
                if rec.get(fld) is None and _cand_attr(c, cf) is not None:
                    rec[fld] = _cand_attr(c, cf)
            for fld, cf in (
                ("width", "actual_width"), ("height", "actual_height"),
                ("size_bytes", "actual_size"),
            ):
                if rec.get(fld) is None:
                    rec[fld] = _cand_attr(c, cf)
            if not rec.get("ext") and ext:
                rec["ext"] = ext
        existing[sha] = rec

    tmp_path = mpath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for sha in sorted(existing):
            f.write(json.dumps(existing[sha], ensure_ascii=False) + "\n")
    os.replace(tmp_path, mpath)

    # state/collect/runs/_latest -> 本批次 run_id（_state_dir 已含 state/collect，勿重复拼接）
    runs_dir = os.path.join(_state_dir(meta_dir), "runs")
    os.makedirs(runs_dir, exist_ok=True)
    latest = os.path.join(runs_dir, "_latest")
    if os.path.lexists(latest):
        os.remove(latest)
    os.symlink(run_id, latest)


def _write_stats(out_dir, jobs, cand_stat, n_candidates, searched_per_source,
                 rejected, downloaded, failed, bytes_,
                 cc_downloaded=0, unauth_downloaded=0, capped_per_source=0):
    lines = []
    for j in jobs:
        t = cand_stat.get(j.instance) or {"candidates": 0, "zh": 0}
        zh_ratio = (t["zh"] / t["candidates"]) if t["candidates"] else 0
        lines.append(json.dumps({
            "instance": j.instance,
            "source": ",".join(j.sources),
            "candidates": t["candidates"],
            "zh_candidates": t["zh"],
            "zh_ratio": round(zh_ratio, 3),
            "target_count": j.effective.target_count,
        }, ensure_ascii=False))
    lines.append(json.dumps({
        "instance": "TOTAL",
        "source": "*",
        "candidates": n_candidates,
        "by_source": dict(searched_per_source),
        "rejected": rejected,
        "downloaded": downloaded,
        "cc_downloaded": cc_downloaded,
        "unauthorized_downloaded": unauth_downloaded,
        "failed": failed,
        "capped_per_source": capped_per_source,
        "bytes": bytes_,
    }, ensure_ascii=False))
    with open(os.path.join(out_dir, "stats.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
