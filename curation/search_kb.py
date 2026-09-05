#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search_kb.py — 实例知识检索接地管线（search agent，交接 2026-08-28 落地）。

为 instances.json 实例做「多源检索 → 证据包 → glm-5.3-flash 合成视觉知识卡」：
    源规划（glm-5.3-flash 按实例从源注册表 DIRECT_REGISTRY 选源+给查询词；
    失败回退规则路由；--no-planner 可关）→ 直供源（zh/en wiki、萌百、Bangumi、
    inaturalist/anilist/jikan/steam/openlibrary/dbpedia/wikidata，见
    search_kb_sources.py）→ 四引擎 SERP（神马/360/bing 直连 + ddg 代理；语言路由
    + 每引擎 1q/1.2s + 词面相关性 gate 防 bing 投毒；planner 可改写引擎查询）
    → 链接解包（ddg uddg= 直解；360 so.com/link?m=、bing ck/a 走一次 redirect）
    → 白名单域正文抓取（截 3.5k 字）
    → glm-5.3-flash 合成知识卡（证据锚定纪律，零证据/证据无关拒卡）
    → 机审 gate（证据编号合法性 + 特征锚定）→ desc 刷新草稿（150-350 字契约）

源池进出（2026-08-29）：新源 = 探针验证后在 DIRECT_REGISTRY + search_kb_sources
登记一格（LLM 不裸发明端点——通道/限速/命中校验必须过探针）；慢源/死源由
SourceHealth（state/taxonomy/search_kb/source_health.json）按滚动命中率自动停用、
冷却到点恢复。动态发现新站点走 planner 的 serp site: 定向（安全通道内）。

**本管线不改 instances.json**（入库方式待用户拍板后另批执行）；产物全部落
state/taxonomy/search_kb/（证据断点缓存 + 卡 + desc 草稿 + 源健康账本 + 运行日志）。

目标选择（消费者倒排）：metadata.jsonl 质量门合格图（quality>=8 AND
identity=true）覆盖的实例，排除 source=curated；29 域轮转排序（跨域均衡），
域内先无 desc、后按合格图数降序。幂等：实体名主键，cards.jsonl 已完成
（ok/no_evidence）或尝试≥2 次的实体跳过；证据包缓存在 evidence.jsonl。

用法：
    python3 data/taxonomy/search_kb.py --dry-run --limit 10
    python3 data/taxonomy/search_kb.py --limit 5 --batch smoke
    python3 data/taxonomy/search_kb.py --limit 50 --batch pilot
    python3 data/taxonomy/search_kb.py --limit 1500 --batch scale --workers 4
    python3 data/taxonomy/search_kb.py --entities 咯吱盒,拉奥孔 --batch debug
    python3 data/taxonomy/search_kb.py --limit 10 --plan-only      # 只看源规划
    python3 data/taxonomy/search_kb.py --limit 20 --no-planner     # 规则路由 A/B

LLM key 只从 modelhub/.env 读（GLM_API_BASE/GLM_API_KEY），不落盘不打印。
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # data/ 包根

from collect_v2 import infra                                   # noqa: E402
from collect_v2.infra import (                                  # noqa: E402
    SourceLimits, request as infra_request, close_client,
    DeterministicError, TransientExhaustedError)
from collect_v2.llm_common import extract_json                    # noqa: E402
from collect_v2.mount_map import load_mount_map                   # noqa: E402
from collect_v2.search_kb_sources import (                        # noqa: E402
    anilist_source, dbpedia_source, inaturalist_source, jikan_source,
    openlibrary_source, steam_source, wikidata_source)

ROOT = Path(__file__).resolve().parent.parent.parent             # 仓库根
META_DIR = ROOT / "datasets" / "demiwtg" / "meta"
INSTANCES_PATH = META_DIR / "instances.json"
TAXONOMY_PATH = META_DIR / "taxonomy.json"
MANIFEST_PATH = META_DIR / "metadata.jsonl"
MODELHUB_ENV = ROOT / "modelhub" / ".env"
OUT_DIR = ROOT / "state" / "taxonomy" / "search_kb"
EVIDENCE_PATH = OUT_DIR / "evidence.jsonl"
CARDS_PATH = OUT_DIR / "cards.jsonl"
DESC_PATH = OUT_DIR / "desc_drafts.jsonl"
LOG_PATH = OUT_DIR / "run.log"

LANG = "zh"          # 当前赛道语言（set_lang 切换；所有路径/标记/路由读模块全局）


