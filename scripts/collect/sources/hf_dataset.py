"""Hugging Face 数据集统一流式适配器（方案 B）。

设计（docs 讨论结论）：一个通用适配器 + 每数据集一份 profile，通过
datasets-server 行级 API（https://datasets-server.huggingface.co/rows）
流式分页读取，按 query 关键词过滤 caption/文本列，命中的行投影为 Candidate。
不落全量、不整包下载；对 URL 型数据集（图文对只含 URL）由现有 downloader 拉原图。

profile 字段：
  name / dataset / text_field / url_field / license_default /
  is_authorized / dl_suffixes（下载 host 白名单）/
  max_rows_scan（单次检索扫描行数上限）/ page_size

已注册 profile：
- hf_coco  : ChristophSchuhmann/MS_COCO_2017_URL_TEXT（59 万行 URL+TEXT，已实测可流式）
- hf_laion : laion/relaion2B-en-research-safe（gated：需 HF_TOKEN 环境变量；
             20 亿行只能浅扫，keyword 召回有限，作扩量兜底）

HF 上的 OpenImages 镜像（dalle-mini/open-images 等）仅有 URL 列表、无文本/标签列，
无法按标签检索，故不注册 profile。

用法：config 的 sources（hf_coco 若按未授权使用则放 unauthorized_sources）加入
对应名称即可；默认不启用，不影响现有配置。
"""

from __future__ import annotations

import os
import re
from typing import List

from ..models import (
    Candidate,
    SOURCE_KIND_DATASET,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_json
from .base import SourceAdapter, register

ROWS_API = "https://datasets-server.huggingface.co/rows"
API_SUFFIXES = ("datasets-server.huggingface.co",)

MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "tiff": "image/tiff", "tif": "image/tiff",
}


def _tokens(q: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (q or "").lower()) if len(t) >= 2]


class HFDatasetAdapter(SourceAdapter):
    source_kind = SOURCE_KIND_DATASET
    lang = "en"

    def __init__(self, profile: dict):
        self.name = profile["name"]
        self.dataset = profile["dataset"]
        self.config_name = profile.get("config", "default")
        self.split = profile.get("split", "train")
        self.text_field = profile["text_field"]
        self.url_field = profile["url_field"]
        self.license_default = profile.get("license_default", "未知")
        self.is_authorized = profile.get("is_authorized", False)
        self.allowed_suffixes = tuple(profile.get("dl_suffixes", ()))
        self.max_rows_scan = profile.get("max_rows_scan", 10000)
        self.page_size = profile.get("page_size", 100)
        self._conn_dead = False  # 连接/授权层熔断：401/403/连接失败后本次运行短路
        self._rows_cache: List[dict] | None = None  # 首检拉取的行缓存，后续标签本地过滤

    def _auth_headers(self) -> dict:
        tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def _fetch_rows(self, offset: int, length: int, timeout: int) -> dict:
        return fetch_json(
            ROWS_API,
            allowed_suffixes=API_SUFFIXES,
            params={
                "dataset": self.dataset,
                "config": self.config_name,
                "split": self.split,
                "offset": str(offset),
                "length": str(length),
            },
            headers=self._auth_headers(),
            timeout=timeout,
            max_retries=2,
        )

    def _scan_rows(self, timeout: int) -> List[dict]:
        """首次调用时拉取前 max_rows_scan 行并缓存；之后所有标签本地过滤，
        避免 N 个标签重复请求同一批页（2000+ 标签下是万次级重复请求）。"""
        if self._rows_cache is not None:
            return self._rows_cache
        rows: List[dict] = []
        offset = 0
        while offset < self.max_rows_scan:
            length = min(self.page_size, self.max_rows_scan - offset)
            try:
                data = self._fetch_rows(offset, length, timeout)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                # 授权层错误熔断（gated/无 token，重试无意义）；超时/5xx 保留已拉部分
                if any(k in msg for k in ("401", "403", "Unauthorized", "Forbidden")):
                    self._conn_dead = True
                    print(f"[warn] {self.name} 熔断，本次运行不再重试: {msg}"
                          "（gated 数据集？请设置 HF_TOKEN 并在数据集页同意条款）")
                else:
                    print(f"[warn] {self.name} 流式读取失败 offset={offset}: {msg}")
                break
            page = data.get("rows") or []
            if not page:
                break
            rows.extend(page)
            offset += len(page)
        self._rows_cache = rows
        return rows

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        if self._conn_dead:
            return []
        toks = _tokens(job.en_query)
        if not toks:
            return []
        hits: List[dict] = []
        hit_cap = max(4, (cfg.target_count or 4) * 3)
        for item in self._scan_rows(max(60, cfg.timeout_sec)):
            row = item.get("row") or {}
            text = str(row.get(self.text_field) or "").lower()
            url = str(row.get(self.url_field) or "")
            if not url or not all(t in text for t in toks):
                continue
            hits.append({
                "_url": url,
                "_text": str(row.get(self.text_field) or ""),
                "_row_idx": item.get("row_idx"),
                "_w": row.get("width"), "_h": row.get("height"),
            })
            if len(hits) >= hit_cap:
                break
        return hits

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        url = raw["_url"]
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]  # filterer 要求 https；图床 https 通道放行后即生效
        ext = (url.rsplit(".", 1)[-1].split("?")[0] or "").lower()
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"{self.dataset}:{raw.get('_row_idx')}",
            tag=job.tag,
            query=job.en_query,
            query_lang="en",
            landing_url=f"https://huggingface.co/datasets/{self.dataset}",
            content_url=url,
            declared_mime=MIME_BY_EXT.get(ext),
            declared_width=raw.get("_w"),
            declared_height=raw.get("_h"),
            declared_size=None,
            author=None,
            credit=None,
            license_raw=self.license_default,
            source_authorized=self.is_authorized,
            evidence={"caption": raw.get("_text"), "dataset": self.dataset},
            status=STATUS_CANDIDATE,
        )


PROFILES = [
    {
        # 59 万行 URL+TEXT 图文对（COCO 2017 五.caption 展开），license 逐行未知 → 按未授权处理
        "name": "hf_coco",
        "dataset": "ChristophSchuhmann/MS_COCO_2017_URL_TEXT",
        "text_field": "TEXT",
        "url_field": "URL",
        "license_default": "COCO 2017 caption pair (license unknown)",
        "is_authorized": False,
        "dl_suffixes": ["images.cocodataset.org"],
        # rows API 冷页可达 ~8s/页；行缓存使全量标签共享一次扫描，扫描上限按
        # 单次成本设置（20 页），命中 hit_cap 只影响过滤不减少缓存行数。
        "max_rows_scan": 2000,
    },
    {
        # LAION 重建版（research-safe）。gated：需 HF_TOKEN。20 亿行只做浅扫兜底。
        "name": "hf_laion",
        "dataset": "laion/relaion2B-en-research-safe",
        "text_field": "TEXT",
        "url_field": "URL",
        "license_default": "LAION (license unknown)",
        "is_authorized": False,
        "dl_suffixes": [],   # 原图分布全网，host 不可枚举 → 走未授权通道（scheme 仍需 https）
        "max_rows_scan": 2000,
        "page_size": 100,
    },
]

for _p in PROFILES:
    register(HFDatasetAdapter(_p))
