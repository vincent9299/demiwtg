"""配置加载与合并（对应文档第 2 节）。

config JSON 结构：
{
  "defaults": { ...全局默认上限与 allowlist... },
  "jobs": [ {"tag": "...", "query": "...", "<可选覆盖键>": ...}, ... ]
}
Job 未提供的键回退到 defaults；query 缺省取 tag 的末段名（路径末段）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional


CONFIG_KEYS = [
    "mime_allowlist",
    "max_file_bytes",
    "total_budget_bytes",
    "license_allowlist",
    "target_count",
    "per_host_min_interval_sec",
    "timeout_sec",
    "max_retries",
    "thumb_width",
    "min_resolution",
    "max_per_source",
    "sources",
    "unauthorized_sources",
    # —— 运行期优化（2026-08-13）——
    "min_images_per_tag",        # 太少动态扩源触发线：标签成功图 < 此数则补搜
    "dead_min_tags",             # 死源动态剔除：源在 >= 此数标签上 0 成功即剔除
    "known_dead_sources",        # 死源种子（沙箱已知必失败源，直接跳过超时）
    "expansion_sources",         # 扩源池（空=用全部源兜底）
    "starved_max_per_source",    # 扩源轮每源上限（放宽基础轮 max_per_source）
    "alias_stop_factor",         # 多别名检索早停系数：累计候选 >= target_count*K 即不再追加别名
]

DEFAULTS = {
    # 允许的原图 MIME：与 downloader 可解码/嗅探的格式对齐（JPEG/PNG/WEBP/GIF/TIFF/BMP）。
    # 早期仅 jpeg/png 导致 Wikimedia(CC) 大量 webp/gif/tiff 候选被过滤、CC 下载量为 0。
    "mime_allowlist": ["image/jpeg", "image/png", "image/webp", "image/gif", "image/tiff", "image/bmp"],
    "max_file_bytes": 10 * 1024 * 1024,
    "total_budget_bytes": 1024 * 1024 * 1024,
    "license_allowlist": [
        "CC BY", "CC BY-SA", "CC0", "Public domain",
        "CC BY 4.0", "CC BY-SA 4.0",
        # 扩种 iNaturalist（社区物种观测，多为 CC BY-NC / ND）：放宽纳入非商业/禁止演绎变体，
        # 以便其原生投票分数（cached_votes_total）能进入数据集。license_raw 仍原样保留，
        # 下游可按需再切分"纯商业友好(无 NC/ND)"子集。
        "CC BY-NC", "CC BY-NC-SA", "CC BY-ND", "CC BY-NC-ND",
    ],
    # 检索召回广度系数（仅影响"每来源检索多少候选"，不影响最终保留多少）。
    # 下载阶段会按"实际分辨率门"(min_resolution) 拦截低分辨率原图（不落盘）；
    # 宽度/相似度等更细的"整体选图"延后在 Lance 数据集上做 SELECT。
    "target_count": 4,
    "per_host_min_interval_sec": 1.0,
    "timeout_sec": 30,
    "max_retries": 3,
    "thumb_width": 1280,
    # 下载阶段分辨率门（实际解码后的像素，安全可靠）：候选通过基础校验后，
    # 在 downloader 解码原图时用 actual_width/actual_height 拦截 —— 任一边 < min_resolution
    # 直接丢弃、不落盘。用"实际解码尺寸"而非上游声明尺寸，避免声明失真导致误杀/漏杀。
    # 默认 768：低于此的原图信息量过低、放大需求大，先用此门压住总量，下游仍可在
    # Lance 数据集上做更细的按分辨率/相似度 SELECT。设为 0 = 关闭门（全部保留）。
    "min_resolution": 768,
    # 每源每标签最多下载张数（下载前封顶）。同一 (tag, source) 只保留
    # source_rank 最小的前 N 张（最相关）再下载；N=None/0 = 不封顶（下载全部通过基础校验的候选）。
    # 与早期被否的"按宽度排序+全局 max_n 截断"不同：这里按 (tag,source) 独立封顶、取最相关者，
    # 是单调幂等的（召回更多候选不会挤掉已入选的图），重跑结果一致。典型值 1 = 每源 1 张
    # （不同源各 1 张 → 每标签≈源数个不同内容图），可大幅压低存储。
    "max_per_source": None,
    # 授权（CC）来源列表：每个 job 会用列表中各源按自身语言 query 检索并合并候选。
    "sources": ["wikimedia", "wikimedia_zh", "inaturalist"],
    # 未授权来源（非 CC、有 ToS/法律风险）。单流模式下与授权源统一落盘、统一清单，
    # 但每张图保留 source / license_raw / source_authorized 字段以便下游按授权状态切分。
    # 扩种中文源：百度/必应/360/搜狗/百度百科/豆瓣（通用）+ 央视/搜狗图库/站酷/中新/
    # 人民/花瓣(HTML+API)/堆糖/知乎/新浪/搜狐（通用网页抽取）。部分源在此沙箱被反爬/JS
    # 渲染拦截（403/空），但均已实现 best-effort，换环境或放宽反爬后可能出图。
    "unauthorized_sources": [
        "baidu", "bing", "so360", "sogou", "baidu_baike", "douban",
        "cctv", "sogou_tuku", "zcool", "chinanews", "people",
        "huaban", "huaban_api", "duitang", "zhihu", "sina", "sohu",
        "toutiao",
    ],
    # —— 运行期优化（2026-08-13）——
    # 太少动态扩源触发线：某标签成功图 < 此数时，自动用 expansion_sources 补搜。
    "min_images_per_tag": 4,
    # 死源动态剔除：某源在 >= dead_min_tags 个标签上 0 成功即剔除（不再搜/下）。
    "dead_min_tags": 8,
    # 死源种子（沙箱已知必失败源，直接跳过其超时等待）。
    "known_dead_sources": [
        "sogou", "baidu_baike", "douban", "cctv", "sogou_tuku",
        "zcool", "chinanews", "people", "zhihu", "sina", "sohu",
    ],
    # 扩源池：标签图少时补搜的来源。空 = 用全部源作兜底（除已剔除外的所有源）。
    "expansion_sources": [],
    # 扩源轮每源上限（放宽基础轮的 max_per_source，尽量凑够 min_images_per_tag）。
    "starved_max_per_source": 2,
    # 多别名检索早停系数：某来源累计候选 >= target_count * K 即不再追加下一个别名。
    # K 过小→召回不足；过大→请求量上升（inaturalist 等限流源更敏感）。
    "alias_stop_factor": 4,
}


@dataclass
class EffectiveConfig:
    mime_allowlist: list
    max_file_bytes: int
    total_budget_bytes: int
    license_allowlist: list
    target_count: int
    per_host_min_interval_sec: float
    timeout_sec: int
    max_retries: int
    thumb_width: int
    min_resolution: int
    max_per_source: Optional[int]
    sources: list
    unauthorized_sources: list
    # —— 运行期优化（2026-08-13）——
    min_images_per_tag: int
    dead_min_tags: int
    known_dead_sources: list
    expansion_sources: list
    starved_max_per_source: Optional[int]
    alias_stop_factor: float

    @classmethod
    def resolve(cls, defaults: dict, overrides: Optional[dict]) -> "EffectiveConfig":
        ov = overrides or {}
        kw = {k: (ov[k] if k in ov else defaults[k]) for k in CONFIG_KEYS}
        return cls(**kw)


@dataclass
class Job:
    tag: str
    query: str                         # 英文 query（别名/回退）
    source: str = "wikimedia"          # 兼容旧字段（首个授权源），实际由 sources 控制
    zh_query: str = ""                 # 中文 query（中文源用）
    overrides: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=lambda: dict(DEFAULTS))
    effective: Optional[EffectiveConfig] = None
    # 多别名检索池（query ∪ aliases 按语言分流排序；en 源用 en_aliases、zh 源用 zh_aliases）
    en_aliases: list = field(default_factory=list)
    zh_aliases: list = field(default_factory=list)

    @staticmethod
    def tail_of(tag: str) -> str:
        return tag.rsplit(" / ", 1)[-1].rsplit("/", 1)[-1]

    def __post_init__(self):
        if not self.query:
            self.query = self.tail_of(self.tag)
        # 中文 query：去《》（）装饰后的中文末段名；未提供则回退英文 query。
        if not self.zh_query:
            tail = self.tail_of(self.tag)
            tail = tail.replace("《", "").replace("》", "")
            import re as _re
            tail = _re.sub(r"[（(].*?[)）]", "", tail).strip()
            self.zh_query = tail or self.query
        # 别名池兜底：未显式提供时退化为单别名（保持旧行为）。
        if not self.en_aliases:
            self.en_aliases = [self.query] if self.query else []
        if not self.zh_aliases:
            self.zh_aliases = [self.zh_query] if self.zh_query else []
        self.effective = EffectiveConfig.resolve(self.defaults, self.overrides)

    @property
    def en_query(self) -> str:
        return self.query

    @property
    def sources(self) -> list:
        return self.overrides.get("sources", self.defaults.get("sources", ["wikimedia"]))

    @property
    def unauthorized_sources(self) -> list:
        return self.overrides.get(
            "unauthorized_sources", self.defaults.get("unauthorized_sources", []))


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
                query=j.get("query") or Job.tail_of(j["tag"]),
                source=j.get("source", (defaults.get("sources") or ["wikimedia"])[0]),
                zh_query=j.get("zh_query", ""),
                overrides=overrides,
                defaults=defaults,
            )
        )
    return jobs


def _norm_instance(name: str) -> str:
    """归一化实例名：去《》、去（...）/(...)注释、去首尾空白。"""
    s = name.strip().replace("《", "").replace("》", "")
    s = re.sub(r"[（(].*?[)）]", "", s)
    return s.strip()


# 图片打标只存实体名（不含路径，见 AGENTS.md 1.5）；路径由消费端
# （scripts/lake/link_by_tag.py）从 instances.json 解析。
def _tag_of(taxonomy_path: str, name: str) -> str:
    return name


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _build_alias_pools(name: str, query_list, aliases_list) -> tuple[list, list]:
    """合并 query ∪ aliases，按语言分流成 (en 池, zh 池)。

    - en 池：拉丁字母检索词，排序（短词优先、无装饰优先）——英文源 best-first 早停用；
    - zh 池：中文检索词 + 归一化实例名兜底——中文源用。
    """
    norm_name = _norm_instance(name)
    seen: set[str] = set()
    en: list[str] = []
    zh: list[str] = []
    for term in list(query_list or []) + list(aliases_list or []):
        if isinstance(term, str):
            terms = [term]
        else:
            terms = list(term or [])
        for t in terms:
            t = (t or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            if _has_cjk(t):
                zh.append(t)
            else:
                en.append(t)
    if norm_name and norm_name not in seen:
        zh.append(norm_name)

    def _en_key(t: str) -> tuple:
        deco = sum(1 for c in t if c in "《》()（）[]")
        return (deco, len(t.split()), len(t))

    en.sort(key=_en_key)
    return en, zh


def load_taxonomy(path: str, aliases_path: Optional[str] = None) -> tuple[list[Job], str]:
    """直接以「统一标签体系」的实例元文件为采集输入，实时派生 jobs（无需单独的采集配置）。

    全量收敛后：path 指向 data/instances.json（扁平 instance 列表）。每个【实体名】生成一个
    job（同名实体挂多个路径时只采集一次，采集按实体标签进行，见 AGENTS.md 1.5）：
      tag      = 实例名（实体标签，不含路径）
      query    = instance.query（英文检索词，全量实例已补齐）；缺省回退 instance.aliases[0]，
                 再回退实例名（Job 构造器兜底）
      zh_query = 归一化实例名（中文源用）
    aliases_path 已废弃（别名并入 instances.json 的 instance.query/aliases 字段）；保留参数仅为兼容调用方。

    返回 (jobs, taxonomy_label)；label 取文件名，供消费审计记录。
    """
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    instances = doc.get("instances") or []

    defaults = dict(DEFAULTS)
    jobs: list[Job] = []
    seen: set[str] = set()
    for it in instances:
        name = it.get("name")
        if not name:
            continue
        tag = _tag_of(it.get("taxonomy_path") or "", name)
        if tag in seen:
            continue
        seen.add(tag)
        aliases = it.get("aliases") or []
        q = it.get("query") or []
        if isinstance(q, str):
            q = [q]
        en, zh = _build_alias_pools(name, q, aliases)
        jobs.append(Job(
            tag=tag,
            # 主 query 取 en 池最优项；纯中文实例回退 zh 池首项（保持旧行为，英文源仍可一试）
            query=(en[0] if en else (zh[0] if zh else "")),
            zh_query=_norm_instance(name),
            en_aliases=en,
            zh_aliases=zh,
            defaults=defaults,
        ))

    label = os.path.basename(path)
    return jobs, label
