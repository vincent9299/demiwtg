"""Danbooru / Gelbooru 图片板来源适配器（未授权国际源，safebooru 同源族扩展）。

- danbooru（danbooru.donmai.us）：Danbooru 本家，ACG 角色/作品 tag 体系最全，
  正补「虚构角色」长尾。匿名 API 限 1 req/s（per-host 限速器默认 1s 恰好满足）；
  UA 必须是项目标识式（DEFAULT_HEADERS 即是），浏览器 UA 会被 Cloudflare 403。
  检索强制 rating:general（全站含 R18，只采全年龄向）。
  API: https://danbooru.donmai.us/posts.json?tags=<tag> rating:general&limit=N
  图片 host cdn.donmai.us，社区上传作品非 CC → source_authorized=False。

- gelbooru（gelbooru.com）：与 safebooru 同款 dapi 接口，西方动画/游戏角色覆盖互补；
  同样强制 rating:general。本沙箱环境当前对其超时（网络层拦截），实现保持就绪，
  健康账本会自然把它判为弱源，换环境即生效。

tag 规则同 safebooru：英文小写下划线；纯中文实体无英文 query 时返回空。
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


def _to_tag(q: str) -> str:
    return re.sub(r"\s+", "_", (q or "").strip().lower())


class DanbooruAdapter(SourceAdapter):
    name = "danbooru"
    source_kind = SOURCE_KIND_UNAUTHORIZED
    allowed_suffixes = ("danbooru.donmai.us", "cdn.donmai.us")
    lang = "en"
    is_authorized = False

    _API = "https://danbooru.donmai.us/posts.json"
    _SEARCH_SUFFIXES = ("danbooru.donmai.us",)

    def search(self, job: Job) -> List[Dict]:
        tag = _to_tag(job.query or "")
        if not tag:
            return []
        cfg = job.effective
        data = fetch_json(
            self._API,
            allowed_suffixes=self._SEARCH_SUFFIXES,
            params={
                "tags": f"{tag} rating:general",
                "limit": min(40, max(10, cfg.target_count * 6)),
            },
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        if not isinstance(data, list):
            return []
        out: List[Dict] = []
        for p in data:
            if not isinstance(p, dict):
                continue
            if p.get("rating") != "g":
                continue
            u = p.get("file_url") or p.get("large_file_url")
            if not u or not u.startswith("https://"):
                continue
            out.append({
                "url": u,
                "w": p.get("image_width"),
                "h": p.get("image_height"),
                "size": p.get("file_size"),
                "id": p.get("id"),
                "uploader": p.get("uploader_id"),
                "score": p.get("score"),
                "source": p.get("source"),
                "characters": p.get("tag_string_character"),
                "page": f"https://danbooru.donmai.us/posts/{p.get('id')}",
            })
            if len(out) >= 40:
                break
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"danbooru-{raw.get('id')}",
            instance=job.instance,
            query=job.query,
            landing_url=raw.get("page") or "",
            content_url=raw["url"],
            declared_mime=None,  # 下载后由 Pillow 复验
            declared_width=raw.get("w") or None,
            declared_height=raw.get("h") or None,
            declared_size=raw.get("size"),
            author=(f"user {raw.get('uploader')}" if raw.get("uploader") else None),
            credit=f"Danbooru post {raw.get('id')}",
            license_raw="未知(未授权来源,非CC)",
            source_authorized=False,
            source_score=raw.get("score"),
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


class GelbooruAdapter(SourceAdapter):
    name = "gelbooru"
    source_kind = SOURCE_KIND_UNAUTHORIZED
    allowed_suffixes = ("gelbooru.com", "videocdn.gelbooru.com")
    lang = "en"
    is_authorized = False

    _API = "https://gelbooru.com/index.php"
    _SEARCH_SUFFIXES = ("gelbooru.com",)

    def search(self, job: Job) -> List[Dict]:
        tag = _to_tag(job.query or "")
        if not tag:
            return []
        cfg = job.effective
        data = fetch_json(
            self._API,
            allowed_suffixes=self._SEARCH_SUFFIXES,
            params={
                "page": "dapi",
                "s": "post",
                "q": "index",
                "tags": f"{tag} rating:general",
                "json": "1",
                "limit": min(40, max(10, cfg.target_count * 6)),
            },
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        # dapi JSON 形态：[{...}] 或 {"post": [...]}
        if isinstance(data, dict):
            data = data.get("post") or []
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
                "page": f"https://gelbooru.com/index.php?page=post&s=view&id={p.get('id')}",
            })
            if len(out) >= 40:
                break
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"gelbooru-{raw.get('id')}",
            instance=job.instance,
            query=job.query,
            landing_url=raw.get("page") or "",
            content_url=raw["url"],
            declared_mime=None,
            declared_width=raw.get("w") or None,
            declared_height=raw.get("h") or None,
            declared_size=None,
            author=raw.get("owner"),
            credit=f"Gelbooru post {raw.get('id')}",
            license_raw="未知(未授权来源,非CC)",
            source_authorized=False,
            source_score=raw.get("score"),
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(DanbooruAdapter())
register(GelbooruAdapter())
