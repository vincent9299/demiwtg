"""来源适配器抽象基类与注册表。

每个来源实现：
- search(job) -> list[dict]   返回 source-specific 原始条目
- to_candidate(raw, job) -> Candidate  投影为统一 Candidate（docs 第 5 节）
- allowed_suffixes            HTTP 校验允许的主机后缀（用于下载 content_url）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from ..models import Candidate


class SourceAdapter(ABC):
    name: str = "base"
    source_kind: str = ""
    allowed_suffixes: tuple = ()

    @abstractmethod
    def search(self, job) -> List[dict]:
        ...

    @abstractmethod
    def to_candidate(self, raw: dict, job) -> Candidate:
        ...


_REGISTRY: Dict[str, SourceAdapter] = {}


def register(adapter: SourceAdapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get_adapter(name: str) -> SourceAdapter:
    if name not in _REGISTRY:
        raise KeyError(f"未知来源: {name}（已注册：{sorted(_REGISTRY)}）")
    return _REGISTRY[name]
