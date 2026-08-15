"""百度图片来源适配器（未授权来源，docs 扩种中文源需求）。

⚠️ 重要声明（务必阅读）：
- 百度图片结果【非 CC / 无明确授权】，抓取与再利用存在 ToS 与法律/版权风险。
- 单流模式下，本适配器与授权源统一落盘到同一 images_dir、写入同一清单；每张图保留
  source=baidu / license_raw=未知 / source_authorized=False，下游可按 source_authorized
  切分出纯 CC 子集，或整体作为带 provenance 的图集使用。
- 百度有反爬与防盗链：下载带 Referer: https://image.baidu.com/，但仍可能偶发 403/限流。

检索：image.baidu.com/search/acjson（resultjson_com 格式），取 middleURL/thumbURL。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..models import (
    Candidate,
    SOURCE_KIND_UNAUTHORIZED,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_json_cookie
from .base import SourceAdapter, register


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SEARCH_URL = "https://image.baidu.com/search/acjson"
# 检索端点 host 白名单（下载时未授权源走 https-only，不依赖此字段）。
SEARCH_SUFFIXES = ("image.baidu.com", "baidu.com")
# 主页预热：百度反爬要求携带 BAIDUID cookie（来自 www.baidu.com），否则 acjson 返回
# antiFlag:1 "Forbid spider access"。先 GET 首页预热 cookie，再带浏览器头请求 acjson。
WARMUP_URL = "https://www.baidu.com/"
WARMUP_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
# acjson 走 XHR：必须浏览器 UA + 来源 Referer + X-Requested-With，否则触发反爬。
SEARCH_HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://image.baidu.com/",
    "X-Requested-With": "XMLHttpRequest",
}
DOWNLOAD_HEADERS = {
    "Referer": "https://image.baidu.com/",
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    "User-Agent": _UA,
}


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def _pick_url(item: Dict[str, Any]) -> str:
    """优先 middleURL（较大且多为明文 https），回退 thumbURL / hoverURL。
    不使用 objURL（Baidu 加密，解码不稳定）。"""
    for key in ("middleURL", "thumbURL", "hoverURL", "objURL"):
        u = (item.get(key) or "").strip()
        if u and u.lower().startswith("http"):
            return u
    return ""


_WH_RE = re.compile(r"[?&](?:w|width)=(\d+)[^&]*&?(?:h|height)=(\d+)|[?&](?:h|height)=(\d+)[^&]*&?(?:w|width)=(\d+)")


def _dims_from_url(url: str):
    """Baidu 的 middleURL/thumbURL 在查询串里带真实服务尺寸（?w=800&h=1421）。
    用真实服务尺寸作为 declared/orig 宽高，避免与 acjson 的【原图】宽高（常更大）
    不符导致下载器误判"尺寸偏差过大"。取不到则返回 (None, None)。"""
    m = _WH_RE.search(url)
    if not m:
        return None, None
    if m.group(1):
        return int(m.group(1)), int(m.group(2))
    return int(m.group(3)), int(m.group(4))


class BaiduAdapter(SourceAdapter):
    name = "baidu"
    source_kind = SOURCE_KIND_UNAUTHORIZED
    allowed_suffixes = SEARCH_SUFFIXES
    lang = "zh"
    is_authorized = False
    download_headers = DOWNLOAD_HEADERS
    search_timeout = 15
    search_max_retries = 1

    _fail_streak = 0          # 连续检索失败计数（与 pipeline 慢源/坏源剔除同口径）
    _FAIL_STREAK_EVICT = 3    # 连续失败满此次数即要求 pipeline 剔除本来源

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        q = job.zh_query or job.en_query
        limit = min(60, max(20, (cfg.target_count or 4) * 6))
        try:
            data = fetch_json_cookie(
                SEARCH_URL,
                warmup_url=WARMUP_URL,
                warmup_headers=WARMUP_HEADERS,
                allowed_suffixes=SEARCH_SUFFIXES,
                params={
                    "tn": "resultjson_com",
                    "ipn": "rj",
                    "ct": "201326592",
                    "is": "",
                    "fp": "result",
                    "cl": "2",
                    "lm": "-1",
                    "ie": "utf-8",
                    "oe": "utf-8",
                    "word": q,
                    "pn": "0",
                    "rn": str(limit),
                },
                headers=SEARCH_HEADERS,
                timeout=self.search_timeout,
                max_retries=self.search_max_retries,
            )
        except Exception as e:  # noqa: BLE001
            # 反爬/限流/网络异常：优雅降级，返回空候选（不中断整体任务）；
            # 连续失败则升级为 NotImplementedError，由 pipeline 立即剔除（同坏源剔除规则）。
            self._fail_streak += 1
            if self._fail_streak >= self._FAIL_STREAK_EVICT:
                raise NotImplementedError(
                    f"连续 {self._fail_streak} 次检索失败（解析/反爬/网络），本次: {e}")
            print(f"[warn] baidu 检索失败（{job.tag}）: {e}")
            return []
        self._fail_streak = 0

        items = (data or {}).get("data") or []
        out = []
        seen = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            url = _pick_url(it)
            if not url:
                continue
            w, h = _dims_from_url(url)
            if url in seen:
                continue
            seen.add(url)
            out.append({
                "_url": url,
                "_w": w, "_h": h,
                "_title": (it.get("fromPageTitle") or it.get("fromTitle") or ""),
                "_page": (it.get("fromURL") or it.get("pageURL") or ""),
                "_size": _to_int(it.get("di")),
            })
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        w, h = raw.get("_w"), raw.get("_h")
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=raw["_url"],
            tag=job.tag,
            query=job.zh_query or job.en_query,
            query_lang="zh",
            landing_url=raw.get("_page") or "",
            content_url=raw["_url"],
            declared_mime=None,  # 百度不返回 MIME，下载后由 Pillow 复验
            declared_width=w or None,
            declared_height=h or None,
            declared_size=raw.get("_size") or None,
            # 用 URL 内真实服务尺寸作为 orig_width/orig_height，使选图按真实宽度分桶；
            # 百度 middleURL 多为 ~800px（thumbURL 更小），原图真实宽高由此反映。
            orig_width=w or None,
            orig_height=h or None,
            author=None,
            credit=None,
            license_raw="未知(百度图片,未授权)",
            source_authorized=False,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(BaiduAdapter())