def set_lang(lang: str) -> None:
    """切换 zh/en 赛道：英文实例（instances_en.json，全裸名无别名无图）直接走
    英文源（wiki_en/dbpedia/ina/steam/ol/anilist/jikan + SERP 只用 bing/ddg），
    产物独立落 state/taxonomy/search_kb_en/，与中文轨互不干扰可并行。"""
    global LANG, INSTANCES_PATH, TAXONOMY_PATH, OUT_DIR, EVIDENCE_PATH, \
        CARDS_PATH, DESC_PATH, LOG_PATH, ACG_MARKS, GAME_MARKS, BOOK_MARKS, \
        SPECIES_DOMAINS, SERP_ENGINES
    if lang not in ("zh", "en"):
        raise ValueError(f"未知语言 {lang!r}")
    if lang == LANG:
        return
    LANG = lang
    if lang == "en":
        INSTANCES_PATH = META_DIR / "instances_en.json"
        TAXONOMY_PATH = META_DIR / "taxonomy_en.json"
        OUT_DIR = ROOT / "state" / "taxonomy" / "search_kb_en"
        ACG_MARKS = ("Anime", "Manga", "Light Novel", "Fictional World",
                     "Virtual Character", "Game Character")
        GAME_MARKS = ("Game Works",)               # Culture, Arts and Media / Content Works / Game Works
        BOOK_MARKS = ("Literary Work", "Novel", "Picture Book", "Comic")
        SPECIES_DOMAINS = ("Animal", "Plant", "Fungi and Microorganisms")
        SERP_ENGINES = ("bing", "ddg")             # 英文引擎；sm/so360 是中文引擎不浪费配额
    else:
        INSTANCES_PATH = META_DIR / "instances.json"
        TAXONOMY_PATH = META_DIR / "taxonomy.json"
        OUT_DIR = ROOT / "state" / "taxonomy" / "search_kb"
        ACG_MARKS = _ZH_ACG_MARKS
        GAME_MARKS = _ZH_GAME_MARKS
        BOOK_MARKS = _ZH_BOOK_MARKS
        SPECIES_DOMAINS = _ZH_SPECIES_DOMAINS
        SERP_ENGINES = _ZH_SERP_ENGINES
    EVIDENCE_PATH = OUT_DIR / "evidence.jsonl"
    CARDS_PATH = OUT_DIR / "cards.jsonl"
    DESC_PATH = OUT_DIR / "desc_drafts.jsonl"
    LOG_PATH = OUT_DIR / "run.log"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HTTP_HEADERS = {"User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}

SEARCH_INTERVAL = 1.2          # 每引擎最小查询间隔（burst.py 实测安全线，勿加码）
PAGE_TEXT_LIMIT = 3500         # 正文抓取截断（探针同款）
SNIPPET_KEEP = 5               # 每引擎入包摘要条数
SERP_KEEP = 8                  # 每引擎解析保留条数
UNWRAP_KEEP = 4                # 每引擎参与解包的正文候选条数
MAX_PAGES = 2                  # 每实体正文抓取页数上限
MAX_EVIDENCE = 16              # 证据包条目上限（正文优先，摘要按引擎序补齐）
ENGINE_DOWN_AFTER = 8          # 连续失败 N 次判引擎阵亡（进入冷却）
ENGINE_COOLDOWN = 600          # 阵亡冷却秒数：到点放一次探活查询，成功即复活
MAX_ATTEMPTS = 2               # 每实体最大尝试次数（跨运行累计；--max-attempts 可调，
                               # 全量跑用 4：引擎冷却期的 degraded llm_error 不该烧尽额度）

# ---------------------------------------------------------------------------
# 源登记（复用 collect_v2.infra：按源闸门限速 + 分类重试 + 直连/代理双池）
# ---------------------------------------------------------------------------

infra.SOURCE_LIMITS.update({
    "sk:sm":      SourceLimits(rate=1.0 / SEARCH_INTERVAL, concurrency=1),
    "sk:so360":   SourceLimits(rate=1.0 / SEARCH_INTERVAL, concurrency=1),
    "sk:bing":    SourceLimits(rate=1.0 / SEARCH_INTERVAL, concurrency=1),
    "sk:ddg":     SourceLimits(rate=1.0 / SEARCH_INTERVAL, concurrency=1, proxy=True),
    # wiki 规矩（昨日坑）：代理、并发≤2、每请求 0.5s（= rate 2.0）、退避×4
    "sk:wiki":    SourceLimits(rate=2.0, concurrency=2, proxy=True),
    "sk:bgm":     SourceLimits(rate=1.0, concurrency=2, proxy=True),
    "sk:moegirl": SourceLimits(rate=0.5, concurrency=1),
    "sk:page_zh": SourceLimits(rate=1.0, concurrency=2),
    "sk:page_en": SourceLimits(rate=1.0, concurrency=2, proxy=True),
    # 直供源扩充（2026-08-29 探针定通道；见 search_kb_sources.py 头注）
    "sk:ina":     SourceLimits(rate=1.0, concurrency=2, proxy=True),
    "sk:anilist": SourceLimits(rate=0.5, concurrency=2, proxy=True),
    "sk:jikan":   SourceLimits(rate=0.5, concurrency=1),
    "sk:steam":   SourceLimits(rate=0.5, concurrency=1, proxy=True),
    "sk:ol":      SourceLimits(rate=1.0, concurrency=2, proxy=True),
    "sk:dbpedia": SourceLimits(rate=1.0, concurrency=2),
})

log = logging.getLogger("search_kb")

# ---------------------------------------------------------------------------
# HTML 解析（SERP 四引擎 + 正文抽取，解析器移植自 src_probe/engine_probe.py）
# ---------------------------------------------------------------------------


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_bing(body: str) -> list:
    out = []
    for m in re.finditer(r'<li class="b_algo".*?(?=<li class="b_algo"|</ol>)', body, re.S):
        blk = m.group(0)
        a = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not a:
            continue
        snip = re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        out.append((strip_tags(a.group(2))[:80], a.group(1)[:300],
                    strip_tags(snip.group(1))[:200] if snip else ""))
    return out


def parse_so360(body: str) -> list:
    out = []
    for m in re.finditer(r'<li class="res-list".*?(?=<li class="res-list"|</ul>)', body, re.S):
        blk = m.group(0)
        a = re.search(r'<h3[^>]*res-title[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not a:
            continue
        snip = re.search(r'<p class="res-desc[^"]*"[^>]*>(.*?)</p>', blk, re.S)
        out.append((strip_tags(a.group(2))[:80], a.group(1)[:300],
                    strip_tags(snip.group(1))[:200] if snip else ""))
    return out


def parse_sm(body: str) -> list:
    """神马 SERP：sc_structure_template_normal 卡片块；标题/URL 优先取 data-reco
    结构化字段（article_title/norm_url），块文本即摘要（REPORT §8）。"""
    import html as _html
    out = []
    blocks = re.split(r'<div class="sc sc_structure_template_normal"', body)[1:]
    for blk in blocks[:12]:
        blk = blk[:20000]
        title, url = "", ""
        m = re.search(r'data-reco="([^"]*)"', blk)
        if m:
            try:
                reco = json.loads(_html.unescape(m.group(1)))
                title = str(reco.get("article_title") or "")
                url = str(reco.get("norm_url") or "")
            except Exception:                                       # noqa: BLE001
                pass
        a = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if (not title or len(title) < 6) and a:
            title = strip_tags(a.group(2))
        if not url and a:
            url = a.group(1)
        if not title or len(title) < 6:
            continue
        out.append((title[:80], url[:300], strip_tags(blk)[:200]))
    return out


def parse_ddg_html(body: str) -> list:
    out = []
    for m in re.finditer(r'<div class="result results_links.*?(?=<div class="result results_links|<div class="nav-link)', body, re.S):
        blk = m.group(0)
        a = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not a:
            continue
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</td>', blk, re.S)
        s = strip_tags((snip.group(1) or snip.group(2)) if snip and (snip.group(1) or snip.group(2)) else "")
        out.append((strip_tags(a.group(2))[:80], a.group(1)[:300], s[:200]))
    return out


ENGINES = {   # name: (url 模板, 解析器)
    "sm":    ("https://m.sm.cn/s?q={Q}", parse_sm),
    "so360": ("https://www.so.com/s?q={Q}", parse_so360),
    "bing":  ("https://www.bing.com/search?q={Q}", parse_bing),
    "ddg":   ("https://html.duckduckgo.com/html/?q={Q}", parse_ddg_html),
}


def html_text(body: str, limit: int = PAGE_TEXT_LIMIT) -> str:
    """正文抽取：剥 script/style/导航标签 → 剥标签 → 压空白 → 截断。"""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S | re.I)
    t = re.sub(r"<(nav|header|footer|aside|form|noscript).*?</\1>", "", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def mediawiki_text(body: str, limit: int = PAGE_TEXT_LIMIT) -> str:
    """MediaWiki 词条页：优先取正文容器，取不到退整页抽取。"""
    m = re.search(r'<div class="mw-parser-output">(.*?)<div class="catlinks"', body, re.S)
    if m:
        return html_text(m.group(1), limit)
    return html_text(body, limit)


def unwrap_ddg(u: str) -> str:
    m = re.search(r"uddg=([^&]+)", u)
    return urllib.parse.unquote(m.group(1)) if m else u


# ---------------------------------------------------------------------------
# 词面相关性 gate（防 bing 投毒式反爬：结构正常但内容随机无关）
# ---------------------------------------------------------------------------


def query_keys(q: str) -> set:
    """查询关键词集合：中文专名取前 4 字 + 全名；英文取最长主词 + 全句。"""
    q = q.strip()
    toks = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", q)
    if toks and len("".join(toks)) >= max(3, int(len(q) * 0.6)):
        best = max(toks, key=len)
        return {best.lower(), q.lower()}
    keys = {q, q[:4]}
    nospace = re.sub(r"\s+", "", q)
    if nospace != q:
        keys.add(nospace)                       # 空格变体（F1 世界锦标赛→F1世界锦标赛）
        keys.add(nospace[:4])
    for t in toks:                          # 混排里的英文主词也算（如「最终幻想TCG」）
        if len(t) >= 3:
            keys.add(t.lower())
    return keys


# 放宽二次匹配的 2 字前缀保护名单：行政区划/大地理/时代词——这些前缀单独出现
# 与实体无关（广东醒狮≠广东），不允许作为放宽键；仅在整页零相关的二次匹配用，
# 首次匹配不受影响（2026-08-29：整页弃页率 34.8%，其中命名不一致是可救部分）
ADMIN_PREFIX_STOP = frozenset(
    "中国 美国 日本 韩国 朝鲜 越南 泰国 缅甸 印度 蒙古 伊朗 埃及 南非 巴西 "
    "英国 法国 德国 瑞士 瑞典 挪威 芬兰 波兰 荷兰 希腊 埃及 墨西哥 "
    "欧洲 亚洲 非洲 美洲 世界 全球 现代 古代 当代 中世 "
    "北京 上海 天津 重庆 河北 山西 辽宁 吉林 黑龙 江苏 浙江 安徽 福建 "
    "江西 山东 河南 湖北 湖南 广东 海南 四川 贵州 云南 陕西 甘肃 青海 "
    "宁夏 西藏 新疆 内蒙 广西 台湾 香港 澳门 "
    "武汉 南京 成都 西安 杭州 苏州 广州 深圳 长沙 郑州 青岛 大连 沈阳 "
    "哈尔 长春 昆明 合肥 福州 南昌 贵阳 兰州 西宁 太原 石家庄".split())
# 3 字行政地名：前缀剥离按 3 字整词切，防止切成「滨工业大学」
ADMIN3_PREFIX = frozenset(
    "哈尔滨 石家庄 秦皇岛 马鞍山 张家界 张家口 景德镇 驻马店 平顶山 连云港 "
    "攀枝花 牡丹江 佳木斯 呼和浩 呼伦贝 鄂尔多 乌兰察 巴彦淖 阿拉善 石河子 "
    "库尔勒 阿克苏 吐鲁番 克拉玛 乌鲁木 日喀则 山南市 林芝市 昌都市 迪庆州 "
    "西双版 大理白 楚雄州 红河州 黔东南 黔南州 黔西南 凉山州 甘孜州 阿坝州".split())


def relax_keys(q: str) -> list:
    """命名不一致的二次放宽键（仅标题域匹配，仅中文查询）：去 2 字限定前缀段
    （武汉光谷有轨电车→光谷有轨电车）与受保护的 2 字前缀（南浔运河古镇→南浔）。"""
    q = q.strip()
    if not re.search(r"[\u4e00-\u9fff]", q):
        return []
    keys = []
    if q[:3] in ADMIN3_PREFIX:                    # 3 字地名：按整词剥，余段须够长
        if len(q) >= 8 and len(q[3:]) >= 5:
            keys.append(q[3:])
        return keys
    if len(q) >= 6 and len(q[2:].strip()) >= 4:     # 去限定前缀后的主段
        keys.append(q[2:].strip())
    if len(q) >= 5 and q[:2] not in ADMIN_PREFIX_STOP:
        keys.append(q[:2])
    return [k for k in keys if k]


def text_relevant(text: str, keys: set) -> bool:
    low = (text or "").lower()
    return any(k.lower() in low for k in keys if k)


# ---------------------------------------------------------------------------
# 引擎健康账本（运行内失效自动降级，collect_v2 source_health 先例的轻量版）
# ---------------------------------------------------------------------------


class EngineHealth:
    """引擎健康账本：连续失败进入冷却窗（不永久弃用），到点探活、成功复活。"""

    def __init__(self):
        self.stats = Counter()
        self.consec = Counter()
        self.down_until: dict = {}

    def ok(self, e):
        self.stats[f"{e}:ok"] += 1
        self.consec[e] = 0
        self.down_until.pop(e, None)

    def fail(self, e):
        self.stats[f"{e}:fail"] += 1
        if e in self.down_until:            # 探活失败：续冷却
            self.down_until[e] = time.time() + ENGINE_COOLDOWN
            return
        self.consec[e] += 1
        if self.consec[e] >= ENGINE_DOWN_AFTER:
            self.down_until[e] = time.time() + ENGINE_COOLDOWN
            log.warning("引擎 %s 连续失败 %d 次，冷却 %ds 后探活", e,
                        self.consec[e], ENGINE_COOLDOWN)

    def gate_drop(self, e):
        self.stats[f"{e}:gate_drop"] += 1
        self.consec[e] = 0                   # 有结构有响应，不算引擎死（是内容不相关）

    def alive(self, e) -> bool:
        return self.down_until.get(e, 0) <= time.time()

    @property
    def down(self) -> set:
        now = time.time()
        return {e for e, t in self.down_until.items() if t > now}

    def summary(self) -> str:
        extra = f" down={sorted(self.down)}" if self.down else ""
        return "  ".join(f"{k}={v}" for k, v in sorted(self.stats.items())) + extra


# ---------------------------------------------------------------------------
# GLM 客户端（key 只从 modelhub/.env 读，不落盘不打印）
# ---------------------------------------------------------------------------


def load_glm_conf() -> tuple:
    """GLM 配置（key 只从 modelhub/.env 读，不落盘不打印）。
    GLM_KEY_SLOT=N 环境变量选第 N 把 key：读 GLM_API_BASE_N/GLM_API_KEY_N/
    GLM_MODEL_N（双监督器分域并行，2026-08-30 拍板；lane B 走 modelhub 网关的
    glm/glm-5.3-flash）；base 缺省回落无后缀 GLM_API_BASE。返回 (base, key, model)。"""
    suffix = f"_{os.environ['GLM_KEY_SLOT'].strip()}" \
        if os.environ.get("GLM_KEY_SLOT", "").strip() else ""
    base, key, model, base0 = "", "", "", ""
    for line in MODELHUB_ENV.read_text().splitlines():
        if line.startswith("GLM_API_BASE="):
            base0 = line.split("=", 1)[1].strip().rstrip("/")
        elif line.startswith("GLM_API_BASE" + suffix + "="):
            base = line.split("=", 1)[1].strip().rstrip("/")
        elif line.startswith("GLM_API_KEY" + suffix + "="):
            key = line.split("=", 1)[1].strip()
        elif line.startswith("GLM_MODEL" + suffix + "="):
            model = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit(f"modelhub/.env 缺 GLM_API_KEY{suffix}")
    return base or base0 or "https://open.bigmodel.cn/api/coding/paas/v4", \
        key, model or "glm-5.3-flash"


class GlmClient:
    """glm-5.3-flash（thinking 模型，max_tokens>=8192）。

    429 治理（全量跑实测 2026-08-29：裸并发 24-40 时 42% 实体打满重试后 llm_error）：
    - 并发闸（默认 16，--glm-concurrency 可调）；
    - **全局冷却**：任一请求吃到 429，全体 GLM 调用暂停 15s（配额是账户级令牌桶，
      单请求退避救不了并发风暴）；
    - 退避序列 2,5,10,20,40,60 共 6 次（429 加随机抖动防同步共振）。
    """

    PAUSE_ON_429 = 15.0

    def __init__(self, base: str, key: str, max_tokens: int,
                 max_concurrency: int = 16, model: str = "glm-5.3-flash"):
        import httpx
        self.url = f"{base}/chat/completions"
        self.key = key
        self.model = model
        self.max_tokens = max_tokens
        self.sem = asyncio.Semaphore(max(1, max_concurrency))
        self._pause_until = 0.0
        self.client = httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(connect=10.0, read=300.0,
                                                   write=30.0, pool=10.0))

    async def _respect_pause(self):
        while True:
            wait = self._pause_until - time.time()
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 20.0))

    async def chat(self, system: str, user: str) -> tuple:
        payload = {"model": self.model, "temperature": 0.2,
                   "max_tokens": self.max_tokens,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        last = None
        backoff = [2, 5, 10, 20, 40, 60]
        for attempt in range(len(backoff) + 1):
            try:
                await self._respect_pause()
                async with self.sem:
                    await self._respect_pause()
                    r = await self.client.post(
                        self.url, json=payload,
                        headers={"Authorization": f"Bearer {self.key}",
                                 "Content-Type": "application/json"})
                if r.status_code == 429:          # 账户级限流：全局冷却
                    self._pause_until = max(self._pause_until,
                                            time.time() + self.PAUSE_ON_429)
                    raise RuntimeError("HTTP 429")
                if r.status_code in (500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {r.status_code}")
                r.raise_for_status()
                j = r.json()
                msg = j["choices"][0]["message"]
                return (msg.get("content") or ""), j.get("usage", {})
            except Exception as e:                                  # noqa: BLE001
                last = e
                if attempt < len(backoff):
                    jitter = 0.5 + attempt * 0.5
                    await asyncio.sleep(backoff[attempt]
                                        + (jitter if "429" in str(last) else 0))
        raise RuntimeError(f"GLM 调用重试用尽: {last}")

    async def aclose(self):
        await self.client.aclose()


# ---------------------------------------------------------------------------
# 合成 prompt（骨架移植自 src_probe/synth_test.py，实测三实体+喂错证据拒卡）
# ---------------------------------------------------------------------------

CARD_SYSTEM = """你是实体视觉知识卡撰写员，为文生图评测供给视觉知识。规则：
1. 只依据用户给的检索证据撰写，禁止使用证据之外的知识补充任何具体事实（颜色/数量/年代/人名等）。
2. 每条视觉特征必须标注支撑它的证据编号（evidence 字段），可多引。
3. 特征必须是"画面上可见"的：外观、结构、材质、颜色、姿态、典型场景、组成部件。
4. 证据不足以确定的关键视觉点，列入 visual_gaps，不要编造。
5. 若证据与实体明显无关、或不足以建立该实体的基本视觉定义，输出 {"reject": "原因"} 拒绝出卡。
只输出 JSON：
{"definition": "一句话定义(≤60字)",
 "canonical_features": [{"feature": "视觉特征描述", "evidence": ["E1"]}],
 "visual_gaps": ["证据不足以确认的视觉点"]}"""

CARD_SYSTEM_ONESHOT = CARD_SYSTEM.replace(
    '只输出 JSON：\n{"definition": "一句话定义(≤60字)",',
    '只输出 JSON：\n{"definition": "一句话定义(≤60字)",\n "desc": "150~350字中文desc草稿：维基百科词条风格、具体知识点、视觉可锚定优先（外观/结构/材质/颜色/典型场景），不采历史沿革/轶闻，只用卡内事实",')

DESC_SYSTEM = """你是实体知识库 desc 撰写员。基于给定视觉知识卡（其特征已证据锚定）转写一条中文 desc 草稿。规则：
1. 150~350 字，维基百科词条风格：说明它是什么、来源/类别、核心视觉特征与典型场景，须有具体知识点，拒绝空话套话。
2. 视觉可锚定优先：外观/结构/材质/颜色/姿态/典型场景/canonical 特征；历史沿革/票房/轶闻不采。
3. 只能使用卡内信息，不得新增任何事实；卡内没有的写进句子会视为幻觉。
只输出 JSON：{"desc": "..."}"""


# ---------------------------------------------------------------------------
# 源注册表（数据化的源池）+ LLM 源规划器（动态选源/查询词）+ 源健康账本（源进源出）
#
# 架构边界（2026-08-29 拍板）：「选源」动态——规划器按实例从注册表里挑源并给
# 查询词；「源池」进出——新源 = 探针验证后在此登记一格（连接器+描述），慢源/
# 死源由 SourceHealth 滚动命中率自动停用、冷却到点探活恢复；LLM 不裸发明端点
# （通道/限速/命中校验必须过探针，防投毒防封禁）。动态发现新站点走 SERP
# site: 定向（plan.serp），在安全通道内。
# ---------------------------------------------------------------------------

DIRECT_REGISTRY = {
    "wiki_zh":  ("中文维基百科条目 intro（查询=实体中文名或规范条目名）", "zh"),
    "wiki_en":  ("英文维基百科条目 intro（查询须英文：英文名/学名）", "en"),
    "bangumi":  ("Bangumi 中文 ACG 库：日本动画/漫画/轻小说/游戏作品与角色（中文或日文名）", "zh"),
    "moegirl":  ("萌娘百科：ACG 角色/作品中文设定与外观描述，视觉细节最丰富（条目名常为『角色名』或『角色名（作品名）』）", "zh"),
    "inaturalist": ("iNaturalist 物种库：动物/植物/真菌的分类阶元+学名（查询必须拉丁学名或英文俗名，如 humpback whale）", "en"),
    "anilist":  ("AniList：日本动画/漫画角色与作品英文库（角色查询用罗马字全名，如 Rei Furuya）", "en"),
    "jikan":    ("MyAnimeList：角色小传与动画简介（查询用罗马字/英文名）", "en"),
    "steam":    ("Steam 商店：PC/主机电子游戏本体，含中文官方简介（查询用英文官方游戏名；手游/页游/电竞俱乐部无效）", "en"),
    "openlibrary": ("OpenLibrary 书目：书籍/绘本/文学作品（查询用英文书名或书系名）", "en"),
    "dbpedia":  ("DBpedia 英文实体百科：地标/品牌/产品/组织/历史事物补充（查询用英文名）", "en"),
    "wikidata": ("Wikidata 项：中文实体名精确匹配出中文描述+英文标签并桥接英文维基（查询=中文实名，无英文名的实体用）", "zh"),
}

PLANNER_SYSTEM = """你是检索源规划器。给定一个实体（名字/挂载路径/别名候选/合格图数），从可用源清单中选出本次值得查询的源并为每个源给出最优查询词；可选地改写搜索引擎查询。

决策规则：
1. 每个源请求都有成本，只选真正可能命中的源（通常 0~4 个）。抽象概念/泛类词（如「纪录片」「综艺节目」「属性词」）不选任何源，serp 也留空。
2. 查询词决定命中率，按源的命名习惯给词：inaturalist 用拉丁学名或英文俗名；anilist/jikan 用罗马字全名（姓+名顺序不限）；moegirl 用『角色名』或『角色名（作品名）』；steam 用英文官方游戏名；wikidata 用中文实名。
3. 优先复用实体自带别名候选里已有的词，也可以自己生成更准的词（日文罗马字、英文通名、消歧义后缀如「角色名（作品名）」）。
4. serp 改写：仅当默认查询（中文实名）明显不佳时给出（如分词歧义、需要 site: 限定某个资料站、需要英文名查英文资料）；每引擎最多一条，不需要改就不写。
5. 清单之外的源 id 不要写。
只输出 JSON：
{"sources": [{"id": "源id", "query": "查询词"}],
 "serp": [{"engine": "sm|so360|bing|ddg", "q": "查询"}],
 "note": "一句话理由"}"""


async def plan_sources(glm: GlmClient, target: dict, src_health) -> dict | None:
    """LLM 源规划：返回 {sources:[{id,query}], serp:[{engine,q}], note}；
    规划失败/JSON 无效返回 None（调用方回退规则路由）。只提供未停用的源。"""
    avail = {sid: desc for sid, (desc, _lang) in DIRECT_REGISTRY.items()
             if src_health is None or src_health.usable(sid)}
    user = json.dumps({
        "entity": target["name"], "domain": target.get("domain", ""),
        "paths": target.get("paths") or [],
        "alias_candidates": target.get("aliases") or [],
        "qual_images": target.get("img_count", 0),
        "可用源清单": avail}, ensure_ascii=False)
    try:
        content, _ = await glm.chat(PLANNER_SYSTEM, user)
        plan = extract_json(content)
    except Exception as e:                                          # noqa: BLE001
        log.debug("planner %s 失败: %s", target["name"], str(e)[:120])
        return None
    if not isinstance(plan, dict):
        return None
    picks, seen = [], set()
    for s in (plan.get("sources") or [])[:6]:
        if not isinstance(s, dict):
            continue
        sid, q = str(s.get("id") or "").strip(), str(s.get("query") or "").strip()
        if sid in avail and sid not in seen and len(q) >= 2:
            seen.add(sid)
            picks.append({"id": sid, "query": q})
    serp = {}
    for s in (plan.get("serp") or [])[:4]:
        if not isinstance(s, dict):
            continue
        e, q = str(s.get("engine") or "").strip(), str(s.get("q") or "").strip()
        if e in ENGINES and len(q) >= 2:
            serp[e] = q
    return {"sources": picks, "serp": serp,
            "note": str(plan.get("note") or "")[:80]}


class SourceHealth:
    """直供源滚动健康账本（跨运行持久化 source_health.json，有读取消费者）：
    窗口命中过低自动停用（源出），冷却到点自动恢复（源进）。"""

    MIN_CALLS = 25          # 停用判定的最小样本
    HIT_FLOOR = 0.04        # 窗口命中率下限
    COOLDOWN = 3600         # 停用时长（秒）

    def __init__(self, path: Path):
        self.path = path
        self.stats = {}          # sid -> {calls, hits, susp_until}
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.stats = {k: v for k, v in doc.items()
                          if isinstance(v, dict) and "calls" in v}
        except Exception:                                            # noqa: BLE001
            pass

    def _rec(self, sid):
        return self.stats.setdefault(sid, {"calls": 0, "hits": 0,
                                           "susp_until": 0})

    def usable(self, sid: str) -> bool:
        return self._rec(sid)["susp_until"] <= time.time()

    def record(self, sid: str, hit: bool):
        r = self._rec(sid)
        r["calls"] += 1
        r["hits"] += 1 if hit else 0
        if r["calls"] >= self.MIN_CALLS and r["susp_until"] <= time.time():
            if r["hits"] / r["calls"] < self.HIT_FLOOR:
                r["susp_until"] = time.time() + self.COOLDOWN
                log.warning("直供源 %s 命中率 %.1f%%（%d/%d），停用 %d 分钟",
                            sid, r["hits"] / r["calls"] * 100, r["hits"],
                            r["calls"], self.COOLDOWN // 60)

    def save(self):
        """原子落盘（tmp+rename，防另一并行进程读到半截 JSON）+ 跨进程 max 合并：
        各进程计数只增不减，calls/hits/susp_until 取 max 即并集高水位（停用最保守）。"""
        try:
            cur = {}
            try:
                cur = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:                                            # noqa: BLE001
                pass
            merged = {k: dict(v) for k, v in self.stats.items()}
            for sid, v in (cur or {}).items():
                if not (isinstance(v, dict) and "calls" in v):
                    continue
                m = merged.setdefault(sid, {"calls": 0, "hits": 0,
                                            "susp_until": 0})
                for k in ("calls", "hits", "susp_until"):
                    m[k] = max(m.get(k, 0), v.get(k, 0))
            tmp = self.path.with_name(f"{self.path.name}.tmp{os.getpid()}")
            tmp.write_text(
                json.dumps(merged, ensure_ascii=False, indent=1),
                encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:                                          # noqa: BLE001
            log.debug("source_health 保存失败: %s", e)


BRANCH_PLAN_SYSTEM = """你是分支检索源规划器。给定一个标签树分支（路径）和该分支下的实体样例，从可用源清单选出适合该分支的源，并为每个源指定查询词策略。

规则：
1. 看样例实体的性质选源（样例即分支的代表）；通常 1~4 个源。
2. 查询词策略只能取两种：{"q": "name"}（用实体名，适合中文库/wiki_zh/wikidata）或 {"q": "alias"}（用英文别名/实体名，适合英文源——zh 轨 alias 缺失时英文源会自动跳过）。
3. 抽象概念分支（属性词/学科/动作/声音）选空列表 []。
4. 清单外的源 id 不要写。
只输出 JSON：
{"sources": [{"id": "源id", "q": "name|alias"}],
 "note": "一句话理由"}"""


class BranchPlanner:
    """分支级源集决策（运行时按分支决策，2026-08-29 拍板）：
    - L2 分支一次 LLM 决策（样例实体带上下文），缓存 branch_plans.jsonl；
    - (分支×源) 滚动命中率自适应剪枝：同分支连续 0 命中的源停打该分支
      （全局 SourceHealth 管不到"anilist 对动画分支好、对器物分支零用"的粒度）；
    - 尾量实体用分支源集+机械查询词（零实例 GLM 成本拿到聪明路由）；
      池内实体仍走实例规划器（造词收益保留）。"""

    PRUNE_MIN_CALLS = 12     # 分支内剪枝最小样本
    PRUNE_FLOOR = 0.08       # 分支内命中率下限

    def __init__(self, glm: GlmClient, out_dir: Path, src_health: SourceHealth):
        self.glm = glm
        self.src_health = src_health
        self.cache_path = out_dir / "branch_plans.jsonl"
        self.plans = {}          # branch -> plan dict
        self.bstats = {}         # "branch\u0001sid" -> [calls, hits]
        try:
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.plans[r["branch"]] = r["plan"]
        except Exception:                                            # noqa: BLE001
            pass

    async def get(self, branch: str, samples: list) -> dict | None:
        """分支计划（LLM 决策一次并缓存）；samples=[(name, alias), ...] ≤10。"""
        if branch in self.plans:
            return self.plans[branch]
        avail = {sid: desc for sid, (desc, _l) in DIRECT_REGISTRY.items()
                 if self.src_health.usable(sid)}
        user = json.dumps({"branch": branch, "实体样例": [
            {"name": n, "alias": a or ""} for n, a in samples[:10]],
            "可用源清单": avail}, ensure_ascii=False)
        try:
            content, _ = await self.glm.chat(BRANCH_PLAN_SYSTEM, user)
            plan = extract_json(content)
        except Exception as e:                                      # noqa: BLE001
            log.debug("branch planner %s 失败: %s", branch, str(e)[:100])
            return None
        if not isinstance(plan, dict):
            return None
        picks = []
        for s in (plan.get("sources") or [])[:5]:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").strip()
            q = s.get("q") if s.get("q") in ("name", "alias") else "name"
            if sid in DIRECT_REGISTRY and sid not in [p["id"] for p in picks]:
                picks.append({"id": sid, "q": q})
        rec = {"sources": picks, "note": str(plan.get("note") or "")[:80]}
        self.plans[branch] = rec
        try:
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"branch": branch, "plan": rec},
                                   ensure_ascii=False) + "\n")
        except Exception as e:                                      # noqa: BLE001
            log.debug("branch plan 落盘失败: %s", e)
        return rec

    def picks_for(self, target: dict, plan: dict | None) -> list:
        """分支计划 → 实体 picks（机械查询词）；含 (分支×源) 剪枝与全局停用过滤。"""
        if not plan:
            return _rule_picks(target)
        name, alias = target["name"], target["alias"]
        out = []
        for p in plan.get("sources") or []:
            sid = p["id"]
            if not self.src_health.usable(sid):
                continue
            key = f"{target.get('branch', '')}\u0001{sid}"
            calls, hits = self.bstats.get(key, (0, 0))
            if calls >= self.PRUNE_MIN_CALLS and hits / calls < self.PRUNE_FLOOR:
                continue                       # 该分支下此源持续零命中：剪掉
            q = alias if (p.get("q") == "alias" and alias) else name
            out.append({"id": sid, "query": q})
        return out or _rule_picks(target)

    def record(self, branch: str, sid: str, hit: bool):
        key = f"{branch}\u0001{sid}"
        c, h = self.bstats.get(key, (0, 0))
        self.bstats[key] = (c + 1, h + (1 if hit else 0))


# ---------------------------------------------------------------------------
# 目标选择（消费者倒排：质量门合格图覆盖 × 非 curated，29 域轮转）
# ---------------------------------------------------------------------------

_ZH_ACG_MARKS = ("动漫", "动画", "漫画", "轻小说", "虚拟角色", "游戏角色", "电子游戏",
                 "虚构世界")
ACG_MARKS = _ZH_ACG_MARKS
_ZH_GAME_MARKS = ("游戏作品",)   # Steam 路由（游戏本体挂 内容作品/游戏作品/*；
                                 # 电竞赛事/俱乐部不查 Steam）
GAME_MARKS = _ZH_GAME_MARKS
_ZH_BOOK_MARKS = ("文学", "小说", "书籍", "绘本", "漫画")                     # OpenLibrary 路由
BOOK_MARKS = _ZH_BOOK_MARKS
_ZH_SPECIES_DOMAINS = ("动物", "植物", "真菌与微生物")                       # inaturalist 路由
SPECIES_DOMAINS = _ZH_SPECIES_DOMAINS
_ZH_SERP_ENGINES = ("sm", "so360", "bing", "ddg")   # 中文四引擎（神马/360 中文强）
SERP_ENGINES = _ZH_SERP_ENGINES                     # 当前赛道引擎集（set_lang 切换）
FOREIGN_PAGE_DOMAINS = ("wikipedia.org", "wikimedia.org", "wikidata.org",
                        "fandom.com", "bgm.tv", "bangumi.tv", "openlibrary.org",
                        "britannica.com", "dbpedia.org", "steamcommunity.com",
                        "steampowered.com")
PAGE_BLACKLIST = ("baike.baidu.com", "zhihu.com", "baidu.com", "douyin.com",
                  "weibo.com", "tieba.baidu.com", "v.qq.com", "baike.com")
PAGE_PREF = {"baike.so.com": 0, "wikipedia.org": 0, "moegirl.org.cn": 0,
             "britannica.com": 1, "fandom.com": 1}


def domain_of(mounts: dict, name: str) -> str:
    for p in mounts.get(name) or []:
        segs = [s.strip() for s in p.split(" / ")]
        if len(segs) >= 2:
            return segs[1]
    return "未挂载"


def branch_of(mounts: dict, name: str) -> str:
    """L2 分支（'域 / 二级'）：分支级选源决策的键。多挂载取首条路径。"""
    for p in mounts.get(name) or []:
        segs = [s.strip() for s in p.split(" / ")]
        if len(segs) >= 3:
            return f"{segs[1]} / {segs[2]}"
        if len(segs) == 2:
            return segs[1]
    return "未挂载"


def is_acg(mounts: dict, name: str) -> bool:
    return any(any(m in p for m in ACG_MARKS) for p in (mounts.get(name) or []))


def is_game(mounts: dict, name: str) -> bool:
    return any(any(m in p for m in GAME_MARKS) for p in (mounts.get(name) or []))


def is_book(mounts: dict, name: str) -> bool:
    return any(any(m in p for m in BOOK_MARKS) for p in (mounts.get(name) or []))


def english_alias(inst: dict) -> str:
    """实例的英文别名（aliases/query 里首个 ASCII 主词），无则空串。"""
    cands = list(inst.get("aliases") or []) + list(inst.get("query") or [])
    for c in cands:
        c = str(c).strip()
        if not c:
            continue
        toks = re.findall(r"[A-Za-z][A-Za-z0-9' -]*", c)
        if toks and len("".join(toks)) >= max(3, int(len(c) * 0.7)):
            return c
    return ""


def all_aliases(inst: dict) -> list:
    """全部别名/检索扩展词（源规划器的候选词池，含中文，去重保序，≤10 个）。"""
    out, seen = [], set()
    for c in [*list(inst.get("aliases") or []), *list(inst.get("query") or [])]:
        c = str(c).strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:10]


