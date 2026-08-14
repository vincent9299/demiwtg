"""M2–M4 来源占位（docs 第 9 节交付计划，本期未实现）。

预留 SourceAdapter 接口与注册，便于后续里程碑直接接入，不在 M1 范围。
"""

from __future__ import annotations

from ..models import Candidate
from ..config import Job
from .base import SourceAdapter, register


class _StubMixin:
    def search(self, job: Job):
        raise NotImplementedError(
            f"来源 {self.name} 尚未实现（M1 仅交付 Wikimedia；见 docs 交付计划 M2–M4）"
        )

    def to_candidate(self, raw, job: Job) -> Candidate:
        raise NotImplementedError(f"来源 {self.name} 尚未实现")


class OpenImagesAdapter(_StubMixin, SourceAdapter):
    """OpenImages 官方路线已不可行（2026-08 实测）：images.csv/标注 CSV 匿名访问 403；
    HF 镜像（dalle-mini/open-images 等）仅 URL 列表、无类别/文本列，无法按标签检索。
    通用物体类标签改走 coco / hf_coco。保留占位使旧配置引用不报 KeyError。"""
    name = "openimages"
    source_kind = "数据集"
    allowed_suffixes = ("storage.googleapis.com", "googleapis.com")

    def search(self, job: Job):
        raise NotImplementedError(
            "来源 openimages 不可用：官方 CSV 索引匿名访问已被拒（403），"
            "HF 镜像无标签列；请改用 coco / hf_coco"
        )


class SearchEngineAdapter(_StubMixin, SourceAdapter):
    name = "searchengine"
    source_kind = "搜索引擎"
    allowed_suffixes = ()


register(OpenImagesAdapter())
register(SearchEngineAdapter())
