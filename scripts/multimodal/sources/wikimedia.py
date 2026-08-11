"""Wikimedia Commons 来源适配器（M1 首个可运行来源，docs 4.1）。

检索方式：Action API `File` 命名空间按 Query 检索 → imageinfo 读取元数据，
投影为统一 Candidate。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models import (
    Candidate,
    SOURCE_KIND_CATALOG,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_json, strip_html
from .base import SourceAdapter, register


API = "https://commons.wikimedia.org/w/api.php"
SEARCH_SUFFIXES = ("commons.wikimedia.org", "wikimedia.org", "upload.wikimedia.org")


def _extmeta_str(extmeta: Dict[str, Any], key: str):
    node = extmeta.get(key)
    if not node:
        return None
    val = node.get("value")
    if isinstance(val, dict):
        val = val.get("html") or val.get("value")
    if val is None:
        return None
    return strip_html(str(val))


class WikimediaAdapter(SourceAdapter):
    name = "wikimedia"
    source_kind = SOURCE_KIND_CATALOG
    allowed_suffixes = SEARCH_SUFFIXES

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        # 检索更多候选再筛选：至少 target_count 的若干倍，封顶 50
        limit = min(50, max(20, cfg.target_count * 5))
        s = fetch_json(
            API,
            allowed_suffixes=SEARCH_SUFFIXES,
            params={
                "action": "query",
                "list": "search",
                "srsearch": job.query,
                "srnamespace": 6,  # File 命名空间
                "srlimit": limit,
                "format": "json",
            },
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        hits = (s.get("query") or {}).get("search") or []
        if not hits:
            return []

        # 批量拉取 imageinfo 元数据（一次最多 50 个 title）
        # iiurlwidth 请求缩略图 URL：Wikimedia 对原图批量下载限流严格（429），
        # 推荐用 thumbnail 路径（upload.wikimedia.org/thumb/...）抓取。
        thumb_width = max(cfg.min_width, min(cfg.thumb_width, 2000))
        titles = [h["title"] for h in hits]
        raw_by_title: Dict[str, dict] = {}
        for i in range(0, len(titles), 50):
            batch = "|".join(titles[i:i + 50])
            info = fetch_json(
                API,
                allowed_suffixes=SEARCH_SUFFIXES,
                params={
                    "action": "query",
                    "titles": batch,
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime|user|userid|timestamp|extmetadata",
                    "iiurlwidth": thumb_width,
                    "format": "json",
                },
                timeout=cfg.timeout_sec,
                max_retries=cfg.max_retries,
            )
            pages = (info.get("query") or {}).get("pages") or {}
            for pid, page in pages.items():
                ii = (page.get("imageinfo") or [None])[0]
                if ii:
                    raw_by_title[page["title"]] = {"page": page, "ii": ii}

        out = []
        for h in hits:
            raw = raw_by_title.get(h["title"])
            if raw:
                out.append(raw)
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        page = raw["page"]
        ii = raw["ii"]
        ext = (ii.get("extmetadata") or {})
        license_short = _extmeta_str(ext, "LicenseShortName")
        license_full = _extmeta_str(ext, "License")
        if license_short and license_full:
            license_raw = f"{license_short} | {license_full}"
        else:
            license_raw = license_short or license_full

        # 优先用缩略图 URL（Wikimedia 推荐，避免原图 429）。
        # 注意：当原图宽度小于 iiurlwidth 时，thumburl 会回退为原图（路径不含
        # /thumb/），但 thumbwidth 仍被置为请求宽度(1280)，与真实原图宽度不符。
        # 因此仅当 thumbwidth 明显小于原图宽度（确为下采样缩略图）时才用缩略图
        # 维度；否则按原图维度声明，确保复验一致。
        orig_w = ii.get("width")
        orig_h = ii.get("height")
        thumb_url = ii.get("thumburl")
        tw = ii.get("thumbwidth")
        th = ii.get("thumbheight")
        if thumb_url and tw and orig_w and tw < orig_w:
            content_url = thumb_url
            decl_w, decl_h = tw, th
            decl_size = None  # 缩略图字节数未知，复验阶段跳过大小比对
        else:
            content_url = ii.get("url") or thumb_url or ""
            decl_w, decl_h = orig_w, orig_h
            decl_size = ii.get("size")

        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=str(page.get("pageid") or page.get("title")),
            tag=job.tag,
            query=job.query,
            landing_url=ii.get("descriptionurl") or "",
            content_url=content_url,
            declared_mime=ii.get("mime"),
            declared_width=decl_w,
            declared_height=decl_h,
            declared_size=decl_size,
            author=_extmeta_str(ext, "Artist"),
            credit=_extmeta_str(ext, "Credit"),
            license_raw=license_raw,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(WikimediaAdapter())
