"""采集管线（docs 第 6 节两阶段 + 第 7 节产物）。

阶段一：逐 job 检索 → 统一 Candidate → 写 candidates.jsonl（不下载）。
阶段二：筛选 → 配额/预算检查 → 下载复验 → 写 rejected / success / failed / stats JSONL。
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


def run(jobs: List[Job], out_dir: str, images_dir: str,
        metadata_only: bool = False,
        only_tags: Optional[set] = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    rate_limiter = RateLimiter()

    jobs = [j for j in jobs if (not only_tags or j.tag in only_tags)]
    job_by_tag = _job_index(jobs)

    # ---------- 阶段一：检索 + 候选 ----------
    candidates: List[models.Candidate] = []
    searched_per_tag = defaultdict(int)
    for job in jobs:
        adapter = get_adapter(job.source)
        try:
            raws = adapter.search(job)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 标签 {job.tag}: 来源 {job.source} 检索失败: {e}")
            continue
        searched_per_tag[job.tag] = len(raws)
        for raw in raws:
            candidates.append(adapter.to_candidate(raw, job))

    models.write_jsonl(os.path.join(out_dir, "candidates.jsonl"), candidates)
    print(f"[阶段一] 检索候选 {len(candidates)} 条（{len(jobs)} 个任务）")

    if metadata_only:
        _write_stats(out_dir, jobs, candidates, searched_per_tag,
                     rejected=0, downloaded=0, failed=0, bytes_=0)
        print("[阶段一] 已完成（--metadata-only，未下载）")
        return {"candidates": len(candidates)}

    # ---------- 阶段二：筛选 + 下载 ----------
    rejected: List[models.Candidate] = []
    success: List[models.Candidate] = []
    failed: List[models.Candidate] = []

    accepted_per_tag: Dict[str, int] = defaultdict(int)
    # 总预算：各 job 预算视为其份额，求和作为全量预算
    run_budget = sum(j.effective.total_budget_bytes for j in jobs) or 0
    total_bytes = 0

    for c in candidates:
        job = job_by_tag.get(c.tag)
        if job is None:
            c.status = models.STATUS_REJECTED
            c.reject_reason = "找不到对应任务配置"
            rejected.append(c)
            continue

        ok, reason = filterer.filter_candidate(
            c, job.effective, get_adapter(job.source).allowed_suffixes
        )
        if not ok:
            c.status = models.STATUS_REJECTED
            c.reject_reason = reason
            rejected.append(c)
            continue

        # 标签配额
        if accepted_per_tag[c.tag] >= job.effective.target_count:
            c.status = models.STATUS_REJECTED
            c.reject_reason = f"超过标签配额 (target={job.effective.target_count})"
            rejected.append(c)
            continue

        # 总预算（用声明大小预估；未知则跳过预估）
        if (run_budget and c.declared_size is not None
                and total_bytes + c.declared_size > run_budget):
            c.status = models.STATUS_REJECTED
            c.reject_reason = "超过总预算"
            rejected.append(c)
            continue

        # 接受并下载
        accepted_per_tag[c.tag] += 1
        c.status = models.STATUS_ACCEPTED
        ok_dl = downloader.download_and_store(
            c, job.effective,
            get_adapter(job.source).allowed_suffixes,
            images_dir, rate_limiter,
        )
        if ok_dl:
            success.append(c)
            total_bytes += c.actual_size or 0
        else:
            failed.append(c)

    models.write_jsonl(os.path.join(out_dir, "candidates_rejected.jsonl"), rejected)
    models.write_jsonl(os.path.join(out_dir, "downloads_success.jsonl"), success)
    models.write_jsonl(os.path.join(out_dir, "downloads_failed.jsonl"), failed)
    _write_stats(out_dir, jobs, candidates, searched_per_tag,
                 rejected=len(rejected), downloaded=len(success),
                 failed=len(failed), bytes_=total_bytes)

    print(f"[阶段二] 拒绝 {len(rejected)} / 下载成功 {len(success)} / "
          f"下载失败 {len(failed)} / 累计 {total_bytes} 字节")
    return {
        "candidates": len(candidates),
        "rejected": len(rejected),
        "downloaded": len(success),
        "failed": len(failed),
        "bytes": total_bytes,
    }


def _write_stats(out_dir, jobs, candidates, searched_per_tag,
                 rejected, downloaded, failed, bytes_):
    lines = []
    by_tag = defaultdict(lambda: {"candidates": 0, "downloaded": 0})
    for c in candidates:
        by_tag[c.tag]["candidates"] += 1
    # 成功计数按 tag 聚合（success 列表未在此传，用 stats 近似：downloaded 总数放 TOTAL）
    for j in jobs:
        lines.append(json.dumps({
            "tag": j.tag,
            "source": j.source,
            "searched": searched_per_tag.get(j.tag, 0),
            "candidates": by_tag[j.tag]["candidates"],
            "target_count": j.effective.target_count,
        }, ensure_ascii=False))
    lines.append(json.dumps({
        "tag": "TOTAL",
        "source": "*",
        "candidates": len(candidates),
        "rejected": rejected,
        "downloaded": downloaded,
        "failed": failed,
        "bytes": bytes_,
    }, ensure_ascii=False))
    with open(os.path.join(out_dir, "stats.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
