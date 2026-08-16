"""Safebooru 图片板来源适配器（未授权国际源）。

Safebooru 提供无需鉴权的公开 JSON API（Danbooru 系图板），对动漫/游戏/欧美动画
角色的覆盖极好，正补中文源对「虚构角色」长尾（西方文学/影视/游戏角色）的盲区。

API: https://safebooru.org/index.php?page=dapi&s=post&q=index&tags=<tag>&json=1
- tags 为英文小写下划线格式；只取 rating=general/safe 的图；
- 字段 file_url/sample_url/width/height/score/owner；
- 图片为社区上传作品，非 CC、授权不明 → source_authorized=False。

实现 best-effort：无英文 query（纯中文实体）时返回空，不中断任务。
"""

from __future__ import annotations

import re
from typing import Dict, List

from .base import SourceAdapter, register
from ..models import (
    Candidate,
    SOURCE_KIND_UNAUTHORIZED,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_json

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_API = "https://safebooru.org/index.php"


def _to_instance(q: str) -> str:
    t = re.sub(r"\s+", "_", (q or "").strip().lower())
    return t


class SafebooruAdapter(SourceAdapter):
    name = "safebooru"
    source_kind = SOURCE_KIND_UNAUTHORIZED
    allowed_suffixes = ("safebooru.org",)
    lang = "en"
    is_authorized = False

    def search(self, job: Job) -> List[Dict]:
        tag = _to_instance(job.query or "")
        if not tag:
            return []
        cfg = job.effective
        data = fetch_json(
            _API,
            allowed_suffixes=("safebooru.org",),
            params={
                "page": "dapi",
                "s": "post",
                "q": "index",
                "tags": tag,
                "json": "1",
                "limit": min(40, max(10, cfg.target_count * 6)),
            },
            headers={"User-Agent": _UA},
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        if not isinstance(data, list):
            return []
        out: List[Dict] = []
        for p in data:
            if not isinstance(p, dict):
                continue
            if p.get("rating") not in ("general", "safe"):
                continue
            u = p.get("file_url") or p.get("sample_url")
            if not u or not u.startswith("https://"):
                continue
            out.append({
                "url": u,
                "w": p.get("width"),
                "h": p.get("height"),
                "id": p.get("id"),
                "owner": p.get("owner"),
                "score": p.get("score"),
                "tags": p.get("tags"),
                "page": f"https://safebooru.org/index.php?page=post&s=view&id={p.get('id')}",
            })
            if len(out) >= 40:
                break
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        url = raw["url"]
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"safebooru-{raw.get('id')}",
            instance=job.instance,
            query=job.query,
            landing_url=raw.get("page") or "",
            content_url=url,
            declared_mime=None,  # 下载后由 Pillow 复验
            declared_width=raw.get("w") or None,
            declared_height=raw.get("h") or None,
            declared_size=None,
            author=raw.get("owner"),
            credit=f"Safebooru user {raw.get('owner')}",
            license_raw="未知(未授权来源,非CC)",
            source_authorized=False,
            source_score=raw.get("score"),
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(SafebooruAdapter())
