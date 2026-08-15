"""未授权中文网页图片爬虫源（扩种中文源·第二批：通用网页抽取）。

与 scrapers.py 的「API/JSON 式」源（必应/360/搜狗/百度百科/豆瓣）不同，这里以
「通用 HTML <img> 抽取」为主：对站点搜索结果页/标签页做 HTTPS GET，抽取其中所有
https 图片 URL（src / data-src / data-original / data-ori / srcset 首图），过滤掉
icon/logo/avatar/emoji/banner/ad 等 UI 噪声，按可选 host 提示优先保留内容图。

实现均为 best-effort：解析失败/空结果时优雅降级（返回空候选），不中断整体任务。
下载采用 https-only 校验（allowed_suffixes=None），由 Pillow 解码把关只存真实图片。

⚠️ 同 scrapers.py：非 CC、授权不明，产物与授权源统一落盘、统一清单，但每张图保留
source / license_raw=未知 / source_authorized=False，下游可按 source_authorized 切分。

新增来源（名称 → 站点）：
  cctv       央视网（search.cctv.com）
  sogou_tuku 搜狗图库（pic.sogou.com HTML 页，区别于被 403 的 json.jsp 接口）
  zcool      站酷（zcool.com.cn）
  chinanews  中国新闻网（chinanews.com.cn）
  people     人民网（people.com.cn）
  huaban     花瓣网（huaban.com）
  duitang    堆糖（duitang.com，napi JSON）
  zhihu      知乎（zhihu.com，JS 渲染，best-effort）
  sina       新浪搜索（search.sina.com.cn）
  sohu       搜狐搜索（search.sohu.com）
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .base import SourceAdapter, register
from .scrapers import _SearchImageScraper, _UA
from ..models import (
    Candidate,
    SOURCE_KIND_UNAUTHORIZED,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_text


# 通用图片 URL 抽取：<img> 标签里的 src/data-src/data-original/data-ori/srcset 首图。
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_IMG_ATTR_RE = re.compile(r'(data-original|data-src|data-ori|data-lazy-src|src)\s*=\s*["\']([^"\']+)["\']', re.I)
_SRCSET_RE = re.compile(r'srcset\s*=\s*["\']([^"\']+)["\']', re.I)
# 抽取常见图片扩展名（含 webp：部分中文站仅提供 webp，Pillow 可解码亦存为 .webp）。
_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|webp)", re.I)
# UI 噪声关键词：命中则视为图标/装饰而非内容图，跳过。
_UI_SKIP = (
    "icon", "logo", "avatar", "emoji", "banner", "ad", "btn", "sprite",
    "bg", "arrow", "nav", "footer", "header", "qrcode", "wechat", "badge",
    "loading", "placeholder", "default", "thumb_s", "-gray", "avatar",
    "play", "ico", "pixel", "counter", "stat",
)


class GenericHtmlImageScraper(_SearchImageScraper):
    """通用中文网页图片抽取源。子类仅需设置 name / search_url_tpl /（可选）host_hint。"""

    lang = "zh"
    is_authorized = False
    source_kind = SOURCE_KIND_UNAUTHORIZED
    search_suffixes = None  # 搜索页 GET 仅做 https 校验（host 不可枚举）
    download_headers = {"User-Agent": _UA, "Referer": "https://www.baidu.com/"}
    search_url_tpl: str = ""          # 子类设置，需含 {q} 占位（已 urlencode）
    host_hint: tuple = ()             # 可选：优先保留命中这些 host 的内容图

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return self.search_url_tpl.format(q=quote(q))

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        urls: List[str] = []
        for tag in _IMG_TAG_RE.findall(text):
            m = _IMG_ATTR_RE.search(tag)
            if m:
                u = m.group(2).strip().replace("&amp;", "&")
                if u.lower().startswith("http"):
                    urls.append(u)
        for m in _SRCSET_RE.finditer(text):
            for part in m.group(1).split(","):
                u = part.strip().split()[0] if part.strip() else ""
                u = u.replace("&amp;", "&")
                if u.lower().startswith("http") and _EXT_RE.search(u):
                    urls.append(u)

        seen: set = set()
        out: List[Dict[str, Any]] = []
        # 若给定 host_hint，先放命中的，再放其余，保证内容图优先。
        ordered = sorted(urls, key=lambda u: 0 if any(h in u.lower() for h in self.host_hint) else 1) \
            if self.host_hint else urls
        for u in ordered:
            if not _EXT_RE.search(u):
                continue
            low = u.lower()
            if any(k in low for k in _UI_SKIP):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "w": None, "h": None, "page": ""})
            if len(out) >= 40:
                break
        return out


def _zh_page_headers(ref: str) -> dict:
    return {
        "User-Agent": _UA,
        "Referer": ref,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


class CctvAdapter(GenericHtmlImageScraper):
    name = "cctv"
    search_url_tpl = "https://search.cctv.com/search?q={q}"
    host_hint = ("cctv.com",)
    download_headers = _zh_page_headers("https://www.cctv.com/")


class SogouTukuAdapter(GenericHtmlImageScraper):
    name = "sogou_tuku"
    search_url_tpl = "https://pic.sogou.com/pics?query={q}"
    host_hint = ("sogoucdn.com", "sogou.com")
    download_headers = _zh_page_headers("https://pic.sogou.com/")


class ZcoolAdapter(GenericHtmlImageScraper):
    name = "zcool"
    search_url_tpl = "https://www.zcool.com.cn/search/content?word={q}"
    host_hint = ("zcool.cn",)
    download_headers = _zh_page_headers("https://www.zcool.com.cn/")


class ChinanewsAdapter(GenericHtmlImageScraper):
    name = "chinanews"
    search_url_tpl = "https://www.chinanews.com.cn/sousuo/?q={q}"
    host_hint = ("chinanews.com.cn",)
    download_headers = _zh_page_headers("https://www.chinanews.com.cn/")


class PeopleAdapter(GenericHtmlImageScraper):
    name = "people"
    search_url_tpl = "https://search.people.com.cn/cnpeople/news?q={q}"
    host_hint = ("people.com.cn",)
    download_headers = _zh_page_headers("https://search.people.com.cn/")


class HuabanAdapter(GenericHtmlImageScraper):
    name = "huaban"
    search_url_tpl = "https://huaban.com/search/?q={q}"
    host_hint = ("huaban.com",)
    download_headers = _zh_page_headers("https://huaban.com/")


class HuabanApiAdapter(GenericHtmlImageScraper):
    """花瓣：api.huaban.com JSON 接口（比 HTML 抽取更稳，服务端渲染）。"""

    name = "huaban_api"
    search_suffixes = None
    host_hint = ("huaban.com",)
    download_headers = _zh_page_headers("https://huaban.com/")

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return f"https://api.huaban.com/search?q={quote(q)}&limit=20"

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        pins = (data.get("pins") or data.get("data") or [])
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for p in pins:
            f = p.get("file") or {}
            key = f.get("key")
            if not key:
                continue
            u = "https://hbimg.huaban.com/" + key
            if u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "w": f.get("width"), "h": f.get("height"), "page": ""})
            if len(out) >= 40:
                break
        return out


class ZhihuAdapter(GenericHtmlImageScraper):
    name = "zhihu"
    search_url_tpl = "https://www.zhihu.com/search?type=content&q={q}"
    host_hint = ("zhimg.com",)
    download_headers = _zh_page_headers("https://www.zhihu.com/")


class SinaAdapter(GenericHtmlImageScraper):
    name = "sina"
    search_url_tpl = "https://search.sina.com.cn/?q={q}&range=all"
    host_hint = ("sinaimg.cn", "sina.com.cn")
    download_headers = _zh_page_headers("https://www.sina.com.cn/")


class SohuAdapter(GenericHtmlImageScraper):
    name = "sohu"
    search_url_tpl = "https://search.sohu.com/?keyword={q}"
    host_hint = ("sohu.com",)
    download_headers = _zh_page_headers("https://www.sohu.com/")


def _filter_img_urls(urls: List[str], host_hint: tuple, cap: int = 40,
                     exclude: tuple = ()) -> List[Dict[str, Any]]:
    """通用图片 URL 清洗：过滤 UI 噪声、去重、host_hint 内容图优先，截断到 cap。
    exclude：命中即丢弃的子串（如头条签名图床 toutiaoimg.com 普遍 403 防盗链，仅 byteimg/
    douyinpic 可下）。"""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    ordered = sorted(urls, key=lambda u: 0 if any(h in u.lower() for h in host_hint) else 1) \
        if host_hint else urls
    for u in ordered:
        if not _EXT_RE.search(u):
            continue
        if u.lower().startswith("http://"):
            u = "https://" + u[u.find("://") + 3:]
        low = u.lower()
        if exclude and any(k in low for k in exclude):
            continue
        if any(k in low for k in _UI_SKIP):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "w": None, "h": None, "page": ""})
        if len(out) >= cap:
            break
    return out


class ToutiaoAdapter(GenericHtmlImageScraper):
    """今日头条图片搜索（so.toutiao.com）。服务端渲染，结果含 byteimg / toutiaoimg /
    douyinpic CDN 的图片 URL；用全文本正则抽取（含内联 JSON 里的图链），比仅扫 <img> 更全。
    仅保留 byteimg.com / douyinpic.com（toutiaoimg.com 为签名图床，普遍 403 防盗链）。"""

    name = "toutiao"
    search_url_tpl = ("https://so.toutiao.com/search?keyword={q}"
                      "&source=input&traffic_source=web_search_tab")
    host_hint = ("byteimg.com", "douyinpic.com")
    download_headers = _zh_page_headers("https://so.toutiao.com/")

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        urls = re.findall(r"https?://[^\s\"'<>]+\.(?:jpg|jpeg|png|webp)", text, re.I)
        return _filter_img_urls(urls, self.host_hint, exclude=("toutiaoimg.com",))


class DuitangAdapter(GenericHtmlImageScraper):
    """堆糖：napi JSON 接口（比 HTML 抽取更稳）。"""

    name = "duitang"
    search_suffixes = None
    host_hint = ("duitang.com",)
    download_headers = _zh_page_headers("https://www.duitang.com/")

    def build_url(self, q: str) -> str:
        from urllib.parse import quote
        return ("https://www.duitang.com/napi/blog/list/by_search/"
                f"?kw={quote(q)}&start=0&limit=20")

    def parse_items(self, text: str, q: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        objs = (data.get("data") or {}).get("object_list") or []
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for it in objs:
            ph = (it.get("photo") or {}).get("path")
            if not ph:
                continue
            u = "https://img.duitang.com/uploads/blog/" + ph
            if u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "w": None, "h": None, "page": ""})
            if len(out) >= 40:
                break
        return out


register(CctvAdapter())
register(SogouTukuAdapter())
register(ZcoolAdapter())
register(ChinanewsAdapter())
register(PeopleAdapter())
register(HuabanAdapter())
register(HuabanApiAdapter())
register(ZhihuAdapter())
register(SinaAdapter())
register(SohuAdapter())
register(DuitangAdapter())
register(ToutiaoAdapter())
