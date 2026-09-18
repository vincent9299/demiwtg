"""collect_v2 兼容 shim（2026-09-05 还原盘布局适配）。

本还原盘沿用 AGENTS.md 收编前布局：mount_map/llm_common 在根 taxonomy/，
search_kb 系在根 curation/。旧机器上 collect_v2 是独立包；此 shim 以
re-export 方式补齐 import 面，零改动 benchmark/taxonomy/curation 现有代码。
infra/op_annotate 本盘缺失，import 时报明确错误。
"""
from pathlib import Path
import sys as _sys

_REPO = Path(__file__).resolve().parents[3]  # collect/archive/collect_v2 → repository
for _p in (str(_REPO), str(_REPO / 'taxonomy'), str(_REPO / 'curation')):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
