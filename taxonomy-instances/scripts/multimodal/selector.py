"""选图器。

用户要求【先全部下载、后续再整体选图过滤】，且明确【彻底不要任何过滤】：
本步骤不做内容去重、不做宽度/分辨率偏好排序、不做数量上限——所有已下载候选
原样返回，顺序与输入一致。

任何"去重 / 按分辨率筛选 / 限张数"等决定都不在此处发生（若需要，由下游单独的
"整体选图"步骤统一、可幂等地处理，而非分散在按来源调用的本函数里）。
"""

from __future__ import annotations

from typing import List

from .models import Candidate


def select_distinct(candidates: List[Candidate]) -> List[Candidate]:
    """原样返回所有候选，不做任何过滤（无去重、无排序、无限张数）。

    内容寻址去重已由下载器的 sha256 落盘天然完成；此处不再施加任何基于
    内容/宽度/数量的剔除或重排。
    """
    return list(candidates)