def load_targets(args) -> list:
    """目标实体列表，29 域轮转排序（跨域均衡；域内先无 desc、后按合格图数降序）。

    --all：全量口径（2026-08-29 拍板）——不做质量门过滤、包含 curated，
    且**合格图池实体排前面、24 万尾量 derived 殿后**（消费优先：跑不完时
    先损耗的是无消费者的尾量）。默认口径不变（合格图覆盖 × 非 curated）。
    英文赛道（--lang en）：实例无图无别名，跳过 metadata.jsonl 合格图扫描，
    alias=实体名本身。"""
    qual = Counter()
    if LANG == "zh":
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("quality") or 0) >= 8 and r.get("identity"):
                    for i in r.get("instances") or []:
                        qual[i] += 1
    doc = json.load(open(INSTANCES_PATH, encoding="utf-8"))
    mounts = load_mount_map(str(TAXONOMY_PATH))
    wanted = None
    if args.entities:
        wanted = {x.strip() for x in args.entities.split(",") if x.strip()}
    by_domain = defaultdict(list)
    for it in doc.get("instances", []):
        name = it.get("name", "")
        if not name:
            continue
        if wanted is not None:
            if name not in wanted:
                continue
        else:
            if not args.all:
                if qual.get(name, 0) == 0:       # 质量门合格图覆盖外（抽样池外）
                    continue
                if it.get("source") == "curated":
                    continue
            if args.only_empty and it.get("desc"):
                continue
        if args.domains:
            doms = [x.strip() for x in args.domains.split(",") if x.strip()]
            if not any(x in domain_of(mounts, name) for x in doms):
                continue
        by_domain[domain_of(mounts, name)].append(
            (name, qual.get(name, 0), is_acg(mounts, name),
             english_alias(it) if LANG == "zh" else name, bool(it.get("desc")),
             is_game(mounts, name), is_book(mounts, name),
             all_aliases(it), list(mounts.get(name) or [])[:3],
             branch_of(mounts, name)))
    for d in by_domain:
        by_domain[d].sort(key=lambda x: (0 if not x[4] else 1, -x[1], x[0]))

    def emit(sub_by_domain: dict) -> list:
        order = sorted(sub_by_domain, key=lambda d: -len(sub_by_domain[d]))
        out, idx = [], 0
        while True:
            added = False
            for d in order:
                if idx < len(sub_by_domain[d]):
                    (name, n, acg, alias, has_desc, game, book, ali, paths,
                     branch) = sub_by_domain[d][idx]
                    out.append({"name": name, "domain": d, "img_count": n,
                                "acg": acg, "alias": alias, "has_desc": has_desc,
                                "game": game, "book": book, "aliases": ali,
                                "paths": paths, "branch": branch,
                                "species": d in SPECIES_DOMAINS})
                    added = True
            if not added:
                break
            idx += 1
        if getattr(args, "by_branch", False):
            # by 分支跑：片内实体同构（源/引擎模式稳定，分支自适应剪枝生效快）；
            # 池先行语义保留（emit(pool) 在前），分支间按首个实体原相对序
            heads = {}
            for k, t in enumerate(out):
                heads.setdefault(t["branch"], k)
            out.sort(key=lambda t: heads[t["branch"]])
        return out

    if not args.all:
        return emit(by_domain)
    pool = {d: [r for r in v if r[1] > 0] for d, v in by_domain.items()}
    pool = {d: v for d, v in pool.items() if v}
    tail = {d: [r for r in v if r[1] == 0] for d, v in by_domain.items()}
    tail = {d: v for d, v in tail.items() if v}
    return emit(pool) + emit(tail)          # 合格图池先行，尾量殿后


