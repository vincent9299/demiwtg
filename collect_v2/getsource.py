"""collect_v2 域路由算子（getsource）：seed → (seed, 源) 配对，位于 op_seed 与 op_search 之间。

契约（.qoder/handoff_collect_v2.md §4.4 + 2026-08-20 用户拍板）：
- 域路由从「种子流入口侧」实体化为独立算子（用户拍板：单独加算子 getsource）；
- **配置表驱动**：路由规则是数据（ROUTE_TABLE）不是散落逻辑，加源只改表；
- 本期最小路由表：中文 seed → wikimedia_zh + baidu（已实现 adapter）；
  西文 seed → wikimedia（en，adapter 待建；路由先行，adapter 缺时链层跳过）；
- inaturalist（需生物类实例标识）/fandom（需域划分）本期无路由依据，挂起；
  其余源在待办队列逐个补 adapter 时进表；
- 防错配不杀候选：路由只决定「这个 seed 打哪些源」，不对候选做任何筛选。
"""

from __future__ import annotations

from collect_v2.op_search import Seed

# 域路由表：lang → 源列表（顺序即投递顺序，无权重语义）
ROUTE_TABLE: dict = {
    "zh": ["wikimedia_zh", "baidu"],
    "latin": ["wikimedia"],
}


def route(seed: Seed) -> list:
    """单 seed 路由：返回 [(seed, 源), ...]，源序按表内顺序。

    未登记的 lang 返回空列表（认缺，不回落不放宽）。
    """
    return [(seed, source) for source in ROUTE_TABLE.get(seed.lang, [])]
