"""Wikimedia Commons 来源适配器（M1 首个可运行来源，docs 4.1）。

检索方式：Action API `File` 命名空间按 Query 检索 → imageinfo 读取元数据，
投影为统一 Candidate。

本模块提供两个适配器：
- WikimediaAdapter   (lang="en")：用 job.en_query（英文别名）检索。
- WikimediaZhAdapter (lang="zh")：用 job.zh_query（中文叶子名）检索；
  这是"中文源用中文 query"的主要中文来源（Commons 含大量中文标签图）。

两个适配器都只产出【原图】候选（不再做缩略图档位展开 / 本地缩放），
原始分辨率保留，由 selector 负责按原始宽度分桶选 4 张不同图。
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


class _WikimediaCore:
    """共享的 Commons 检索 + 投影逻辑（en/zh 适配器共用）。"""

    @staticmethod
    def search_query(q: str, cfg, limit: int) -> List[dict]:
        """对单个检索词执行 Commons 检索 + 批量取原图信息，返回原始候选 dict 列表。"""
        s = fetch_json(
            API,
            allowed_suffixes=SEARCH_SUFFIXES,
            params={
                "action": "query",
                "list": "search",
                "srsearch": q,
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
        titles = [h["title"] for h in hits]

        # 原始信息（原图 url + 维度 + extmetadata/许可证），批量一次
        orig: Dict[str, tuple] = {}
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
                    "format": "json",
                },
                timeout=cfg.timeout_sec,
                max_retries=cfg.max_retries,
            )
            pages = (info.get("query") or {}).get("pages") or {}
            for pid, page in pages.items():
                ii = (page.get("imageinfo") or [None])[0]
                if ii:
                    orig[page["title"]] = (page, ii)

        # 每条命中只产出「原图」候选（不再展开缩略图档位，原始分辨率保留）。
        out = []
        seen = set()
        for h in hits:
            t = h["title"]
            o = orig.get(t)
            if not o or t in seen:
                continue
            seen.add(t)
            page, ii = o
            orig_w, orig_h = ii.get("width"), ii.get("height")
            if not (orig_w and orig_h):
                continue
            out.append({
                "page": page, "ii": ii,
                "_content_url": ii.get("url") or "",
                "_decl_w": orig_w, "_decl_h": orig_h,
                "_decl_size": ii.get("size"),
                "_orig_w": orig_w, "_orig_h": orig_h,
            })
        return out

    @staticmethod
    def to_candidate(raw: dict, job: Job, *, lang: str,
                     source: str) -> Candidate:
        page = raw["page"]
        ii = raw["ii"]
        ext = (ii.get("extmetadata") or {})
        license_short = _extmeta_str(ext, "LicenseShortName")
        license_full = _extmeta_str(ext, "License")
        if license_short and license_full:
            license_raw = f"{license_short} | {license_full}"
        else:
            license_raw = license_short or license_full

        content_url = raw.get("_content_url") or ii.get("url") or ""
        decl_w, decl_h, decl_size = (
            raw.get("_decl_w"), raw.get("_decl_h"), raw.get("_decl_size"))

        return Candidate(
            source=source,
            source_kind=SOURCE_KIND_CATALOG,
            asset_id=str(page.get("pageid") or page.get("title")),
            tag=job.tag,
            query=(job.zh_query if lang == "zh" else job.en_query),
            query_lang=lang,
            landing_url=ii.get("descriptionurl") or "",
            content_url=content_url,
            declared_mime=ii.get("mime"),
            declared_width=decl_w,
            declared_height=decl_h,
            declared_size=decl_size,
            author=_extmeta_str(ext, "Artist"),
            credit=_extmeta_str(ext, "Credit"),
            license_raw=license_raw,
            source_authorized=True,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


class WikimediaAdapter(SourceAdapter):
    name = "wikimedia"
    source_kind = SOURCE_KIND_CATALOG
    allowed_suffixes = SEARCH_SUFFIXES
    lang = "en"
    is_authorized = True

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        limit = min(50, max(20, (cfg.target_count or 4) * 5))
        return _WikimediaCore.search_query(job.en_query, cfg, limit)

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        return _WikimediaCore.to_candidate(raw, job, lang="en", source=self.name)


class WikimediaZhAdapter(SourceAdapter):
    name = "wikimedia_zh"
    source_kind = SOURCE_KIND_CATALOG
    allowed_suffixes = SEARCH_SUFFIXES
    lang = "zh"
    is_authorized = True

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        limit = min(50, max(20, (cfg.target_count or 4) * 5))
        return _WikimediaCore.search_query(job.zh_query, cfg, limit)

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        return _WikimediaCore.to_candidate(raw, job, lang="zh", source=self.name)


register(WikimediaAdapter())
register(WikimediaZhAdapter())
