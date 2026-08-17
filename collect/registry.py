"""来源注册表：源卡 + 生命周期状态机 + 统一适配器加载（L2 源平面核心）。

来源的三层构成（名字全局唯一）：
1. 手写适配器（sources/ 下的 Python 模块，import 时自注册进 _REGISTRY）
   → provenance=manual, mode=code；代码即定义，无需登记文件。
2. 手写 spec（collect/specs/<name>.v<N>.json，入 git）
   → provenance=manual, mode=spec；声明式源的定义也是代码产物。
3. 生成源（state/collect/specs/<name>.v<N>.json + 本文件覆盖层登记，
   不入 git）→ provenance=llm_spec|llm_code，生命周期由验收/健康证据驱动。

生命周期（状态迁移一律由代码证据驱动，无 LLM 参与裁决）：
    candidate → probation → active → degraded → retired
可参与采集的状态：active / probation。
默认生命周期：手写模块/手写 spec 为 active（人工审核过的代码产物）；
生成源（llm_spec/llm_code）为 candidate（必须经探测/验收证据晋升后才参与采集）。

覆盖层 state/collect/source_registry.jsonl：append-only，一行一事件，
同名取最后一行；只存生命周期迁移与生成源登记（手写源的定义不在此重复）。

Tier C 逃生舱（代码生成源契约，声明式优先的兜底；登记即契约，不做
LLM 代码生成机器——超出 Tier A 表达边界的源先由人工/agent 写模块）：
源超出「单请求 + 纯投影」边界（多步请求链/URL 派生/HTML 抽取等）时，
在 collect/sources/ 落盘模块（实现 search/to_candidate，入 git = 人工审核闸门），
再经覆盖层事件 {"name":..., "provenance":"llm_code", "lifecycle":"candidate"}
登记治理身份；此后与 llm_spec 源同规则受 govern 降级/退休、修复后
--reactivate 恢复。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .sources.base import _REGISTRY
from . import sources as _sources_pkg  # noqa: F401  触发自注册
from .spec_adapter import GenericSpecAdapter, load_spec_file
from .util import meta_lock

LIFECYCLE_STATES = ("candidate", "probation", "active", "degraded", "retired")
USABLE_STATES = ("active", "probation")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL_SPECS_DIR = os.path.join(_REPO_ROOT, "collect", "specs")

_SPEC_FILE_RE = re.compile(r"^(?P<name>[a-z0-9_]+)(?:\.v(?P<ver>\d+))?\.json$")


@dataclass
class SourceCard:
    name: str
    mode: str = "code"                    # code（手写模块）| spec（声明式）
    provenance: str = "manual"            # manual | llm_spec | llm_code
    lifecycle: str = "active"
    kind: str = "directed"                # directed | general
    capabilities: list = field(default_factory=list)
    authorized: bool = True
    lang: str = "en"
    source_kind: str = ""
    spec_path: Optional[str] = None       # 相对仓库根
    version: int = 1
    network_scope: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    scope_approximated: bool = False      # True=双池由 allowed_suffixes 粗合成（手写模块），
    #                                     # api/media 分池不可信，仅供展示，禁止用于策略匹配


def _state_dir(meta_dir: Optional[str]) -> Optional[str]:
    """与 pipeline._state_dir 一致的推导：meta 向上三级 + state/collect。"""
    if not meta_dir:
        return None
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
            os.path.normpath(meta_dir))))),
        "state", "collect")


def _overlay_path(meta_dir: Optional[str]) -> Optional[str]:
    d = _state_dir(meta_dir)
    return os.path.join(d, "source_registry.jsonl") if d else None


def _scan_specs(dirpath: str) -> Dict[str, tuple]:
    """扫描 spec 目录 → {name: (version, abspath)}，同名取最高版本。"""
    out: Dict[str, tuple] = {}
    if not os.path.isdir(dirpath):
        return out
    for fn in sorted(os.listdir(dirpath)):
        m = _SPEC_FILE_RE.match(fn)
        if not m:
            continue
        ver = int(m.group("ver") or 1)
        name = m.group("name")
        if name not in out or ver > out[name][0]:
            out[name] = (ver, os.path.join(dirpath, fn))
    return out


class Registry:
    def __init__(self, cards: Dict[str, SourceCard], meta_dir: Optional[str]):
        self._cards = cards
        self._meta_dir = meta_dir
        self._adapter_cache: Dict[str, object] = {}

    # ---------- 查询 ----------
    def names(self) -> List[str]:
        return sorted(self._cards)

    def card(self, name: str) -> SourceCard:
        if name not in self._cards:
            raise KeyError(f"未知来源: {name}（已注册：{self.names()}）")
        return self._cards[name]

    def usable_names(self) -> set:
        """可参与采集的源（active/probation）；retired/degraded/candidate 不参与。"""
        return {n for n, c in self._cards.items() if c.lifecycle in USABLE_STATES}

    def get_adapter(self, name: str):
        """统一适配器入口：手写模块实例 or spec 实例化（缓存）。"""
        card = self.card(name)
        if card.mode == "code":
            ad = _REGISTRY.get(name)
            if ad is None:
                raise KeyError(
                    f"来源 {name}（provenance={card.provenance}）登记为 code 模式，"
                    f"但 collect/sources/ 中没有可加载的模块；"
                    f"生成代码源（Tier C）需先完成模块落盘与导入登记才能实例化")
            return ad
        if name not in self._adapter_cache:
            spec_path = card.spec_path
            if not spec_path or not os.path.isabs(spec_path):
                spec_path = os.path.join(_REPO_ROOT, spec_path or "")
            spec = load_spec_file(spec_path)
            self._adapter_cache[name] = GenericSpecAdapter(spec)
        return self._adapter_cache[name]

    # ---------- 写入（生命周期迁移 / 生成源登记） ----------
    def record_event(self, entry: dict) -> None:
        """向覆盖层追加一条事件（{"name":..., "lifecycle":...,...}）。"""
        p = _overlay_path(self._meta_dir)
        if not p:
            raise RuntimeError("未提供 meta_dir，无法写 registry 覆盖层")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        with meta_lock(self._meta_dir):
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def set_lifecycle(self, name: str, lifecycle: str,
                      reason: str = "") -> None:
        if lifecycle not in LIFECYCLE_STATES:
            raise ValueError(f"非法生命周期状态: {lifecycle}")
        self.card(name)  # 存在性校验
        self.record_event({"name": name, "lifecycle": lifecycle,
                           "reason": reason})
        self._cards[name].lifecycle = lifecycle


def _card_from_spec(spec: dict, spec_rel: str, version: int,
                    provenance: str) -> SourceCard:
    # 生成源默认 candidate：未经探测/验收证据不得参与采集（P1 闸门）
    default_lifecycle = "active" if provenance == "manual" else "candidate"
    return SourceCard(
        name=spec["name"],
        mode="spec",
        provenance=provenance,
        lifecycle=default_lifecycle,
        kind=spec.get("kind", "directed"),
        capabilities=list(spec.get("capabilities") or []),
        authorized=bool(spec.get("authorized", False)),
        lang=spec.get("lang", "en"),
        source_kind=spec.get("source_kind", ""),
        spec_path=spec_rel,
        version=version,
        network_scope=spec.get("network_scope") or {},
        limits=spec.get("limits") or {},
    )


def load_registry(meta_dir: Optional[str] = None) -> Registry:
    """装配注册表：手写模块 ∪ 手写 spec ∪ 生成 spec，再叠加覆盖层事件。"""
    cards: Dict[str, SourceCard] = {}

    # 1) 手写适配器（代码即定义；scope 为 allowed_suffixes 粗合成，仅展示用）
    for name, ad in _REGISTRY.items():
        suffixes = tuple(getattr(ad, "allowed_suffixes", ()) or ())
        cards[name] = SourceCard(
            name=name, mode="code", provenance="manual",
            kind="directed",
            authorized=bool(getattr(ad, "is_authorized", True)),
            lang=getattr(ad, "lang", "en"),
            source_kind=getattr(ad, "source_kind", ""),
            network_scope={"api_hosts": list(suffixes),
                           "media_hosts": list(suffixes)},
            scope_approximated=True,
        )

    # 2) 手写 spec（collect/specs/，入 git；人工审核过，默认 active）
    for name, (ver, path) in _scan_specs(MANUAL_SPECS_DIR).items():
        spec = load_spec_file(path)
        rel = os.path.relpath(path, _REPO_ROOT)
        cards[spec["name"]] = _card_from_spec(spec, rel, ver, "manual")

    # 3) 生成 spec（state/collect/specs/，不入 git；默认 candidate）。
    #    容错加载：单个坏 spec 只警告跳过，不得拖垮整条采集管线。
    gen_dir = os.path.join(_state_dir(meta_dir) or "", "specs")
    for name, (ver, path) in _scan_specs(gen_dir).items():
        try:
            spec = load_spec_file(path)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            print(f"[warn] 生成 spec 无法加载，已跳过: {path}: {e}", flush=True)
            continue
        cards[spec["name"]] = _card_from_spec(
            spec, path, ver, "llm_spec")  # provenance 可被覆盖层改写

    # 4) 覆盖层事件（append-only；同名后写覆盖）
    p = _overlay_path(meta_dir)
    if p and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = e.get("name")
                if not name:
                    continue
                c = cards.get(name)
                if c is None:
                    # 覆盖层登记了手写/spec 之外的源（如 llm_code 模块源）
                    c = cards[name] = SourceCard(name=name, mode="code",
                                                 provenance="llm_code")
                for k in ("lifecycle", "provenance", "kind", "authorized",
                          "lang", "version"):
                    if k in e:
                        setattr(c, k, e[k])
                if "capabilities" in e:
                    c.capabilities = list(e["capabilities"])
                if "spec" in e:
                    c.spec_path = e["spec"]
                    c.mode = "spec"

    return Registry(cards, meta_dir)
