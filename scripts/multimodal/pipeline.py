"""采集管线（docs 第 6 节两阶段 + 第 7 节产物）。

阶段一：逐 job 遍历所有启用来源（授权源 sources + 未授权源 unauthorized_sources，
        排除运行期动态剔除的死源），各源按自身语言（en/zh）取对应 query 检索 →
        统一 Candidate（带上游原生次序 source_rank / 原生分数 source_score）→
        写 candidates.jsonl。
阶段二：按 source 类型做【基础校验】（CC 源走许可证白名单；未授权源跳过许可证校验，
        仅做 URL/MIME/体积检查）→ 通过基础校验的候选下载原图（内容寻址去重，不改分辨率），
        并在 downloader 解码后用【实际分辨率门】(min_resolution) 拦截低分辨率原图（不落盘）。

本版增强（2026-08-13）：
- 断点续传：启动加载本湖 images.jsonl → 构建 url_index(content_url→rec)；下载前若
  content_url 已在索引且 blob 仍在，直接复用、跳过网络抓取。
- labels 增量落盘：每下载成功 1 张即追写 images.jsonl（含 content_url），并按需刷新
  tags.json + by_tag/，任意时刻可看按标签组织的结果。
- 死源动态剔除：known_dead_sources 种子 + 运行期统计（某源在 >=dead_min_tags 个标签上
  0 成功即剔除），后续标签不再搜/下该源。
- 太少动态扩源：某标签成功图 < min_images_per_tag 时，用 expansion_sources 补搜并用
  starved_max_per_source 放宽每源上限，直到达标或候选耗尽。

每张候选的上游原生信号都会落库：source_rank / source_score。
单流存储：授权与未授权候选都下载到【同一个】images_dir，写入【同一个】downloads_success.jsonl。
数据湖布局：本批次过程产物写 <meta_dir>/runs/<run_id>/；主清单 <meta_dir>/images.jsonl
（按 sha256 去重，跨批次累积）与 <meta_dir>/tags.json（tag↔图 关系索引）作为全局元数据源；
<meta_dir>/runs/_latest 软链指向本批次。
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

from . import models
from .config import Job, load_config
from . import filterer
from . import downloader
from .sources import get_adapter
from .util import RateLimiter


def _job_index(jobs: List[Job]) -> Dict[str, Job]:
    return {j.tag: j for j in jobs}


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


def _candidate_to_rec(c: "models.Candidate", images_dir: str) -> dict:
    """把成功 Candidate 投影成 images.jsonl 记录（含 content_url 以便续传）。"""
    sha = c.sha256 or ""
    ext = os.path.splitext(c.local_path or "")[1].lstrip(".") if c.local_path else ""
    tier = c.selected_tier if c.selected_tier is not None else 0
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
        "tags": [c.tag] if c.tag else [],
        "tiers": [tier],
        "source_rank": c.source_rank,
        "source_score": c.source_score,
        "landing_url": c.landing_url,
        "content_url": c.content_url,
        "fetched_at": c.fetched_at,
        "path": _rel_path(c.local_path),
    }


def _rec_to_candidate(rec: dict, tag: str, images_dir: str) -> "models.Candidate":
    """从 images.jsonl 记录重建一个 success Candidate（用于续传复用，避免重抓）。"""
    sha = rec.get("sha256", "")
    local = _blob_path(rec, images_dir)
    return models.Candidate(
        source=rec.get("source", ""),
        source_kind=rec.get("source_kind", ""),
        asset_id=sha,
        tag=tag or (rec.get("tags") or [""])[0],
        query=tag or "",
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


def _refresh_tags_and_by_tag(meta_dir: str, tags_map: dict, images_dir: str) -> None:
    """把内存中的 tags_map 写出 tags.json 并重建 by_tag/ 软链（幂等）。"""
    if not meta_dir:
        return
    with open(os.path.join(meta_dir, "tags.json"), "w", encoding="utf-8") as f:
        json.dump(tags_map, f, ensure_ascii=False, indent=1)
    try:
        from scripts.link_by_tag import link_from_tags as _lt  # type: ignore
        lake_root = os.path.dirname(os.path.normpath(images_dir))
        _lt(os.path.join(meta_dir, "tags.json"),
            os.path.join(lake_root, "by_tag"), images_dir)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 重建 by_tag/ 失败（忽略，可手动 scripts/link_by_tag.py）: {e}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(jobs: List[Job], out_dir: str, images_dir: str,
        meta_dir: Optional[str] = None, run_id: Optional[str] = None,
        metadata_only: bool = False,
        only_tags: Optional[set] = None) -> dict:

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

    # --jobs 支持子串匹配，便于按实例名试点
    jobs = [j for j in jobs if (not only_tags or any(t in j.tag for t in only_tags))]
    job_by_tag = _job_index(jobs)

    # 运行期配置（带默认值，兼容旧配置无新键）
    eff0 = jobs[0].effective if jobs else None
    min_images = (getattr(eff0, "min_images_per_tag", None) or 4) if eff0 else 4
    dead_min_tags = (getattr(eff0, "dead_min_tags", None) or 8) if eff0 else 8
    known_dead = list(getattr(eff0, "known_dead_sources", None) or []) if eff0 else []
    expansion_sources = list(getattr(eff0, "expansion_sources", None) or []) if eff0 else []
    starved_cap = getattr(eff0, "starved_max_per_source", None)

    # ---------- 加载本湖已有状态：断点续传 + 增量标签 ----------
    url_index: Dict[str, dict] = {}     # content_url -> rec（blob 仍存在）
    tags_map: Dict[str, list] = {}      # tag -> [{sha256, ext, source, tiers, ...}]
    mpath = os.path.join(meta_dir, "images.jsonl") if meta_dir else None
    if mpath and os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                sha = rec.get("sha256")
                if not sha:
                    continue
                # 续传索引：仅保留 blob 仍在磁盘的记录
                if _blob_exists(rec, images_dir):
                    cu = rec.get("content_url")
                    if cu:
                        url_index[cu] = rec
                for t in rec.get("tags", []):
                    tags_map.setdefault(t, []).append({
                        "sha256": sha,
                        "ext": rec.get("ext", ""),
                        "source": rec.get("source", ""),
                        "tiers": rec.get("tiers", [0]),
                        "source_rank": rec.get("source_rank"),
                        "source_score": rec.get("source_score"),
                    })
    print(f"[state] 载入本湖已下载 {len(url_index)} 条（续传索引），标签 {len(tags_map)} 个",
          flush=True)

    # 运行期统计
    src_stats = defaultdict(lambda: {"tags": 0, "ok": 0})
    dead = set(known_dead)
    if dead:
        print(f"[dead] 种子死源 {sorted(dead)}", flush=True)

    # 计数器（跨整个 run）
    C = {
        "success": [], "failed": [], "rejected": [], "candidates": [],
        "cc": 0, "unauth": 0, "bytes": 0, "capped": 0,
        "tag_success": defaultdict(int),
    }
    searched_per_source = defaultdict(int)

    REFRESH_EVERY = 5  # 每 N 个标签刷新一次 tags.json + by_tag

    def _persist(c: "models.Candidate") -> None:
        """追写 images.jsonl（增量、崩溃安全）+ 更新内存 url_index / tags_map。"""
        rec = _candidate_to_rec(c, images_dir)
        rec["content_url"] = c.content_url
        # 幂等：同一 content_url 的图已落盘（断点续传命中 / 扩源重复命中）则不再重复写文件，
        # 仅确保 url_index / tags_map 已含该 sha，避免重复记录与计数膨胀。
        if c.content_url and c.content_url in url_index and _blob_exists(rec, images_dir):
            lst = tags_map.setdefault(c.tag, [])
            if not any(e["sha256"] == c.sha256 for e in lst):
                lst.append({
                    "sha256": c.sha256,
                    "ext": rec.get("ext", ""),
                    "source": c.source,
                    "tiers": [c.selected_tier or 0],
                    "source_rank": c.source_rank,
                    "source_score": c.source_score,
                })
            return
        if mpath:
            with open(mpath, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        url_index[c.content_url] = rec
        lst = tags_map.setdefault(c.tag, [])
        if not any(e["sha256"] == c.sha256 for e in lst):
            lst.append({
                "sha256": c.sha256,
                "ext": rec.get("ext", ""),
                "source": c.source,
                "tiers": [c.selected_tier or 0],
                "source_rank": c.source_rank,
                "source_score": c.source_score,
            })

    def _active_sources(job: Job) -> List[str]:
        return [s for s in (list(job.sources) + list(job.unauthorized_sources))
                if s not in dead]

    def _search_job(job: Job) -> List["models.Candidate"]:
        new = []
        for s in _active_sources(job):
            src_stats[s]["tags"] += 1
            try:
                raws = get_adapter(s).search(job)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 标签 {job.tag}: 来源 {s} 检索失败: {e}")
                continue
            out = []
            for raw in raws:
                out.append(get_adapter(s).to_candidate(raw, job))
            for idx, c in enumerate(out):
                if c.source_rank is None:
                    c.source_rank = idx
            searched_per_source[s] += len(out)
            new.extend(out)
        return new

    def _process_groups(groups, cap, job, stop_at=None):
        """下载 groups（(tag,source)->[cands]），带 url_index 续传跳过 + max_per_source 封顶。
        返回新增 success 数。"""
        local_new = 0
        for (tag, src), cs in groups.items():
            if tag not in job_by_tag:
                continue
            cs.sort(key=lambda c: (c.source_rank if c.source_rank is not None else 0))
            succ = 0
            for c in cs:
                if cap and cap > 0 and succ >= cap:
                    C["capped"] += 1
                    continue
                # —— 断点续传：URL 已下载过则直接复用，跳过网络抓取 ——
                if c.content_url and c.content_url in url_index:
                    rec = url_index[c.content_url]
                    cand = _rec_to_candidate(rec, tag, images_dir)
                    C["success"].append(cand)
                    C["tag_success"][tag] += 1
                    src_stats[src]["ok"] += 1
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
                    C["success"].append(d)
                    C["tag_success"][tag] += 1
                    src_stats[src]["ok"] += 1
                    C["bytes"] += d.actual_size or 0
                    if d.source_authorized:
                        C["cc"] += 1
                    else:
                        C["unauth"] += 1
                    _persist(d)
                    succ += 1
                    local_new += 1
                else:
                    if c.status == models.STATUS_GATE_REJECTED:
                        C["rejected"].append(c)
                    else:
                        C["failed"].append(c)
                if stop_at and C["tag_success"][tag] >= stop_at:
                    break
            if stop_at and C["tag_success"][tag] >= stop_at:
                break
        return local_new

    def _maybe_eval_dead():
        for s, st in list(src_stats.items()):
            if s in dead:
                continue
            if st["tags"] >= dead_min_tags and st["ok"] == 0:
                dead.add(s)
                print(f"[dead] 动态剔除 {s}（已搜 {st['tags']} 标签 0 成功）", flush=True)

    # ---------- 阶段一：多源检索 + 候选（增量落盘 candidates.jsonl） ----------
    cand_path = os.path.join(out_dir, "candidates.jsonl")
    total = len(jobs)
    with open(cand_path, "w", encoding="utf-8") as cf:
        for i, job in enumerate(jobs, 1):
            new_for_job = _search_job(job)
            C["candidates"].extend(new_for_job)
            for c in new_for_job:
                cf.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
            if i % 50 == 0 or i == total:
                cf.flush()
                print(f"[阶段一] 进度 {i}/{total} 任务，已累积候选 {len(C['candidates'])} 条")

    print(f"[阶段一] 检索完成：候选 {len(C['candidates'])} 条（{total} 个任务）")
    print(f"[阶段一] 各来源候选数: {dict(searched_per_source)}")

    if metadata_only:
        _write_stats(out_dir, jobs, C["candidates"], searched_per_source,
                     rejected=0, downloaded=0, failed=0, bytes_=0)
        print("[阶段一] 已完成（--metadata-only，未下载）")
        return {"candidates": len(C["candidates"])}

    # ---------- 阶段二：筛选 + 分组 + 下载（含续传/死源/扩源） ----------
    rejected_stage2 = []
    cands_by_tag: Dict[str, list] = defaultdict(list)
    for c in C["candidates"]:
        job = job_by_tag.get(c.tag)
        if job is None:
            c.status = models.STATUS_REJECTED
            c.reject_reason = "找不到对应任务配置"
            rejected_stage2.append(c)
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
            rejected_stage2.append(c)
            continue
        cands_by_tag[c.tag].append(c)

    processed = 0
    for job in jobs:
        tag = job.tag
        # 基础分组（仅活跃源）
        groups = defaultdict(list)
        for c in cands_by_tag.get(tag, []):
            if c.source in dead:
                continue
            groups[(tag, c.source)].append(c)
        _process_groups(groups, job.effective.max_per_source, job)

        # —— 太少动态扩源（优化：优先复用已检索候选、放宽每源上限，避免重新联网检索）——
        if C["tag_success"][tag] < min_images:
            before = C["tag_success"][tag]
            # 第 1 层：复用本标签已检索到的候选（base 轮只下了每源 1 张，余下候选仍在内存），
            # 放宽每源上限到 starved_max_per_source，直到达标或候选耗尽。不再重新检索。
            # 关键：剔除本运行已下载的候选（content_url 已入 url_index），否则 base 轮已下的图
            # 会在扩源轮被「续传分支」再次计入 / 再次落盘，造成重复记录与计数膨胀。
            remaining = defaultdict(list)
            for (tg, src), cs in groups.items():
                if tg != tag:
                    continue
                for c in cs:
                    if c.content_url and c.content_url in url_index:
                        continue
                    remaining[(tg, src)].append(c)
            _process_groups(remaining, starved_cap or job.effective.max_per_source,
                            job, stop_at=min_images)
            # 第 2 层：仍不足且配置了「额外扩源池」（非基础源的其它源）才补搜。
            if C["tag_success"][tag] < min_images and expansion_sources:
                extra = [s for s in expansion_sources
                         if s not in dead and s not in _active_sources(job)]
                if extra:
                    exp_cands = []
                    for s in extra:
                        try:
                            raws = get_adapter(s).search(job)
                        except Exception:  # noqa: BLE001
                            continue
                        for raw in raws:
                            exp_cands.append(get_adapter(s).to_candidate(raw, job))
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
                            eg2[(tag, c.source)].append(c)
                        _process_groups(eg2, starved_cap or job.effective.max_per_source,
                                        job, stop_at=min_images)
            after = C["tag_success"][tag]
            if after > before:
                print(f"[扩源] {tag}: {before} -> {after} 张", flush=True)

        processed += 1
        _maybe_eval_dead()
        if processed % REFRESH_EVERY == 0:
            _refresh_tags_and_by_tag(meta_dir, tags_map, images_dir)
            print(f"[阶段二] 进度 {processed}/{total} 标签，已下载 {len(C['success'])} 张",
                  flush=True)

    # 末尾刷新标签视图 + 干净去重重写主清单（传入本块 success 以正确按标签并集）
    _refresh_tags_and_by_tag(meta_dir, tags_map, images_dir)
    if meta_dir:
        _update_master_manifest(meta_dir, C["success"], run_id or "")
    # 块末调试产出
    models.write_jsonl(os.path.join(out_dir, "candidates_rejected.jsonl"),
                       rejected_stage2 + C["rejected"])
    models.write_jsonl(os.path.join(out_dir, "downloads_success.jsonl"), C["success"])
    models.write_jsonl(os.path.join(out_dir, "downloads_failed.jsonl"), C["failed"])

    _write_stats(out_dir, jobs, C["candidates"], searched_per_source,
                 rejected=len(rejected_stage2) + len(C["rejected"]),
                 downloaded=len(C["success"]),
                 failed=len(C["failed"]),
                 bytes_=C["bytes"],
                 cc_downloaded=C["cc"],
                 unauth_downloaded=C["unauth"],
                 capped_per_source=C["capped"])

    print(f"[阶段二] 拒绝 {len(rejected_stage2) + len(C['rejected'])} / "
          f"下载成功 {len(C['success'])} (授权 {C['cc']} + 未授权 {C['unauth']}) / "
          f"失败 {len(C['failed'])} / 封顶跳过 {C['capped']}")
    return {
        "candidates": len(C["candidates"]),
        "rejected": len(rejected_stage2) + len(C["rejected"]),
        "downloaded": len(C["success"]),
        "cc_downloaded": C["cc"],
        "unauthorized_downloaded": C["unauth"],
        "failed": len(C["failed"]),
        "bytes": C["bytes"],
    }


def _rel_path(local_path: Optional[str]) -> str:
    """把 local_path 规整为相对 dataset/ 的路径（blobs/<aa>/<sha>.<ext>）。"""
    if not local_path:
        return ""
    p = local_path
    if p.startswith("dataset/"):
        p = p[len("dataset/"):]
    return p


def _cand_attr(c: "models.Candidate", field: str):
    return getattr(c, field, None)


def _update_master_manifest(meta_dir: str, success: list, run_id: str) -> None:
    """upsert 主清单 images.jsonl（按 sha256 去重，跨批次累积），
    并派生 tags.json 与 runs/_latest 软链。"""
    os.makedirs(meta_dir, exist_ok=True)
    mpath = os.path.join(meta_dir, "images.jsonl")
    existing: Dict[str, dict] = {}
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                existing[rec["sha256"]] = rec

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
                "tags": [c.tag] if c.tag else [],
                "tiers": [tier],
                "source_rank": c.source_rank,
                "source_score": c.source_score,
                "landing_url": c.landing_url,
                "fetched_at": c.fetched_at,
                "path": _rel_path(c.local_path),
            }
        else:
            if c.tag and c.tag not in rec["tags"]:
                rec["tags"].append(c.tag)
            if tier not in rec["tiers"]:
                rec["tiers"].append(tier)
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

    with open(mpath, "w", encoding="utf-8") as f:
        for sha in sorted(existing):
            f.write(json.dumps(existing[sha], ensure_ascii=False) + "\n")

    # 派生 tag↔图 关系索引：tag -> [ {sha256, ext, source, tiers} ]
    tags: Dict[str, list] = {}
    for rec in existing.values():
        for t in rec.get("tags", []):
            tags.setdefault(t, []).append({
                "sha256": rec["sha256"],
                "ext": rec.get("ext", ""),
                "source": rec.get("source", ""),
                "tiers": rec.get("tiers", [0]),
                "source_rank": rec.get("source_rank"),
                "source_score": rec.get("source_score"),
            })
    with open(os.path.join(meta_dir, "tags.json"), "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=1)

    # runs/_latest -> 本批次 run_id
    runs_dir = os.path.join(meta_dir, "runs")
    os.makedirs(runs_dir, exist_ok=True)
    latest = os.path.join(runs_dir, "_latest")
    if os.path.lexists(latest):
        os.remove(latest)
    os.symlink(run_id, latest)


def _write_stats(out_dir, jobs, candidates, searched_per_source,
                 rejected, downloaded, failed, bytes_,
                 cc_downloaded=0, unauth_downloaded=0, capped_per_source=0):
    lines = []
    by_tag = defaultdict(lambda: {"candidates": 0, "cc": 0, "zh": 0})
    by_source = defaultdict(int)
    for c in candidates:
        by_tag[c.tag]["candidates"] += 1
        by_source[c.source] += 1
        if c.query_lang == "zh":
            by_tag[c.tag]["zh"] += 1
    for j in jobs:
        t = by_tag[j.tag]
        zh_ratio = (t["zh"] / t["candidates"]) if t["candidates"] else 0
        lines.append(json.dumps({
            "tag": j.tag,
            "source": ",".join(j.sources),
            "candidates": t["candidates"],
            "zh_candidates": t["zh"],
            "zh_ratio": round(zh_ratio, 3),
            "target_count": j.effective.target_count,
        }, ensure_ascii=False))
    lines.append(json.dumps({
        "tag": "TOTAL",
        "source": "*",
        "candidates": len(candidates),
        "by_source": dict(by_source),
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
