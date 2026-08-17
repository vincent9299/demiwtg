# -*- coding: utf-8 -*-
"""collect.auto — L3 智能平面（确定性编排 + 低频 LLM 环节）。

模块边界（P1 交付前三步，产物均在 state/collect/auto/，人工过目）：
- gap.py      缺口分析：instances 全集 − images.jsonl 达标集，按 taxonomy 聚簇
- discover.py 源发现：LLM 基于缺口簇产出源提案（端点草案 + 探针查询词；不产完整 spec）
- probe.py    探测：预算内真实请求提案端点，规则裁决（无 LLM）
后续 P2：synth（真实响应样本喂 LLM 合成完整 spec）+ verify 三级闸门。
"""

import os


def auto_dir(meta_dir: str) -> str:
    """state/collect/auto（与 registry._state_dir 同一推导规则）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
        os.path.normpath(meta_dir)))))
    return os.path.join(root, "state", "collect", "auto")
