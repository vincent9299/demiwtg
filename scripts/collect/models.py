"""统一 Candidate 模型（对应采集系统需求与设计（历史文档已归档））。

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
SOURCE_KIND_UNAUTHORIZED = "未授权来源"  # 百度等：非 CC、ToS/法律风险，单独隔离


# 候选状态
STATUS_CANDIDATE = "candidate"        # 阶段一：仅元数据
STATUS_ACCEPTED = "accepted"          # 通过筛选，待下载
STATUS_REJECTED = "rejected"          # 筛选拒绝
STATUS_DOWNLOADED = "downloaded"      # 下载并复验成功
STATUS_FAILED = "failed"              # 下载/复验失败
STATUS_GATE_REJECTED = "gate_rejected"  # 通过基础校验但【实际分辨率】低于阈值，下载阶段拦截（不落盘）


@dataclass
class Candidate:
    # --- 统一 Candidate 字段（文档第 5 节）---
    source: str                       # 来源名称：wikimedia / wikimedia_zh / openverse / baidu
    source_kind: str                  # 来源类型：目录 / 数据集 / 领域社区 / 搜索引擎 / 未授权来源
    asset_id: str                     # 来源内稳定唯一标识（page ID / Image ID / photo ID / SearchLead 资产 ID）
    tag: str                          # 命中标签
    query: str                        # 实际使用的检索词
    landing_url: str                  # 来源落地页
    content_url: str                  # 原图内容 URL（下载原图，不改分辨率）
    query_lang: Optional[str] = None  # 检索词语言：en / zh（用于统计中文源占比）
    declared_mime: Optional[str] = None
    declared_width: Optional[int] = None
    declared_height: Optional[int] = None
    declared_size: Optional[int] = None
    author: Optional[str] = None      # 来源声明的作者
    credit: Optional[str] = None      # 来源声明的署名信息
    license_raw: Optional[str] = None # 许可证原始声明（原样保存，不推断版本）
    source_authorized: bool = True    # 是否授权（CC）；False = 未授权来源，产物隔离
    evidence: Any = field(default=None, repr=False)  # 来源 API / 页面的原始响应（审计用）
    tier: Optional[int] = None       # 尺寸档位（px）；None = 原图/最大档（保留兼容）
    selected_tier: Optional[int] = None  # 选图时被分配的"原始宽度目标档位"（768/1024/2048/0=最大）
    orig_width: Optional[int] = None   # 原图宽度（用于判断能否支撑多档尺寸）
    orig_height: Optional[int] = None
    resize_widths: Optional[list] = None  # 保留兼容（现在恒为空，下载器不再缩放）
    # 上游原生排序/分数（落库用）：source_rank = 该候选在来源结果中的次序（越小越相关）；
    # source_score = 来源提供的原生分数（如 iNaturalist 的 votes），无则 None。
    source_rank: Optional[int] = None
    source_score: Optional[float] = None

    # --- 采集过程字段 ---
    status: str = STATUS_CANDIDATE
    fetched_at: float = field(default_factory=time.time)
    reject_reason: Optional[str] = None
    fail_reason: Optional[str] = None
    fail_kind: Optional[str] = None    # 失败分类：hotlink_forbidden/dead_link/timeout/network_error/decode/content_mismatch
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

    def __post_init__(self):
        # 归一化：orig_width 是选图/分档的依据。多数适配器只填 declared_width，
        # 若未显式给出 orig_width（如 wikimedia/inaturalist/openverse/scrapers），
        # 回落到 declared_width，否则授权源因 orig_width=None 被选图整体丢弃，
        # 导致 CC 下载恒为 0。baidu 等显式设 orig_width 的适配器不受影响。
        if self.orig_width is None and self.declared_width is not None:
            self.orig_width = self.declared_width

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
