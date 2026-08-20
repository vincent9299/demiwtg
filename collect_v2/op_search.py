"""collect_v2 检索算子：输入 (种子, 源) → 输出有界有序候选列表。

契约（.qoder/handoff_collect_v2.md §3.1 / §4.1）：
- 只收域路由之后的 (种子, 源) 对，本文件不做域路由；
- 输出按源原生相关度排序的候选列表，adapter 不重排、不筛选、不凑数；
- K 封顶不分页深翻：语义/爬虫源 ≤5，结构化源 10-20；
- 列表不足或为空原样返回，认缺是链层的事；
- adapter 只产结构化候选，不碰主清单；所有请求走 infra.request。

数据流（用户拍板）：算子链是数据算子流，全链路流转统一的 Item 记录
（类似 Ray Dataset 的行），各算子在 Item 上追加自己的产出字段，
不设独立的 Candidate/DownloadResult 类型。

本期代表源：wikimedia_zh（官方 API 档）、baidu（爬虫档）。
英文/拉丁源待别名清洗落盘后启用。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from collect_v2 import infra

K_SEMANTIC = 5        # 语义检索源（wikimedia/搜索爬虫）K 封顶
K_STRUCTURED = 15     # 结构化源（inaturalist 等）K 封顶，后续源启用时生效


def _int_or_none(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# UA 策略（实网实测结论）：
# - 官方 API 档：Wikimedia robot policy 要求可识别调用方与可联系方式，否则 403；
#   占位邮箱（example.com）会在下载层被拦，真实仓库 URL 实测放行（用户拍板用仓库首页）；
# - 爬虫档：自报机器人身份会被拦（百度 antiFlag "Forbid spider access"），用常规浏览器 UA。
API_UA = ("collect-v2/0.1 (research image collection; "
          "https://github.com/vincent9299/demiwtg) httpx/0.28")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# 数据结构（算子链统一流转的 Item）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Seed:
    """检索种子：域路由之后的 (实例名, 源) 里的种子部分。

    由 op_seed 产出：中文本体 seed（lang="zh"，query 即实例名）与
    西文投影 seed（lang="latin"，query 为 LLM 判定的同实体西文名）。
    """
    name: str                        # 实例名
    query: Optional[str] = None      # 真实检索词，缺省即实例名（透传给 sink）
    lang: str = "zh"                 # 种子语言形态：zh / latin（op_seed 判定）


@dataclass
class Item:
    """算子链统一流转的数据记录（数据算子流，类似 Ray Dataset 的行）。

    各算子只追加自己的产出字段，不改写上游字段；字段缺失即 None：
    - op_seed 产：种子（instance/query/lang，见 Seed）；
    - op_search 产：instance/query/lang/source/rank/content_url/landing_url/
      declared_width/declared_height/mime/license/author/native；
    - op_download 追加：data/sha256/ext/actual_width/actual_height/size_bytes；
    - op_annotate 追加：kb_match/richness/caption/identity（失败则全部为 None）；
    - op_sink 追加：local_path/fetched_at（落盘成功才有值）。
    """
    # 种子（域路由后的实例与真实检索词，query 禁止回落造假）
    instance: str
    query: str
    lang: str = "zh"    # 种子语言形态 zh/latin，sink 写 query_langs 用
    # 检索产出（声明尺寸常失真，实际尺寸以下载解码为准）
    source: str = ""
    rank: int = 0
    content_url: Optional[str] = None
    landing_url: Optional[str] = None
    declared_width: Optional[int] = None
    declared_height: Optional[int] = None
    mime: Optional[str] = None
    license: Optional[str] = None
    author: Optional[str] = None
    native: dict = field(default_factory=dict)   # 源原生元数据原样保留
    # 下载产出
    data: Optional[bytes] = None
    sha256: Optional[str] = None
    ext: Optional[str] = None
    actual_width: Optional[int] = None
    actual_height: Optional[int] = None
    size_bytes: Optional[int] = None
    # 标注产出
    kb_match: Optional[int] = None
    richness: Optional[int] = None
    caption: Optional[str] = None
    identity: Optional[bool] = None
    # 落盘产出
    local_path: Optional[str] = None   # blobs/<aa>/<sha>.<ext>，相对 data/dataset/
    fetched_at: Optional[float] = None


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------

class SearchAdapter:
    """检索源适配器基类：一源一类，只产 Item 不碰主清单。"""

    source: str = ""
    k_cap: int = K_SEMANTIC

    async def search(
        self,
        seed: Seed,
        k: int,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> list[Item]:
        raise NotImplementedError


class WikimediaZhAdapter(SearchAdapter):
    """维基共享资源（中文检索词）：打 commons.wikimedia.org 媒体库本体（旧系统验证过的端点），
    generator=search 只搜文件命名空间。"""

    source = "wikimedia_zh"
    k_cap = K_SEMANTIC
    _API = "https://commons.wikimedia.org/w/api.php"

    async def search(self, seed, k, *, client=None):
        k = min(k, self.k_cap)
        query = seed.query or seed.name
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",        # 文件命名空间
            "gsrlimit": str(k),
            "prop": "imageinfo|pageprops",
            "iiprop": "url|size|mime|extmetadata",
            "ppprop": "canonicalurl",
        }
        resp = await infra.request(
            self.source, "GET", self._API, client=client,
            params=params, headers={"User-Agent": API_UA},
        )
        pages = (resp.json().get("query") or {}).get("pages") or {}
        # API 返回 dict，index 字段即相关度序；排序后取前 k
        ordered = sorted(pages.values(), key=lambda p: int(p.get("index", 0)))
        out: list[Item] = []
        for rank, page in enumerate(ordered[:k]):
            info = (page.get("imageinfo") or [{}])[0]
            ext = info.get("extmetadata") or {}

            def _ext(key: str) -> Optional[str]:
                v = ext.get(key)
                return v.get("value") if isinstance(v, dict) else None

            props = page.get("pageprops") or {}
            out.append(Item(
                instance=seed.name,
                query=query,
                lang=getattr(seed, "lang", "zh"),
                source=self.source,
                rank=rank,
                content_url=info.get("url"),
                landing_url=props.get("canonicalurl") or info.get("descriptionurl"),
                declared_width=info.get("width"),
                declared_height=info.get("height"),
                mime=info.get("mime"),
                license=_ext("LicenseShortName"),
                author=_ext("Artist"),
                native={
                    "page_title": page.get("title"),
                    "page_id": page.get("pageid"),
                    "mediatype": info.get("mediatype"),
                },
            ))
        return out


class BaiduAdapter(SearchAdapter):
    """百度图片 acjson 接口（爬虫档）。

    纯业务经验来自旧系统（_reference/old_repo/collect/sources/baidu.py）：
    - 无会话 cookie 直接调 acjson 会被 antiFlag 拦截，需先预热拿 BAIDUID；
    - objURL 为混淆编码且解码不稳定，**不用**；优先 middleURL（明文 https、较大），
      回退 thumbURL/hoverURL；
    - acjson 的 width/height 是原图尺寸，与 middleURL 实际服务尺寸常不符，
      声明尺寸改从 URL 查询串 ?w=&h= 提取；
    - 非 JSON 应答按瞬态失败走 infra 重试。
    """

    source = "baidu"
    k_cap = K_SEMANTIC
    _API = "https://image.baidu.com/search/acjson"
    _HOME = "https://www.baidu.com/"
    _warmed = False

    async def _warmup(self, client: Optional[httpx.AsyncClient]) -> None:
        """预热拿会话 cookie（BAIDUID），失败不阻断，留给正式请求自行暴露。"""
        if BaiduAdapter._warmed:
            return
        http = client or infra.get_client()
        try:
            await http.get(self._HOME, headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
        except httpx.HTTPError:
            return
        BaiduAdapter._warmed = True

    @staticmethod
    def _pick_url(it: dict) -> Optional[str]:
        """middleURL 优先（明文且较大），回退 thumbURL/hoverURL；不用 objURL（加密）。"""
        for key in ("middleURL", "thumbURL", "hoverURL"):
            u = (it.get(key) or "").strip()
            if u and u.lower().startswith("http"):
                return u
        return None

    @staticmethod
    def _dims_from_url(url: str) -> tuple[Optional[int], Optional[int]]:
        """百度 CDN URL 查询串带真实服务尺寸（?w=500&h=889），比 acjson 原图尺寸可信。"""
        mw = re.search(r"[?&]w=(\d+)", url)
        mh = re.search(r"[?&]h=(\d+)", url)
        if mw and mh:
            return int(mw.group(1)), int(mh.group(1))
        return None, None

    async def search(self, seed, k, *, client=None):
        k = min(k, self.k_cap)
        query = seed.query or seed.name
        await self._warmup(client)
        params = {
            "tn": "resultjson_com",
            "ipn": "rj",
            "ct": "201326592",
            "fp": "result",
            "word": query,
            "queryWord": query,
            "rn": str(k),
            "pn": "0",
            "ie": "utf-8",
        }
        resp = await infra.request(
            self.source, "GET", self._API, client=client,
            params=params,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://image.baidu.com/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # 反爬页/空壳应答：按瞬态失败上抛，由 infra 分类重试语义兜住
            raise infra.TransientExhaustedError(
                f"baidu 检索应答非 JSON（疑似反爬页）: {query}"
            ) from exc
        if data.get("antiFlag"):
            # 源明确拦截（如 "Forbid spider access"）：重试无意义，确定性失败认缺
            raise infra.DeterministicError(
                f"baidu 反爬拦截: {data.get('message')!r} query={query}"
            )
        items = data.get("data") or []
        out: list[Item] = []
        seen: set[str] = set()
        rank = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            content_url = self._pick_url(it)
            if not content_url or content_url in seen:
                continue  # 无图址/重复的空壳条目，不筛选内容
            seen.add(content_url)
            w, h = self._dims_from_url(content_url)
            out.append(Item(
                instance=seed.name,
                query=query,
                lang=getattr(seed, "lang", "zh"),
                source=self.source,
                rank=rank,
                content_url=content_url,
                landing_url=it.get("fromURL") or it.get("hoverURL"),
                declared_width=w,
                declared_height=h,
                mime=None,   # 百度不返回 MIME，下载后由解码实测补齐
                license=None,
                author=None,
                native={
                    "from_page_title": it.get("fromPageTitleEnc"),
                    "from_url": it.get("fromURL"),
                    "orig_width": _int_or_none(it.get("width")),
                    "orig_height": _int_or_none(it.get("height")),
                    "size_bytes": _int_or_none(it.get("di")),
                },
            ))
            rank += 1
            if rank >= k:
                break
        return out


# ---------------------------------------------------------------------------
# 注册表与分派
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, SearchAdapter] = {}


def register(adapter: SearchAdapter) -> None:
    _ADAPTERS[adapter.source] = adapter


register(WikimediaZhAdapter())
register(BaiduAdapter())


async def search(
    seed: Seed,
    source: str,
    k: int = K_SEMANTIC,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[Item]:
    """对 source 检索 seed，返回有界有序 Item 列表（可能为空 = 认缺）。"""
    adapter = _ADAPTERS.get(source)
    if adapter is None:
        raise ValueError(f"源 {source!r} 未注册 adapter")
    return await adapter.search(seed, k, client=client)
