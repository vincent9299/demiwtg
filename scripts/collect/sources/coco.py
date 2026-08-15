"""COCO 官方标注适配器（方案 A·API 直连）。

数据形态：COCO 2017 官方标注 zip（instances_*.json）自带 80 个类别的人工标注，
是"类别词表型"开源数据集中唯一官方索引仍可匿名下载的（OpenImages 官方
images.csv 自 2024 年起匿名访问 403）。按 job 的英文 query 精确匹配类别名，
命中则返回该类别下的图片（官方图床 URL）。

授权语义：COCO 图片源自 Flickr，标注中每张图带 license 编号（1–8），
LICENSE_MAP 映射为 CC 系名称后交给 filterer 的 license_allowlist 过滤；
Flickr ToS / 美国政府版权（7/8）不在白名单内会自然被拒。
注意 COCO 数据集条款限定非商业研究用途，下游商用需自行评估。

缓存：标注 zip 与解析后的类别索引缓存于 <MM_INDEX_DIR>/coco/。官方仅提供
annotations_trainval2017.zip（约 241MB，含 train+val），两个 split 共享该 zip，
首次运行下载后按需解出对应 instances_*.json。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import zipfile
from typing import Dict, List, Optional

from ..models import (
    Candidate,
    SOURCE_KIND_DATASET,
    STATUS_CANDIDATE,
)
from ..config import Job
from .base import SourceAdapter, register

ANNOT_HOST_SUFFIXES = ("images.cocodataset.org",)
ANNOT_BASE = "http://images.cocodataset.org/annotations"  # 官方图床 https 通道被限时，索引下载允许 http
IMAGES_BASE = "https://images.cocodataset.org"             # 图片 URL 恒 https（filterer 要求）

# COCO 标注 license 编号 → CC 系名称（与 filterer license_allowlist 子串匹配对齐）
LICENSE_MAP = {
    1: "CC BY-NC-SA",
    2: "CC BY-NC",
    3: "CC BY-NC-ND",
    4: "CC BY",
    5: "CC BY-SA",
    6: "CC BY-ND",
    7: "Flickr Terms of Use (no known copyright restrictions)",
    8: "United States Government Work",
}
CC_LICENSE_IDS = {1, 2, 3, 4, 5, 6}


def _index_dir() -> str:
    return os.environ.get("MM_INDEX_DIR", os.path.join("state", "dataset_index"))


def _split() -> str:
    s = os.environ.get("MM_COCO_SPLIT", "val2017").strip()
    return s if s in ("val2017", "train2017") else "val2017"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _download(url: str, dest: str, timeout: int = 60) -> None:
    """流式下载到 dest（带 .part 中转，避免半截文件被当缓存）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "multimodal-collector/1.0"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)


def _build_index(split: str) -> dict:
    """下载标注 zip（缺缓存时）并构建 {类别归一名: [图片条目]} 索引。"""
    cache_dir = os.path.join(_index_dir(), "coco")
    os.makedirs(cache_dir, exist_ok=True)
    index_path = os.path.join(cache_dir, f"index_{split}.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            return json.load(f)

    zip_path = os.path.join(cache_dir, "annotations_trainval2017.zip")
    if not os.path.exists(zip_path):
        zip_url = f"{ANNOT_BASE}/annotations_trainval2017.zip"
        print(f"[coco] 下载标注 {zip_url}（首次，约 241MB）")
        _download(zip_url, zip_path, timeout=300)
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(f"annotations/instances_{split}.json") as f:
            annot = json.load(f)

    images = {im["id"]: im for im in annot.get("images") or []}
    index: Dict[str, list] = {}
    for ann in annot.get("annotations") or []:
        im = images.get(ann.get("image_id"))
        cat = next((c for c in annot.get("categories") or []
                    if c.get("id") == ann.get("category_id")), None)
        if not im or not cat:
            continue
        key = _norm(cat.get("name") or "")
        if not key:
            continue
        index.setdefault(key, []).append({
            "image_id": im["id"],
            "file_name": im.get("file_name") or "",
            "width": im.get("width"),
            "height": im.get("height"),
            "license": im.get("license"),
            "flickr_url": im.get("flickr_url") or "",
        })
    # 去重（一图多实例）+ 按 image_id 稳定排序
    for key, rows in index.items():
        seen, dedup = set(), []
        for r in sorted(rows, key=lambda x: x["image_id"]):
            if r["image_id"] not in seen:
                seen.add(r["image_id"])
                dedup.append(r)
        index[key] = dedup
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    print(f"[coco] 索引就绪：{len(index)} 类 / {sum(len(v) for v in index.values())} 图 -> {index_path}")
    return index


class CocoAdapter(SourceAdapter):
    name = "coco"
    source_kind = SOURCE_KIND_DATASET
    allowed_suffixes = ANNOT_HOST_SUFFIXES
    lang = "en"
    is_authorized = True

    _index: Optional[dict] = None

    def search(self, job: Job) -> List[dict]:
        cfg = job.effective
        if self._index is None:
            self.__class__._index = _build_index(_split())
        q = _norm(job.en_query)
        if not q:
            return []
        # 精确类别匹配 + 复数变形
        keys = {q}
        if q.endswith("s"):
            keys.add(q[:-1])
        else:
            keys.add(q + "s")
        rows: List[dict] = []
        for key in keys:
            for r in self._index.get(key, []):
                rows.append({**r, "_cat": key})
        # 每标签最多取 target_count*4 张候选（下载阶段另有 max_per_source 封顶）
        cap = max(4, (cfg.target_count or 4) * 4)
        return rows[:cap]

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        lic_id = raw.get("license")
        lic_name = LICENSE_MAP.get(lic_id, f"未知(license={lic_id})")
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=str(raw.get("image_id")),
            tag=job.tag,
            query=job.en_query,
            query_lang="en",
            landing_url=raw.get("flickr_url") or "",
            content_url=f"{IMAGES_BASE}/{_split()}/{raw.get('file_name')}",
            declared_mime="image/jpeg",
            declared_width=raw.get("width"),
            declared_height=raw.get("height"),
            declared_size=None,
            author=None,
            credit=None,
            license_raw=lic_name,
            source_authorized=lic_id in CC_LICENSE_IDS,
            evidence={"coco_category": raw.get("_cat"), "license_id": lic_id},
            status=STATUS_CANDIDATE,
        )


register(CocoAdapter())
