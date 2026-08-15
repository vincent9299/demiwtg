"""iNaturalist 来源适配器（docs 4.3，M3）。

流程：物种名/学名 → taxon ID（/v1/taxa）→ 带照片的 observations 分页
（/v1/observations）→ 按 photo ID 形成候选。

许可证强制规则（docs 4.3 重点）：
- 使用**照片级许可证**（photo.license），不把 observation 级 license 当 photo license；
- 一律保存原始声明，不自动推断版本（如 cc-by 不推断为 cc-by-4.0）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..models import Candidate, SOURCE_KIND_COMMUNITY, STATUS_CANDIDATE
from ..config import Job
from ..util import fetch_json
from .base import SourceAdapter, register


API = "https://api.inaturalist.org/v1"
# iNaturalist 原图托管在官方开放数据 S3 桶（inaturalist-open-data.s3.amazonaws.com），
# 以及 static.inaturalist.org；两者都需放行，否则下载器 URL 校验会拒图。
ALLOWED_SUFFIXES = (
    "inaturalist.org",
    "static.inaturalist.org",
    "inaturalist-open-data.s3.amazonaws.com",
)


def _pick_url(photo: dict) -> Optional[str]:
    """选定原图内容 URL。

    ⚠️ 关键坑（2026-08-12 实测）：**新版 iNaturalist API 的 photo 对象只给一个
    `url` 字段，且那是 75×75 方形【缩略图】(…/photos/<id>/square.jpg)**，同时另给
    `original_dimensions` 字典报【原图真实尺寸】。它【不返回】original_url/large_url。
    历史上直接取 `photo.url` → 下载的全是 75px 缩略图，却按 original_dimensions 的
    2048×1550 登记，导致"假原图"。

    修复：原图 URL 必须由 square 缩略图 URL【派生】——把路径里的 /square.<ext>
    （或 small/medium/large）替换为 /original.<ext>（同一 S3 桶，host 已放行）。
    派生顺序：original（真实原图，最长边可能很大）→ large（封顶 1024，稳过 768 门）
    → 兜底才用 square（75px，几乎必被分辨率门拦截）。
    """
    base = photo.get("url")
    if not base:
        # 极老字段兜底
        for key in ("original_url", "large_url", "medium_url"):
            u = photo.get(key)
            if u:
                return u
        return None
    import re
    orig = re.sub(r"/(square|small|medium|large)\.(\w+)$", r"/original.\2", base)
    large = re.sub(r"/(square|small|medium|large)\.(\w+)$", r"/large.\2", base)
    for u in (orig, large):
        if u and u != base:
            return u
    return base  # 兜底：square（必被分辨率门拦）


class INaturalistAdapter(SourceAdapter):
    name = "inaturalist"
    source_kind = SOURCE_KIND_COMMUNITY
    allowed_suffixes = ALLOWED_SUFFIXES

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        # 1) 物种名/学名 → taxon
        taxa = fetch_json(
            f"{API}/taxa",
            allowed_suffixes=ALLOWED_SUFFIXES,
            params={"q": job.query, "per_page": 1},
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        results = (taxa.get("results") or [])
        if not results:
            return []
        taxon = results[0]
        taxon_id = taxon.get("id")
        taxon_name = taxon.get("name") or job.query

        # 2) 分页拉带照片的 observations（一次取较多候选供筛选）
        per_page = min(50, max(10, cfg.target_count * 5))
        obs = fetch_json(
            f"{API}/observations",
            allowed_suffixes=ALLOWED_SUFFIXES,
            params={
                "taxon_id": taxon_id,
                "photos": "true",
                "quality_grade": "research",
                "per_page": per_page,
                "order": "votes",
                "order_by": "votes",
            },
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )

        out: List[dict] = []
        for o in (obs.get("results") or []):
            for photo in (o.get("photos") or []):
                url = _pick_url(photo)
                if not url:
                    continue
                # 照片级许可证：iNaturalist 在 photo/observation 上用 license_code
                # 字段（如 "cc-by-nc"），不是 "license"；原始声明，不推断版本。
                lic = photo.get("license_code") or o.get("license_code")
                if lic in ("", "null", None):
                    lic = None
                # 原始尺寸在 original_dimensions 字典里（非顶层 width/height）。
                dim = photo.get("original_dimensions") or {}
                w = dim.get("width") if isinstance(dim, dict) else None
                h = dim.get("height") if isinstance(dim, dict) else None
                out.append({
                    "observation_id": o.get("id"),
                    "photo_id": photo.get("id"),
                    "url": url,
                    "license": lic,
                    "user": (o.get("user") or {}).get("login"),
                    "taxon_id": taxon_id,
                    "taxon_name": taxon_name,
                    "width": w,
                    "height": h,
                    # 原生分数：社区投票数（cached_votes_total 为数值计数；
                    # 注意 o["votes"] 本身是投票对象数组，不能当分数用）。
                    "score": o.get("cached_votes_total"),
                    "observed_on": (o.get("observed_on") or o.get("created_at")),
                    "place": ((o.get("place") or {}).get("name")),
                })
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        url = raw["url"]
        w = raw.get("width")
        h = raw.get("height")
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"photo-{raw.get('photo_id')}",
            tag=job.tag,
            query=job.query,
            landing_url=(
                f"https://www.inaturalist.org/observations/{raw.get('observation_id')}"
            ),
            content_url=url,
            # iNaturalist 照片多为 jpg/png；按 URL 后缀声明 MIME
            declared_mime=("image/png" if url.lower().endswith(".png")
                           else "image/jpeg"),
            declared_width=w,
            declared_height=h,
            declared_size=None,  # 原图字节数未知，下载器流式封顶
            author=raw.get("user"),
            credit=f"iNaturalist user {raw.get('user')}",
            license_raw=raw.get("license"),  # 照片级原始声明，原样保存
            source_score=raw.get("score"),
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(INaturalistAdapter())
