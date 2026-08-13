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
    name = "openimages"
    source_kind = "数据集"
    allowed_suffixes = ("storage.googleapis.com", "googleapis.com")


class SearchEngineAdapter(_StubMixin, SourceAdapter):
    name = "searchengine"
    source_kind = "搜索引擎"
    allowed_suffixes = ()


register(OpenImagesAdapter())
register(SearchEngineAdapter())
