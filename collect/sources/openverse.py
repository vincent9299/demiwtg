"""Openverse 来源适配器（CC 聚合源，docs 扩种中文源需求）。

Openverse 聚合 Flickr / Wikimedia / 各大博物馆等 CC 授权图片，支持中文 query，
是"扩大源头 + 加大中文源占比"的干净来源（全部 CC 授权）。

⚠️ 默认不在 sources 内启用：本沙箱实测 api.openverse.org 不可达；
   网络放行后将 "openverse" 加入 config 的 sources 即可生效，检索失败会优雅跳过。

检索：GET https://api.openverse.org/v1/images/ ，中英文各搜一次并合并去重。
"""

from __future__ import annotations

import urllib.error
from typing import Any, Dict, List

from ..models import (
    Candidate,
    SOURCE_KIND_CATALOG,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_json
from .base import SourceAdapter, register


API = "https://api.openverse.org/v1/images"
# API 与常见 CC 图床后缀（下载时校验 host）。图床较多，按需扩充。
SEARCH_SUFFIXES = (
    "api.openverse.org",
    "openverse.org",
    "staticflickr.com",
    "live.staticflickr.com",
    "upload.wikimedia.org",
    "images.metmuseum.org",
    "ids.lib.harvard.edu",
    "iiif.io",
    "europeana.eu",
    "wordpress.com",
    "wp.com",
)
# 新版 API 的 license slug（无 cc- 前缀；publicdomain 已并入 pdm）。
LICENSE_PARAM = "by,by-sa,cc0,pdm"
MIME_BY_FILETYPE = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "gif": "image/gif",
    "webp": "image/webp",
}
# 新版 API 结果中 filetype/filesize 常为 null：从 URL 扩展名回退推断 MIME。
_MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff",
}


def _mime_from_url(url: str) -> str:
    ext = (url.rsplit(".", 1)[-1].split("?")[0] or "").lower()
    return _MIME_BY_EXT.get(ext, "")


def _first(v) -> str:
    if isinstance(v, list):
        return (v[0] if v else "") or ""
    return v or ""


# 连接级熔断：api.openverse.org 不可达（超时/拒连）时，本进程后续调用直接短路，
# 避免在全量标签上重复吃超时。HTTP 错误（429/5xx）不触发熔断。
_CONN_DEAD = {"flag": False}


class OpenverseAdapter(SourceAdapter):
    name = "openverse"
    source_kind = SOURCE_KIND_CATALOG
    allowed_suffixes = SEARCH_SUFFIXES
    lang = "both"
    is_authorized = True

    def _search_one(self, q: str, cfg) -> List[dict]:
        if not q or _CONN_DEAD["flag"]:
            return []
        try:
            data = fetch_json(
                API,
                allowed_suffixes=("api.openverse.org", "openverse.org"),
                params={
                    "q": q,
                    "license": LICENSE_PARAM,
                    # 匿名请求 page_size 上限 20（超限返回 401）
                    "page_size": str(min(20, max(10, (cfg.target_count or 4) * 6))),
                    "mature": "false",
                },
                timeout=cfg.timeout_sec,
                max_retries=cfg.max_retries,
            )
        except urllib.error.HTTPError as e:  # HTTP 层错误：本次失败，不熔断
            print(f"[warn] openverse 检索失败（{q}）: {e}")
            return []
        except Exception as e:  # noqa: BLE001  连接层错误（超时/拒连/DNS）：熔断
            if not _CONN_DEAD["flag"]:
                _CONN_DEAD["flag"] = True
                print(f"[warn] openverse 连接失败，本次运行已熔断（重启进程后恢复）: {e}")
            return []
        results = (data or {}).get("results") or []
        out = []
        for r in results:
            url = r.get("url") or ""
            if not url:
                continue
            out.append({
                "_r": r, "_url": url,
                "_w": r.get("width"), "_h": r.get("height"),
            })
        return out

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        # both：英文 + 中文各搜一次，合并去重（按 url）。
        raws = self._search_one(job.en_query, cfg)
        seen = {x["_url"] for x in raws}
        for r in self._search_one(job.zh_query, cfg):
            if r["_url"] not in seen:
                seen.add(r["_url"])
                raws.append(r)
        return raws

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        r = raw["_r"]
        lic = f"{r.get('license') or ''} {r.get('license_version') or ''}".strip()
        w, h = raw.get("_w"), raw.get("_h")
        ft = (r.get("filetype") or "").lower()
        mime = MIME_BY_FILETYPE.get(ft) or _mime_from_url(raw["_url"])
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=str(r.get("id") or raw["_url"]),
            instance=job.instance,
            query=(job.zh_query or job.en_query),
            query_lang="zh" if job.zh_query and job.zh_query != job.en_query else "en",
            landing_url=r.get("foreign_landing_url") or "",
            content_url=raw["_url"],
            declared_mime=mime,
            declared_width=_to_int(w),
            declared_height=_to_int(h),
            declared_size=_to_int(r.get("filesize")),
            author=_first(r.get("creator")),
            credit=None,
            license_raw=lic or "未知",
            source_authorized=True,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


register(OpenverseAdapter())
