"""配置加载与合并（对应文档第 2 节）。

config JSON 结构：
{
  "defaults": { ...全局默认上限与 allowlist... },
  "jobs": [ {"tag": "...", "query": "...", "<可选覆盖键>": ...}, ... ]
}
Job 未提供的键回退到 defaults；query 缺省取 tag 的叶子名（路径末段）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


CONFIG_KEYS = [
    "mime_allowlist",
    "min_width",
    "min_height",
    "max_file_bytes",
    "total_budget_bytes",
    "license_allowlist",
    "target_count",
    "per_host_min_interval_sec",
    "timeout_sec",
    "max_retries",
    "thumb_width",
]

DEFAULTS = {
    "mime_allowlist": ["image/jpeg", "image/png"],
    "min_width": 200,
    "min_height": 200,
    "max_file_bytes": 10 * 1024 * 1024,
    "total_budget_bytes": 1024 * 1024 * 1024,
    "license_allowlist": [
        "CC BY", "CC BY-SA", "CC0", "Public domain",
        "CC BY 4.0", "CC BY-SA 4.0",
    ],
    "target_count": 10,
    "per_host_min_interval_sec": 1.0,
    "timeout_sec": 30,
    "max_retries": 3,
    "thumb_width": 1280,
}


@dataclass
class EffectiveConfig:
    mime_allowlist: list
    min_width: int
    min_height: int
    max_file_bytes: int
    total_budget_bytes: int
    license_allowlist: list
    target_count: int
    per_host_min_interval_sec: float
    timeout_sec: int
    max_retries: int
    thumb_width: int

    @classmethod
    def resolve(cls, defaults: dict, overrides: Optional[dict]) -> "EffectiveConfig":
        ov = overrides or {}
        kw = {k: (ov[k] if k in ov else defaults[k]) for k in CONFIG_KEYS}
        return cls(**kw)


@dataclass
class Job:
    tag: str
    query: str
    source: str = "wikimedia"
    overrides: dict = field(default_factory=dict)
    effective: Optional[EffectiveConfig] = None

    @staticmethod
    def leaf_of(tag: str) -> str:
        return tag.rsplit(" / ", 1)[-1].rsplit("/", 1)[-1]

    def __post_init__(self):
        if not self.query:
            self.query = self.leaf_of(self.tag)
        self.effective = EffectiveConfig.resolve(DEFAULTS, self.overrides)


def load_config(path: str) -> list[Job]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    defaults = {**DEFAULTS, **(doc.get("defaults") or {})}
    jobs: list[Job] = []
    for j in doc.get("jobs") or []:
        if "tag" not in j:
            raise ValueError(f"job 缺少 tag 字段: {j}")
        overrides = {k: v for k, v in j.items() if k in CONFIG_KEYS}
        jobs.append(
            Job(
                tag=j["tag"],
                query=j.get("query") or Job.leaf_of(j["tag"]),
                source=j.get("source", "wikimedia"),
                overrides=overrides,
            )
        )
    return jobs
