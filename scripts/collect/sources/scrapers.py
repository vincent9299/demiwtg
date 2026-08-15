"""非 CC 中文图片爬虫源（未授权来源，docs 扩种中文源需求）。

⚠️ 重要声明（务必阅读）：
- 以下源（必应/360/搜狗/百度百科/豆瓣）均为【非 CC、授权不明】的图片，抓取与再
  分发存在版权/ToS 风险。它们与 baidu 同类，单流模式下与授权源统一落盘、统一清单，
  但每张图都保留 source / license_raw=未知 / source_authorized=False，下游可按
  source_authorized 切分纯 CC 子集，或整体作为带 provenance 的图集使用。
- 这些源反爬/防盗链严重：下载带来源 Referer，但仍可能偶发 403/限流/空结果。
- 图片 host 不可枚举，故下载采用 https-only 校验（allowed_suffixes=None），
  但仍要求 https，且由 Pillow 解码把关只存真实图片。

实现均为 best-effort：解析失败/空结果时优雅降级（返回空候选），不中断整体任务。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..models import (
    Candidate,
    SOURCE_KIND_UNAUTHORIZED,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_text
from .base import SourceAdapter, register


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


_IMG_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:jpg|jpeg|png)", re.I)


class _SearchImageScraper(SourceAdapter):
    """中文图片搜索/社区站爬虫的共享基类。"""

    lang = "zh"
    is_authorized = False
    source_kind = SOURCE_KIND_UNAUTHORIZED
    search_timeout = 15
    search_max_retries = 1
    search_headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    download_headers = {"User-Agent": _UA, "Referer": "https://www.baidu.com/"}
    search_suffixes: tuple = ()

    def search(self, job: Job) -> List[dict]:
        q = job.zh_query or job.en_query
        if not q:
            return []
        url = self.build_url(q)
        try:
            text = fetch_text(
                url,
                allowed_suffixes=self.search_suffixes,
                headers=self.search_headers,
                timeout=self.search_timeout,
                max_retries=self.search_max_retries,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {self.name} 检索失败（{job.tag}）: {e}")
            return []
        items = self.parse_items(text, q)
        out = []
        for it in items:
            u = (it.get("url") or "").strip()
            if not u.lower().startswith("http"):
                continue
            w = _to_int(it.get("w"))
            h = _to_int(it.get("h"))
            out.append({"url": u, "w": w, "h": h, "page": it.get("page") or ""})
            if len(out) >= 40:
                break
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        u = raw["url"]
        w = _to_int(raw.get("w"))
        h = _to_int(raw.get("h"))
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=u,
            tag=job.tag,
            query=job.zh_query or job.en_query,
            query_lang="zh",
            landing_url=raw.get("page") or "",
            content_url=u,
            declared_mime=None,  # 下载后由 Pillow 复验
            declared_width=w or None,
            declared_height=h or None,
            declared_size=None,
            author=None,
            credit=None,
            license_raw="未知(未授权来源,非CC)",
            source_authorized=False,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )

    def build_url(self, q: str) -> str:
        raise NotImplementedError

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        raise NotImplementedError


class BingImagesAdapter(_SearchImageScraper):
    name = "bing"
    search_suffixes = ("cn.bing.com", "bing.com")
    download_headers = {"User-Agent": _UA, "Referer": "https://cn.bing.com/"}

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return ("https://cn.bing.com/images/async?q=" + quote(q) +
                "&count=35&first=1&mmasync=1&qft=+filterui:imagesize-medium")

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        # Bing async 响应整体 HTML 转义：&quot;murl&quot;:&quot;...&quot;。
        # 直接对已转义串提取，并把 http 升级为 https（下载器要求 https）。
        murls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', text)
        wh = re.findall(r'w:(\d+),h:(\d+)', text)
        items = []
        for i, u in enumerate(murls):
            if u.startswith("http:"):
                # 仅把 http:// 升级为 https://（正则已含 scheme，取 host 段重拼，
                # 避免拼成 https::// 双冒号导致 urlopen "no host given"）。
                u = "https://" + u[u.find("://") + 3:]
            w = h = None
            if i < len(wh):
                w, h = int(wh[i][0]), int(wh[i][1])
            items.append({"url": u, "w": w, "h": h, "page": ""})
        return items


class So360Adapter(_SearchImageScraper):
    name = "so360"
    search_suffixes = ("image.so.com",)
    download_headers = {"User-Agent": _UA, "Referer": "https://image.so.com/"}

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return ("https://image.so.com/j?q=" + quote(q) +
                "&sn=0&pn=30&src=tab_www")

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        lst = (data.get("list") or []) if isinstance(data, dict) else []
        items = []
        for it in lst:
            u = it.get("imgurl") or it.get("middle") or it.get("thumb") or ""
            if not u:
                continue
            items.append({
                "url": u,
                "w": _to_int(it.get("width")),
                "h": _to_int(it.get("height")),
                "page": it.get("url") or "",
                "title": it.get("title") or "",
            })
        return items


class SogouAdapter(_SearchImageScraper):
    name = "sogou"
    search_suffixes = ("pic.sogou.com",)
    download_headers = {"User-Agent": _UA, "Referer": "https://pic.sogou.com/"}

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return ("https://pic.sogou.com/pics/json.jsp?query=" + quote(q) +
                "&start=0&len=30&reqType=ajax&searchType=image")

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        lst = (data.get("items") or []) if isinstance(data, dict) else []
        items = []
        for it in lst:
            u = (it.get("oriPicURL") or it.get("locImageLink")
                 or it.get("thumbUrl") or "")
            if not u:
                continue
            items.append({
                "url": u,
                "w": _to_int(it.get("width")),
                "h": _to_int(it.get("height")),
                "page": it.get("groupName") or it.get("url") or "",
                "title": it.get("title") or "",
            })
        return items


class BaiduBaikeAdapter(_SearchImageScraper):
    name = "baidu_baike"
    search_suffixes = ("baike.baidu.com",)
    download_headers = {"User-Agent": _UA, "Referer": "https://baike.baidu.com/"}

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return "https://baike.baidu.com/item/" + quote(q)

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        # 百度百科词条页中的图册/配图，多为 bcebos / bdimg CDN。
        urls = re.findall(
            r'https?://(?:bkimg\.cdn\.bcebos\.com|[^/]+\.bdimg\.com)/[^\s"\'<>]+\.'
            r'(?:jpg|jpeg|png)', text, re.I)
        skip = ("icon", "logo", "footer", "bg", "arrow", "btn", "sprite")
        items = []
        seen = set()
        for u in urls:
            low = u.lower()
            if any(k in low for k in skip):
                continue
            if u in seen:
                continue
            seen.add(u)
            items.append({"url": u, "w": None, "h": None, "page": ""})
        return items


class DoubanAdapter(_SearchImageScraper):
    name = "douban"
    search_suffixes = ("douban.com", "doubanio.com")
    download_headers = {"User-Agent": _UA, "Referer": "https://www.douban.com/"}

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return "https://www.douban.com/search?q=" + quote(q)

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        # 豆瓣结果页/条目的图片，host 为 imgN.doubanio.com。
        urls = re.findall(
            r'https?://img[0-9]\.doubanio\.com/[^\s"\'<>]+\.(?:jpg|jpeg|png)', text, re.I)
        items = []
        for u in urls:
            items.append({"url": u, "w": None, "h": None, "page": ""})
        return items


register(BingImagesAdapter())
register(So360Adapter())
register(SogouAdapter())
register(BaiduBaikeAdapter())
register(DoubanAdapter())