# ---------------------------------------------------------------------------
# 证据采集
# ---------------------------------------------------------------------------

_unwrap_client = None      # 不跟随重定向的解包专用客户端（so.com/bing 均直连）


async def fetch_url(source: str, url: str):
    """限速抓取，返回 httpx.Response；失败返回 None（不抛）。"""
    try:
        return await infra_request(source, "GET", url, headers=HTTP_HEADERS)
    except (DeterministicError, TransientExhaustedError) as e:
        log.debug("fetch fail %s %s: %s", source, url[:80], str(e)[:120])
        return None
    except Exception as e:                                          # noqa: BLE001
        log.debug("fetch error %s %s: %s", source, url[:80], str(e)[:120])
        return None


async def unwrap_link(engine: str, url: str) -> str:
    """SERP 包装链接解包：一次 redirect（Location）；失败原样返回。"""
    global _unwrap_client
    try:
        gate = infra.gate_for(f"sk:{engine}")
        async with gate.slot():
            if _unwrap_client is None:
                import httpx
                _unwrap_client = httpx.AsyncClient(follow_redirects=False,
                                                   timeout=15.0)
            r = await _unwrap_client.get(url, headers={"User-Agent": UA})
        loc = r.headers.get("location", "")
        return loc if loc.startswith("http") else url
    except Exception:                                                # noqa: BLE001
        return url


