"""统一 Candidate 模型（对应 docs/多模态图片采集系统_需求与设计.md 第 5 节）。

四个来源都必须投影为统一 Candidate，至少包含：
source / source_kind / 稳定资产 ID / 命中标签 / Query / landing_url /
content_url / 声明 MIME·宽高·大小 / 作者 / Credit / 许可证原始声明 /
来源响应证据。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# 来源类型（文档第 5 节 source_kind）
SOURCE_KIND_CATALOG = "目录"          # Wikimedia Commons
SOURCE_KIND_DATASET = "数据集"        # Open Images
SOURCE_KIND_COMMUNITY = "领域社区"    # iNaturalist
SOURCE_KIND_SEARCH = "搜索引擎"       # 通用图片搜索引擎


# 候选状态
STATUS_CANDIDATE = "candidate"        # 阶段一：仅元数据
STATUS_ACCEPTED = "accepted"          # 通过筛选，待下载
STATUS_REJECTED = "rejected"          # 筛选拒绝
STATUS_DOWNLOADED = "downloaded"      # 下载并复验成功
STATUS_FAILED = "failed"              # 下载/复验失败


@dataclass
class Candidate:
    # --- 统一 Candidate 字段（文档第 5 节）---
    source: str                       # 来源名称：Wikimedia / OpenImages / iNaturalist / SearchEngine
    source_kind: str                  # 来源类型：目录 / 数据集 / 领域社区 / 搜索引擎
    asset_id: str                     # 来源内稳定唯一标识（page ID / Image ID / photo ID / SearchLead 资产 ID）
    tag: str                          # 命中标签
    query: str                        # 实际使用的检索词
    landing_url: str                  # 来源落地页
    content_url: str                  # 原图内容 URL
    declared_mime: Optional[str] = None
    declared_width: Optional[int] = None
    declared_height: Optional[int] = None
    declared_size: Optional[int] = None
    author: Optional[str] = None      # 来源声明的作者
    credit: Optional[str] = None      # 来源声明的署名信息
    license_raw: Optional[str] = None # 许可证原始声明（原样保存，不推断版本）
    evidence: Any = field(default=None, repr=False)  # 来源 API / 页面的原始响应（审计用）

    # --- 采集过程字段 ---
    status: str = STATUS_CANDIDATE
    fetched_at: float = field(default_factory=time.time)
    reject_reason: Optional[str] = None
    fail_reason: Optional[str] = None
    sha256: Optional[str] = None
    local_path: Optional[str] = None
    actual_mime: Optional[str] = None
    actual_width: Optional[int] = None
    actual_height: Optional[int] = None
    actual_size: Optional[int] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # evidence 可能含非 JSON 安全对象（如 requests.Response），统一序列化为 str
        if not _json_safe(self.evidence):
            d["evidence"] = _stringify(self.evidence)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def _json_safe(obj: Any) -> bool:
    if obj is None or isinstance(obj, (str, int, float, bool, list, dict)):
        return True
    return False


def _stringify(obj: Any) -> Any:
    if isinstance(obj, (dict, list)):
        try:
            return json.dumps(obj, ensure_ascii=False)
        except TypeError:
            return str(obj)
    return str(obj)


def write_jsonl(path: str, candidates: list[Candidate]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[Candidate]:
    out: list[Candidate] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(Candidate.from_dict(json.loads(line)))
    return out
