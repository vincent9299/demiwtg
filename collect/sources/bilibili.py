"""Bilibili 相簿（h.bilibili.com）来源适配器（未授权中文源）。

走 B 站搜索 API 的 photo 类型（wbi 签名）：对虚拟偶像/潮玩/ACG 圈层覆盖好，
补中文源在「虚拟偶像/数字人、潮玩商品角色」等垂直内容的盲区。

- 接口：/x/web-interface/wbi/search/type?search_type=photo（需 wbi 签名）；
- wbi 密钥从 /x/web-interface/nav 拉取（img_key+sub_key → mixinKeyEncTab 乱序）；
- 未登录可用但命中数受限；风控（-352）时优雅降级返回空；
- 图 URL host 为 i*.hdslb.com / *.biliimg.com，下载带 Referer 防盗链校验。

实现 best-effort：解析失败/空结果返回空候选，不中断整体任务。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
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

_ALLOWED = ("bilibili.com", "hdslb.com", "biliimg.com")

# wbi 乱序表（官方 mixinKeyEncTab）：对 img_key+sub_key 的 64 位 hex 串按此顺序取样。
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_IMG_URL_RE = re.compile(
    r"https?://(?:i\d+\.hdslb\.com|[a-z0-9.-]*biliimg\.com)/[^\s\"'<>\\]+",
    re.I,
)


def _key_of(url: str) -> str:
    return os.path.basename(urllib.parse.urlparse(url or "").path).split(".")[0]


def _mixin_key(img_key: str, sub_key: str) -> str:
    orig = img_key + sub_key
    if len(orig) < 64:
        return ""
    return "".join(orig[i] for i in _MIXIN_TAB)[:32]


def _wbi_sign(params: dict, mixin: str) -> dict:
    p = dict(params)
    p["wts"] = int(time.time())
    clean = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
             for k, v in p.items()}
    query = urllib.parse.urlencode(sorted(clean.items()))
    p["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return p


class BilibiliAdapter(SourceAdapter):
    name = "bilibili"
    source_kind = SOURCE_KIND_UNAUTHORIZED
    allowed_suffixes = _ALLOWED
    lang = "zh"
    is_authorized = False
    search_timeout = 20
    download_headers = {
        "User-Agent": _UA,
        "Referer": "https://h.bilibili.com/",
    }

    def _mixin(self, cfg) -> str:
        nav = fetch_json(
            "https://api.bilibili.com/x/web-interface/nav",
            allowed_suffixes=("bilibili.com",),
            headers={"User-Agent": _UA},
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        wbi = (nav.get("data") or {}).get("wbi_img") or {}
        return _mixin_key(_key_of(wbi.get("img_url") or ""),
                          _key_of(wbi.get("sub_url") or ""))

    def search(self, job: Job) -> List[Dict]:
        q = job.zh_query or job.query
        if not q:
            return []
        cfg = job.effective
        try:
            mixin = self._mixin(cfg)
            if not mixin:
                return []
            params = _wbi_sign({"search_type": "photo", "keyword": q, "page": 1},
                               mixin)
            data = fetch_json(
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                allowed_suffixes=("bilibili.com",),
                params=params,
                headers={"User-Agent": _UA, "Referer": "https://h.bilibili.com/"},
                timeout=cfg.timeout_sec,
                max_retries=cfg.max_retries,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {self.name} 检索失败（{job.instance}）: {e}")
            return []
        if (data.get("code") or 0) != 0:
            print(f"[warn] {self.name} 检索失败（{job.instance}）: "
                  f"code={data.get('code')} {data.get('message')}")
            return []
        out: List[Dict] = []
        for item in ((data.get("data") or {}).get("result") or []):
            if not isinstance(item, dict):
                continue
            if item.get("result_type", item.get("type")) not in ("photo", "cover_article"):
                continue
            blob = json.dumps(item, ensure_ascii=False)
            urls = [u.replace("\\/", "/") for u in _IMG_URL_RE.findall(blob)]
            for u in urls:
                if u in {x["url"] for x in out}:
                    continue
                out.append({
                    "url": u,
                    "w": item.get("width"),
                    "h": item.get("height"),
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "author": ((item.get("author") or {}).get("name")
                               if isinstance(item.get("author"), dict) else None),
                    "page": f"https://h.bilibili.com/opensearch/detail?id={item.get('id')}",
                })
            if len(out) >= 40:
                break
        return out

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        url = raw["url"]
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"bili-{raw.get('id')}",
            instance=job.instance,
            query=job.zh_query or job.query,
            query_lang="zh",
            landing_url=raw.get("page") or "",
            content_url=url,
            declared_mime=None,  # 下载后由 Pillow 复验
            declared_width=raw.get("w") or None,
            declared_height=raw.get("h") or None,
            declared_size=None,
            author=raw.get("author"),
            credit=raw.get("title"),
            license_raw="未知(未授权来源,非CC)",
            source_authorized=False,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(BilibiliAdapter())