async def wiki_intro(lang: str, title: str) -> str:
    """wiki 词条 intro（action API extracts，代理；search 端点 403 已实测，勿用）。"""
    q = urllib.parse.quote(title.replace(" ", "_"))
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json"
           f"&prop=extracts&exintro=1&explaintext=1&redirects=1&titles={q}")
    resp = await fetch_url("sk:wiki", url)
    if resp is None:
        return ""
    try:
        pages = json.loads(resp.text)["query"]["pages"]
        page = next(iter(pages.values()))
        if page.get("missing"):
            return ""
        ext = page.get("extract") or ""
        if "消歧义" in ext[:200] or "may refer to" in ext[:300] \
                or len(ext) < 80:
            return ""                 # 消歧义页（中/英）与过短 intro 不入包
        return ext[:PAGE_TEXT_LIMIT]
    except Exception:                                               # noqa: BLE001
        return ""


async def moegirl_page(name: str) -> tuple:
    q = urllib.parse.quote(name.replace(" ", "_"), safe="")
    url = f"https://zh.moegirl.org.cn/{q}"
    resp = await fetch_url("sk:moegirl", url)
    if resp is None:
        return "", url
    body = resp.text
    if len(body) < 20000 or "搜索结果" in body[:3000]:
        return "", url
    return mediawiki_text(body), url


