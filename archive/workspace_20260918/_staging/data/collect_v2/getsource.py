"""collect_v2 域路由算子（getsource）：seed → (seed, 源) 配对，位于 op_seed 与 op_search 之间。

契约（.qoder/handoff_collect_v2.md §4.4 + 2026-08-20 用户拍板）：
- 域路由从「种子流入口侧」实体化为独立算子（用户拍板：单独加算子 getsource）；
- **配置表驱动**：路由规则是数据（ROUTE_TABLE）不是散落逻辑，加源只改表；
- 2026-08-20 拍板更新：虚拟角色向新源对**所有 seed**（zh/latin）全量投递，
  无召回即认缺不做语言特判；新源：anilist/mal/pixiv/bing_images/
  yandex_images/deviantart；fandom 全局端点被 Cloudflare 拦，挂起待拍板；
- inaturalist（需生物类实例标识）本期无路由依据，挂起；
- 2026-08-20 国内爬虫三源（huaban_api/toutiao/so360，旧系统迁移）只打 zh 种子
  （中文站内检索，拉丁词无召回价值），与 baidu 同列；
- 防错配不杀候选：路由只决定「这个 seed 打哪些源」，不对候选做任何筛选。
"""

from __future__ import annotations

import json

from collect_v2.op_search import Seed

# 新源（虚拟角色向）：对所有语言的 seed 全量投递（用户拍板）；
# anilist/mal 不在此列：专场已爬过（mal 3218 行/anilist 1436 行），
# 当前全量专场非角色实例占多数，两角色库基本 404 认缺，纯浪费投递（2026-08-22 拍板）。
_CHAR_SOURCES = ["bing_images", "yandex_images"]

# 代理慢源只打 latin 种子（2026-08-22 拍板）：虚拟角色已全覆盖、本场全是真实实体，
# zh 种子打这两源≈纯认缺（连角色专场时命中率都极低），恢复语言对位；
# latin 行保留西文粉丝图通道。日后虚拟角色专场重启时把两源加回 _CHAR_SOURCES。
# deviantart 2026-08-22 再摘（用户拍板）：近 1 小时仅 59 张、多次 10 分钟零产出，
# 半死源还占投递对数与代理流量；日后复活再加回本名单。
_LATIN_ONLY_SOURCES = ["pixiv"]

# 国内爬虫档中文源：只打 zh 种子（中文站，同 baidu）
_CN_CRAWLER_SOURCES = ["huaban_api", "toutiao", "so360"]

# 域路由表：lang → 源列表（顺序即投递顺序，无权重语义）
#
# 2026-08-22 代理 192.168.10.109:10808 修复复通（五端点实测全通），
# 还原 2026-08-21 临时摘除的代理源；同期按拍板剔除 anilist/mal（专场已爬，
# 全量专场无命中价值）；pixiv/deviantart 移入 _LATIN_ONLY_SOURCES（zh 行摘除，
# 恢复语言对位，见上方注释）。日后虚拟角色专场重启时再把角色源加回。
ROUTE_TABLE: dict = {
    "zh": ["baidu", "wikimedia_zh"] + _CN_CRAWLER_SOURCES + _CHAR_SOURCES,
    "latin": ["wikimedia"] + _CHAR_SOURCES + _LATIN_ONLY_SOURCES,
}

# 条件源（2026-08-29 源策略，--source-agent 门控）：按实例挂载路径关键词命中才投递。
# anilist/mal 是动画/漫画角色库（2026-08-22 全量场 404 浪费摘除；条件路由复产——
# 只打挂载路径含动漫关键词的实例，中英树关键词并收，路径段 substring 匹配）。
_DOMAIN_SOURCES: dict[str, tuple] = {
    "anilist": ("Anime", "Manga", "Animation", "动画", "漫画", "动漫"),
    "mal": ("Anime", "Manga", "Animation", "动画", "漫画", "动漫"),
}


def route(seed: Seed, domains=None, *, agent: bool = False) -> list:
    """单 seed 路由：返回 [(seed, 源), ...]，源序按表内顺序。

    未登记的 lang 返回空列表（认缺，不回落不放宽）。
    agent=False（默认）：行为与 2026-08-29 前完全一致（中文链不传旗零变化）。
    agent=True（源策略扩展，EN 链先跑通）：deviantart 复挂 latin 路（健康账本
    监护，死源自动停用）；domains 为该实例的挂载路径段集合（load_domain_segs
    现算），命中 _DOMAIN_SOURCES 关键词的条件源追加投递。
    """
    sources = list(ROUTE_TABLE.get(seed.lang, []))
    if agent:
        if seed.lang == "latin":
            sources.append("deviantart")
        for src, kws in _DOMAIN_SOURCES.items():
            if src in sources:
                continue
            if domains and any(kw in seg for seg in domains for kw in kws):
                sources.append(src)
    return [(seed, s) for s in sources]


def load_domain_segs(tree_path: str) -> dict:
    """读树现算 实例名 → 挂载路径段集合（全层级段名并集，供关键词匹配）。

    消费者现场聚合模式（同 mount_map）：不落盘；一实例多挂=多路径段并集；
    未挂载实例不在表内（返回 dict 缺键，条件源不投递——待认领池无路由依据）。
    """
    doc = json.loads(open(tree_path, encoding="utf-8").read())
    out: dict[str, set] = {}

    def walk(node) -> None:
        for inst in node.get("instances") or []:
            out.setdefault(inst, set()).update(node.get("path", "").split(" / "))
        for ch in node.get("children") or []:
            walk(ch)

    tree = doc.get("tree") or {}
    if tree:
        walk(tree)
    return out