async def bangumi_search(name: str) -> str:
    q = urllib.parse.quote(name)
    resp = await fetch_url("sk:bgm", f"https://api.bgm.tv/search/subject/{q}")
    if resp is None:
        return ""
    try:
        results = json.loads(resp.text).get("list") or []
    except Exception:                                               # noqa: BLE001
        return ""
    best = None
    for r in results:
        if r.get("name_cn") == name or r.get("name") == name:
            best = r
            break
    if best is None:
        for r in results:
            if name in (r.get("name_cn") or ""):
                best = r
                break
    if not best:
        return ""
    summary = (best.get("summary") or "").strip()
    if len(summary) < 60:
        return ""
    label = best.get("name_cn") or best.get("name") or name
    return f"{label}（Bangumi 条目）：{summary}"[:1500]


async def serp_query(engine: str, query: str, health: EngineHealth) -> list | None:
    """单引擎 SERP：抓取→解析→逐条词面过滤。返回 None=引擎失败；[]=零相关弃页。"""
    tpl, parser = ENGINES[engine]
    keys = query_keys(query)
    variants = [query]
    if engine == "bing":
        variants.append(f'"{query}"')       # bing 分词崩坏变体（加引号）
    for vi, v in enumerate(variants):
        url = tpl.replace("{Q}", urllib.parse.quote(v))
        resp = await fetch_url(f"sk:{engine}", url)
        # ddg 202 = 反爬挑战页（结构空壳）：随机退避后重试
        for attempt in range(3):
            if resp is not None and resp.status_code == 202 and engine == "ddg":
                await asyncio.sleep(3 + 2 ** attempt + (attempt * 1.5))
                resp = await fetch_url(f"sk:{engine}", url)
            else:
                break
        if resp is None:
            health.fail(engine)
            return None
        body = resp.text
        items = parser(body)[:SERP_KEEP]
        if not items:
            if len(body) < 20000:            # 疑似反爬空壳
                health.fail(engine)
                return None
            health.gate_drop(engine)
            return []
        rel = [it for it in items
               if text_relevant(f"{it[0]} {it[2]}", keys)]
        if rel:
            health.ok(engine)
            return rel
        # 命名不一致二次放宽：整页零首键相关时，放宽键只对标题匹配（摘要域杂讯多）
        rkeys = relax_keys(query)
        if rkeys:
            low_keys = [k.lower() for k in rkeys]
            rel2 = [it for it in items
                    if any(k in it[0].lower() for k in low_keys)]
            if rel2:
                health.stats[f"{engine}:gate_relax"] += 1
                health.ok(engine)
                return rel2
        if vi < len(variants) - 1:
            continue                         # 引号变体重试
        health.gate_drop(engine)             # 整页零词面相关：弃页（防投毒）
        return []


def page_channel(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if any(d in host for d in FOREIGN_PAGE_DOMAINS):
        return "sk:page_en"
    return "sk:page_zh"


def page_rank(url: str) -> int:
    host = urllib.parse.urlparse(url).netloc.lower()
    for d, p in PAGE_PREF.items():
        if d in host:
            return p
    return 2


def page_allowed(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    if not host:
        return False
    return not any(d in host for d in PAGE_BLACKLIST)


def _rule_picks(target: dict) -> list:
    """规则路由兜底（planner 失败/关闭时）：[{id, query}]。zh：上午版逻辑；
    en：纯英文源（wiki_zh/bangumi/moegirl/wikidata 不适用，实例名即查询词）。"""
    name, alias, acg = target["name"], target["alias"], target["acg"]
    if LANG == "en":
        q = target["alias"] or name          # en 轨 alias=实体名
        picks = [{"id": "wiki_en", "query": q}, {"id": "dbpedia", "query": q}]
        if target.get("species"):
            picks.append({"id": "inaturalist", "query": q})
        if acg:
            picks += [{"id": "anilist", "query": q}, {"id": "jikan", "query": q}]
        if target.get("book"):
            picks.append({"id": "openlibrary", "query": q})
        if target.get("game"):
            picks.append({"id": "steam", "query": q})
        return picks
    picks = []
    if acg:
        picks += [{"id": "bangumi", "query": name},
                  {"id": "moegirl", "query": name}]
    picks.append({"id": "wiki_zh", "query": name})
    if alias:
        picks.append({"id": "wiki_en", "query": alias})
        if target.get("species"):
            picks.append({"id": "inaturalist", "query": alias})
        picks.append({"id": "dbpedia", "query": alias})
        if acg:
            picks += [{"id": "anilist", "query": alias},
                      {"id": "jikan", "query": alias}]
        if target.get("book"):
            picks.append({"id": "openlibrary", "query": alias})
    else:
        picks.append({"id": "wikidata", "query": name})
    if target.get("game"):
        picks.append({"id": "steam", "query": alias or name})
    return picks


def _direct_job(sid: str, query: str, name: str):
    """把 (源id, 查询词) 变成连接器协程；未知 id 返回 None。"""
    if sid == "wiki_zh":
        async def j():
            t = await wiki_intro("zh", query)
            return [{"src": "wiki_zh", "title": query,
                     "url": f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(query)}",
                     "text": t}] if t else []
    elif sid == "wiki_en":
        async def j():
            t = await wiki_intro("en", query)
            return [{"src": "wiki_en", "title": query,
                     "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(query.replace(' ', '_'))}",
                     "text": t}] if t else []
    elif sid == "bangumi":
        async def j():
            t = await bangumi_search(query)
            return [{"src": "bangumi", "title": query, "url": "api.bgm.tv",
                     "text": t}] if t else []
    elif sid == "moegirl":
        async def j():
            t, u = await moegirl_page(query)
            return [{"src": "moegirl", "title": query, "url": u,
                     "text": t}] if t else []
    elif sid == "inaturalist":
        async def j():
            return await inaturalist_source(name, query, wiki_intro)
    elif sid == "wikidata":
        async def j():
            return await wikidata_source(query, wiki_intro)
    else:
        fn = {"anilist": anilist_source, "jikan": jikan_source,
              "steam": steam_source, "openlibrary": openlibrary_source,
              "dbpedia": dbpedia_source}.get(sid)

        async def j():
            return await fn(name, query) if fn else []
    return j()


async def gather_evidence(target: dict, health: EngineHealth,
                          src_health: SourceHealth = None,
                          plan: dict = None,
                          serp_threshold: int = 0,
                          br_planner: BranchPlanner = None) -> dict:
    """证据包采集：直供源（实例规划器/分支计划/规则三层之一）+ SERP（planner 可
    改写查询）+ 白名单正文。serp_threshold>0 时直供证据已达标则整段跳过
    SERP/正文（全量跑的引擎配额优先给弱证据实体）。"""
    name, alias, acg = target["name"], target["alias"], target["acg"]
    en_query = alias or name
    ev = []

    def add(src, title, url, text):
        if text and len(text) >= 60:
            ev.append({"src": src, "title": title, "url": url, "text": text})

    # ---- 直供源（并发；各走各的闸门）。
    # picks 语义：plan（实例规划器或分支计划）给定则照单执行（含空单=该实体
    # 不查直供源，抽象分支的合法决策）；None=规则兜底。
    picks = plan["sources"] if plan else _rule_picks(target)
    jobs, sids = [], []
    for p in picks:
        sid = p["id"]
        if src_health is not None and not src_health.usable(sid):
            continue                    # 健康账本停用中的源：计划里有也不放行
        jobs.append(_direct_job(sid, p["query"], name))
        sids.append(sid)
    direct_res = await asyncio.gather(*jobs) if jobs else []
    if src_health is not None:
        for sid, evs in zip(sids, direct_res):
            src_health.record(sid, bool(evs))
            if br_planner is not None:
                br_planner.record(target.get("branch", ""), sid, bool(evs))
    for evs in direct_res:
        for e in evs or []:
            add(e["src"], e["title"], e["url"], e["text"])

    # ---- SERP（活引擎并发，各走各的 1.2s 闸门；planner 可改写查询；
    #      引擎集按赛道：zh=神马/360/bing+ddg(英文别名)，en=bing+ddg 全英文）
    default_q = {e: (en_query if e == "ddg" else name) for e in SERP_ENGINES}
    if plan and plan.get("serp"):
        default_q.update({e: q for e, q in plan["serp"].items()
                          if e in SERP_ENGINES})
    engine_jobs = {}
    if not (serp_threshold > 0 and len(ev) >= serp_threshold):
        for engine, q in default_q.items():
            if health.alive(engine):
                engine_jobs[engine] = serp_query(engine, q, health)
        serps = await asyncio.gather(*engine_jobs.values()) if engine_jobs else []
    else:
        serps = []

    # ---- 正文抓取候选：相关结果解包真实 URL 后按域偏好取 top2
    covered_hosts = {urllib.parse.urlparse(e["url"]).netloc for e in ev}
    cand = []
    for engine, res in zip(engine_jobs.keys(), serps):
        if not res:
            continue
        top = []
        for title, url, snip in res[:UNWRAP_KEEP]:
            if engine == "ddg":
                url = unwrap_ddg(url)
            elif "so.com/link" in url or "bing.com/ck/" in url:
                url = await unwrap_link(engine, url)
            top.append((url, title))
        cand.extend(top)
    cand = [(u, t) for u, t in cand if page_allowed(u)]
    cand.sort(key=lambda x: page_rank(x[0]))
    seen_hosts, to_fetch = set(), []
    for url, title in cand:
        host = urllib.parse.urlparse(url).netloc
        if host in covered_hosts or host in seen_hosts:
            continue
        if len(to_fetch) >= MAX_PAGES:
            break
        seen_hosts.add(host)
        to_fetch.append((url, title))
    pages_fetched = []
    fetched = await asyncio.gather(*[
        fetch_url(page_channel(u), u) for u, _ in to_fetch]) if to_fetch else []
    for (url, title), resp in zip(to_fetch, fetched):
        if resp is None:
            continue
        body = resp.text
        if len(body) <= 5000:
            continue
        final_url = str(resp.url)
        host = urllib.parse.urlparse(final_url).netloc
        if not page_allowed(final_url):     # 重定向落进死墙域（如 baike.baidu）
            continue
        text = mediawiki_text(body) if ("wiki" in host or "moegirl" in host) \
            else html_text(body)
        if len(text) >= 300:
            add("page", title[:60], final_url, text)
            pages_fetched.append(host)

    # ---- SERP 摘要入包（每引擎 top SNIPPET_KEEP，按引擎序）
    for engine, res in zip(engine_jobs.keys(), serps):
        if not res:
            continue
        for title, url, snip in res[:SNIPPET_KEEP]:
            if not snip:
                continue
            if engine == "ddg":
                url = unwrap_ddg(url)
            add(f"serp:{engine}", title, url, snip)
        if len(ev) >= MAX_EVIDENCE:
            break

    evidence = ev[:MAX_EVIDENCE]
    for k, e in enumerate(evidence, 1):
        e["id"] = f"E{k}"
    meta = {"pages": pages_fetched,
            "engines_ok": [e for e, r in zip(engine_jobs.keys(), serps) if r],
            "engines_drop": [e for e, r in zip(engine_jobs.keys(), serps)
                             if r == []],
            "direct": sids}
    if plan is not None:
        meta["planner"] = True
        meta["plan_note"] = plan.get("note", "")
        if plan.get("serp"):
            meta["serp_q"] = plan["serp"]
    return {"entity": name, "context": f"域：{target['domain']}",
            "evidence": evidence, "meta": meta}


# ---------------------------------------------------------------------------
# 合成 + gate + 产物落盘
# ---------------------------------------------------------------------------


def gate_card(card: dict, ev_ids: set) -> tuple:
    """机审 gate：结构完备 + 证据编号合法 + 特征有锚定。返回 (ok, 原因)。"""
    if not isinstance(card, dict):
        return False, "card 非 JSON 对象"
    if card.get("reject"):
        return False, f"模型拒卡: {str(card['reject'])[:80]}"
    definition = (card.get("definition") or "").strip()
    if not definition:
        return False, "缺 definition"
    feats = card.get("canonical_features") or []
    if not feats:
        return False, "零特征"
    bad = [f.get("evidence") for f in feats
           if any(x not in ev_ids for x in (f.get("evidence") or []))]
    if bad:
        return False, f"非法证据引用 {str(bad)[:60]}"
    if not any(f.get("evidence") for f in feats):
        return False, "特征零锚定"
    return True, ""


async def synthesize_card(glm: GlmClient, pack: dict, one_shot: bool) -> tuple:
    user = json.dumps({"entity": pack["entity"], "context": pack.get("context", ""),
                       "evidence": pack["evidence"]}, ensure_ascii=False)
    system = CARD_SYSTEM_ONESHOT if one_shot else CARD_SYSTEM
    content, usage = await glm.chat(system, user)
    card = extract_json(content)
    if card is None:
        low = content[:300]
        if any(w in low for w in ("无关", "拒绝", "无法确认", "不足以")):
            return {"reject": low[:120]}, usage
        raise RuntimeError(f"卡 JSON 解析失败: {content[:120]}")
    return card, usage


async def synthesize_desc(glm: GlmClient, entity: str, card: dict) -> tuple:
    """desc 转写；字数越界（150-350 契约）带反馈重试一次。"""
    user = json.dumps({"entity": entity, "card": card}, ensure_ascii=False)
    content, usage = await glm.chat(DESC_SYSTEM, user)
    rec = extract_json(content)
    d = (rec or {}).get("desc", "")
    if d.strip() and not (150 <= len(d.strip()) <= 350):
        retry_user = (user.rstrip("}")[:-1] +
                      f', "字数提醒": "上次产出 {len(d.strip())} 字越界，请严格控制在 150~350 字"}}')
        content2, usage2 = await glm.chat(DESC_SYSTEM, retry_user)
        rec2 = extract_json(content2)
        d2 = (rec2 or {}).get("desc", "")
        if 150 <= len(d2.strip()) <= 350:
            return d2.strip(), usage2
        d, usage = d2 or d, usage2
    if not d.strip():
        raise RuntimeError(f"desc JSON 解析失败: {content[:120]}")
    return d.strip(), usage


class Recorder:
    """产物追加写（cards/desc/evidence）。flock 跨进程互斥：双监督器分域并行
    时两进程共写同一组 jsonl（2026-08-30），锁内单条单行写。"""

    def __init__(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self._files = {}

    def append(self, path: Path, rec: dict):
        f = self._files.setdefault(path, open(path, "a", encoding="utf-8"))
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def close(self):
        for f in self._files.values():
            f.close()


def load_done_entities(max_attempts: int = MAX_ATTEMPTS) -> dict:
    """cards.jsonl 实体终态：{name: (status, attempts)}；可重试态标 'retry'。"""
    done = {}
    if not CARDS_PATH.exists():
        return done
    with open(CARDS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            prev = done.get(r["entity"])
            attempts = max(prev[1] if prev else 0, int(r.get("attempts", 1)))
            # ok/no_evidence 即终态；reject 概念类重试基本复现（2026-08-29 全量
            # 预算拍板：两跳即终态，防尾量概念实体烧配额）；其余按 max_attempts
            if (r.get("status") in ("ok", "no_evidence")
                    or attempts >= max_attempts
                    or (r.get("status") == "reject" and attempts >= 2)):
                done[r["entity"]] = (r["status"], attempts)
            else:
                done[r["entity"]] = ("retry", attempts)
    return done


def load_evidence_cache(keep: set = None) -> dict:
    """证据缓存；keep 给定时只载入待处理实体（29 万全量跑防整文件灌内存，
    已终态实体的缓存留在 evidence.jsonl 磁盘上即可）。"""
    cache = {}
    if not EVIDENCE_PATH.exists():
        return cache
    with open(EVIDENCE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ok") and r.get("rec"):
                if keep is None or r["key"] in keep:
                    cache[r["key"]] = r["rec"]
    return cache


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def process_entity(target: dict, glm: GlmClient, health: EngineHealth,
                          rec: Recorder, batch: str, ev_cache: dict,
                          refresh_evidence: bool, one_shot: bool,
                          sem: asyncio.Semaphore, src_health: SourceHealth = None,
                          use_planner: bool = True,
                          serp_threshold: int = 0,
                          br_planner: BranchPlanner = None,
                          branch_samples: dict = None) -> dict:
    name = target["name"]
    attempts = target.get("_prior_attempts", 0) + 1
    async with sem:
        t0 = time.time()
        status, card, desc, n_ev = "error", None, "", 0
        usage_total = {}
        plan = None
        try:
            pack = None if refresh_evidence else ev_cache.get(name)
            degraded = False
            if pack is None:
                if use_planner:                 # 池内：实例规划器（造词收益）
                    plan = await plan_sources(glm, target, src_health)
                elif br_planner is not None:    # 尾量：分支级决策（LLM 一次/分支+缓存）
                    branch = target.get("branch", "")
                    bplan = await br_planner.get(
                        branch, (branch_samples or {}).get(branch)
                        or [(name, target.get("alias", ""))])
                    picks = br_planner.picks_for(target, bplan)
                    plan = {"sources": picks, "serp": {},
                            "note": f"branch:{branch}"}
                pack = await gather_evidence(target, health, src_health, plan,
                                             serp_threshold, br_planner)
                n_ev = len(pack.get("evidence") or [])
                degraded = n_ev == 0 and bool(health.down)
                ev_cache[name] = pack
                rec.append(EVIDENCE_PATH, {"key": name, "ok": not degraded,
                                           "rec": pack})
            else:
                n_ev = len(pack.get("evidence") or [])
            if n_ev == 0:
                status = "llm_error" if degraded else "no_evidence"
            else:
                card, usage = await synthesize_card(glm, pack, one_shot)
                usage_total = dict(usage)
                ok, why = gate_card(card, {e["id"] for e in pack["evidence"]})
                if not ok and why.startswith("模型拒卡"):
                    status = "reject"
                elif not ok:
                    status = "gate_fail"
                    log.warning("gate_fail %s: %s", name, why)
                else:
                    status = "ok"
                    if one_shot and (card.get("desc") or "").strip():
                        desc = card["desc"].strip()
                    else:
                        desc, usage2 = await synthesize_desc(glm, name, card)
                        usage_total.update({f"desc_{k}": v for k, v in usage2.items()})
        except Exception as e:                                      # noqa: BLE001
            status = "llm_error"
            log.warning("entity %s 异常: %s", name, str(e)[:200])

        out = {"entity": name, "batch": batch, "status": status,
               "attempts": attempts, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "domain": target["domain"], "img_count": target["img_count"],
               "has_desc": target["has_desc"], "n_evidence": n_ev,
               "secs": round(time.time() - t0, 1), "usage": usage_total}
        if plan is not None:
            out["planned"] = [p["id"] for p in plan["sources"]]
        if card is not None and status in ("ok", "reject"):
            out["card"] = card
        rec.append(CARDS_PATH, out)
        if status == "ok" and desc:
            rec.append(DESC_PATH, {"entity": name, "batch": batch, "desc": desc,
                                   "definition": card.get("definition", "")})
        return out


async def run(args) -> None:
    set_lang(args.lang)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lane = os.environ.get("SEARCH_KB_LANE", "").strip()
    log_path = OUT_DIR / f"run.{lane}.log" if lane else LOG_PATH
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)     # 降噪：httpx 每请求一行 INFO

    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        if os.environ.get(var):
            log.warning("检测到 %s 环境变量，直连引擎将被代理！按 AGENTS.md §7 先清理", var)

    done = load_done_entities(args.max_attempts)
    targets = load_targets(args)
    targets = [t for t in targets
               if t["name"] not in done or done[t["name"]][0] == "retry"]
    for t in targets:
        t["_prior_attempts"] = done.get(t["name"], ("", 0))[1]
    if args.shard:
        i, n = args.shard
        targets = [t for k, t in enumerate(targets) if k % n == i]
    if args.limit:
        targets = targets[:args.limit]
    log.info("目标实体 %d 个（批次=%s, workers=%d%s%s）", len(targets), args.batch,
             args.workers, ", one-shot" if args.one_shot else "",
             f", 分片 {i}/{n}" if args.shard else "")
    if not targets:
        log.info("无待处理实体，退出。")
        return

    if args.dry_run:
        for t in targets[: max(args.limit or 10, 10)]:
            print(f"- {t['name']} | 域={t['domain']} | 合格图={t['img_count']} | "
                  f"desc={'有' if t['has_desc'] else '无'} | ACG={t['acg']} | "
                  f"英文别名={t['alias'] or '-'}")
        print(f"[dry-run] 共 {len(targets)} 个目标，未联网未调 API。")
        return

    gb, gk, gm = load_glm_conf()
    glm = GlmClient(gb, gk, max_tokens=args.max_tokens,
                    max_concurrency=args.glm_concurrency, model=gm)
    src_health = SourceHealth(OUT_DIR / "source_health.json")
    planner_scope = "none" if args.no_planner else args.planner_scope
    br_planner = None
    if planner_scope != "all":          # 尾量走分支决策（scope=all 时全走实例规划器）
        br_planner = BranchPlanner(glm, OUT_DIR, src_health)
        log.info("分支计划缓存 %d 条", len(br_planner.plans))
    if args.plan_only:
        for t in targets[: max(args.limit or 10, 10)]:
            print(f"- {t['name']} | 域={t['domain']}")
            if args.no_planner:              # A/B：规则路由计划
                for p in _rule_picks(t):
                    print(f"    [rule] {p['id']:<12} ← {p['query']}")
                continue
            plan = await plan_sources(glm, t, src_health)
            if plan is None:
                print("    planner 失败（回退规则路由）")
            else:
                for p in plan["sources"]:
                    print(f"    {p['id']:<12} ← {p['query']}")
                if plan["serp"]:
                    print(f"    serp: {plan['serp']}")
                print(f"    note: {plan['note']}")
        await glm.aclose()
        await close_client()
        return
    if planner_scope != "none":
        susp = [s for s in DIRECT_REGISTRY if not src_health.usable(s)]
        log.info("源规划器开启（scope=%s，池 %d 源%s）", planner_scope,
                 len(DIRECT_REGISTRY),
                 f"，停用中：{susp}" if susp else "")
    health = EngineHealth()
    rec = Recorder()
    ev_cache = load_evidence_cache(
        keep={t["name"] for t in targets})
    log.info("证据缓存命中 %d 实体（仅待处理集）", len(ev_cache))
    sem = asyncio.Semaphore(max(1, args.workers))
    branch_samples = defaultdict(list)     # 分支决策的样例池（每分支 ≤10 实体）
    for t in targets:
        bs = branch_samples[t.get("branch", "")]
        if len(bs) < 10:
            bs.append((t["name"], t.get("alias", "")))

    stats = Counter()
    t_start = time.time()
    try:
        queue = asyncio.Queue()
        for t in targets:
            queue.put_nowait(t)

        async def worker():
            while True:
                try:
                    t = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                use_planner = (planner_scope == "all"
                               or (planner_scope == "pool"
                                   and t["img_count"] > 0))
                try:
                    out = await process_entity(t, glm, health, rec, args.batch,
                                               ev_cache, args.refresh_evidence,
                                               args.one_shot, sem, src_health,
                                               use_planner, args.serp_threshold,
                                               br_planner, branch_samples)
                except Exception as e:                              # noqa: BLE001
                    out = {"entity": t["name"], "status": "error"}
                    log.error("worker 处理 %s 失败: %s", t["name"], str(e)[:200])
                stats[out["status"]] += 1
                ev_cache.pop(t["name"], None)   # 已落 evidence.jsonl，防 29 万全量跑内存爬升
                done_n = sum(stats.values())
                log.info("[%d/%d] %-11s %-24s (%ss)", done_n, len(targets),
                         out["status"], out["entity"], out.get("secs", "?"))
                if done_n % 100 == 0:
                    elapsed = time.time() - t_start
                    remain = len(targets) - done_n
                    eta_h = remain / max(done_n / elapsed, 1e-6) / 3600
                    log.info("== 进度 %d/%d | %.0f 实体/小时 | ETA %.1f 天 | %s | 引擎: %s",
                             done_n, len(targets), done_n / elapsed * 3600,
                             eta_h / 24, dict(stats), health.summary())
                queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max(1, args.workers))]
        await queue.join()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        src_health.save()
        rec.close()
        await glm.aclose()
        if _unwrap_client is not None:
            await _unwrap_client.aclose()
        await close_client()

    elapsed = max(1e-6, time.time() - t_start)
    log.info("运行结束：%.0f 分钟，%d 实体 | %s | %.1f 实体/小时", elapsed / 60,
             sum(stats.values()), dict(stats), sum(stats.values()) / elapsed * 3600)
    log.info("引擎账本：%s | 引擎降级：%s", health.summary(), health.down or "无")
    log.info("直供源账本：%s", src_health and {
        s: f"{v['hits']}/{v['calls']}" for s, v in src_health.stats.items()})
    log.info("产物：%s / %s / %s", CARDS_PATH, DESC_PATH, EVIDENCE_PATH)


def main():
    ap = argparse.ArgumentParser(description="实例检索接地知识卡管线（search agent）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 实体")
    ap.add_argument("--batch", default="adhoc", help="批次标记（smoke/pilot/scale）")
    ap.add_argument("--domains", default="", help="L1 域过滤（逗号分隔子串）")
    ap.add_argument("--only-empty", action="store_true", help="只处理无 desc 实例")
    ap.add_argument("--entities", default="", help="显式实体名（逗号分隔，调试用）")
    ap.add_argument("--workers", type=int, default=3, help="并发实体数")
    ap.add_argument("--max-tokens", type=int, default=8192,
                    help="GLM max_tokens（thinking 模型下限 8192）")
    ap.add_argument("--glm-concurrency", type=int, default=16,
                    help="GLM 并发上限（配额是账户级令牌桶；实测 24-40 会 429 风暴，"
                         "16 以下配 20+ workers 让采集与合成重叠）")
    ap.add_argument("--one-shot", action="store_true",
                    help="卡与 desc 同一次调用产出（放量提速用）")
    ap.add_argument("--refresh-evidence", action="store_true",
                    help="无视证据缓存重新采集")
    ap.add_argument("--no-planner", action="store_true",
                    help="关闭 LLM 源规划器，用规则路由（A/B 对照/调试）")
    ap.add_argument("--planner-scope", choices=["all", "pool"], default="all",
                    help="规划器作用域：all=每实体规划；pool=只有合格图池实体规划"
                         "（全量跑提速：尾量 24 万 derived 规则≈规划器，省 1 次 GLM/实体）")
    ap.add_argument("--serp-threshold", type=int, default=0,
                    help="直供证据条数 ≥N 时跳过 SERP/正文（0=关；全量跑用 3 "
                         "保引擎配额给弱证据实体）")
    ap.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                    help="每实体最大尝试次数（全量跑用 4，防引擎冷却期烧尽额度）")
    ap.add_argument("--shard", metavar=("I", "N"), nargs=2, type=int, default=None,
                    help="分片 I/N（0 基）：按序取 k%%N==I 的实体。多进程并行用，"
                         "注意每进程引擎闸门独立=引擎总速率×N（ban 风险随 N 升）")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh",
                    help="赛道：zh=instances.json（默认）；en=instances_en.json "
                         "382k 英文实例，纯英文源路由（wiki_en/dbpedia/ina/steam/"
                         "ol/anilist/jikan + bing/ddg SERP），产物独立落 "
                         "state/taxonomy/search_kb_en/，可与中文轨并行")
    ap.add_argument("--by-branch", action="store_true",
                    help="按 L2 分支聚簇排序（分支决策/剪枝生效更快，片内实体同构；"
                         "跨域轮转默认关闭此序）")
    ap.add_argument("--all", action="store_true",
                    help="全量口径：不做质量门过滤、含 curated，合格图池先行尾量殿后")
    ap.add_argument("--plan-only", action="store_true",
                    help="只跑源规划器打印计划，不采集不出卡（调试用）")
    ap.add_argument("--dry-run", action="store_true", help="只列目标不联网")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
