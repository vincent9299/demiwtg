#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_docs_pages.py — docs 页（集群抓取的 URL 寻址知识正文）清洗出净版（2026-09-08）。

背景：lake_sync 把集群 docs 线抓回的页面正文同步到湖侧 datasets/demiwtg/pages/
（page_sha = sha256(url)，URL 寻址，10,020 页 / 224MB）。抽审发现质量参差：
serp 线可用率约 16%、wiki 线约 65%，坏页形态为 HTML 骨架转储 / 反爬诱饵乱码 /
JS 站空壳过短页。**原始页是同步产物，保持只读不动**（AGENTS 2.1 同精神：
寻址店不手改）；净版另落一处，供 docs 层与打标 kb 消费。

清洗规则（确定性、可解释、全 stdlib、拒因单一归口）：
  clean_lines：去 markdown 图（装饰）/ {{模板}} / [[a]] 引用脚注 / [citation needed]
              等注记 / 内联链接保留锚文本 / 去裸 URL 与 HTML 标签 / 反斜杠转义还原 /
              表格拆单元格文本 / 去导航页脚登录与商品操作类样板行 / 页内重复行只留一次
  extract_prose：散文行 = ≥10 词 且 ≥60 字 且 含句末标点 且 非样板/非商品 tile；
              散文行总字数 ≥600 → **只留散文行**（自然剔掉导航菜单、电商列表 tile、
              页脚）；否则原样返回交闸门判
  判定序：short_raw（原文<200 字）→ low_density（实词 token<40）→
          nav_listing（净文<300 字，或 ≥8 词行占比<2% 且句末标点<1.5/千字）→
          listing_page（商品 tile 行占比≥30%）→ html_noise（骨架标记≥10 且实词<200）→
          gibberish（无中文且常用英文词占比<6%，反爬诱饵）→
          duplicate（内容 sha 与已收页重复，按 authority 优先级留一）→ keep

产物（state/collect/docs_clean/）：
  pages_clean.jsonl    保留页（元数据 + 清洗正文 + 度量），一行一页
  rejected.jsonl       剔除页（元数据 + 拒因，无正文，审计用）
  concept_docs.jsonl   按概念聚合的 docs 层净版：{name, kind:"passages", body,
                       n_pages, sources}——湖侧 docs 层 schema 同构，可直接被
                       viewer/eval/annotate 消费（不自动并入 concepts_docs_draft）
  clean_report.json    口径统计（计数/比例/按 authority/按域名/lang/长度分布）

用法：
  python3 curation/clean_docs_pages.py                 # 全量清洗
  python3 curation/clean_docs_pages.py --limit 300     # 抽样试跑
  python3 curation/clean_docs_pages.py --max-concept-pages 5 --max-body-chars 12000
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_ROOT = ROOT / "datasets" / "demiwtg" / "pages"
SYNC_MANIFESTS = ROOT / "sync" / "manifests"
OUT_DIR = ROOT / "state" / "collect" / "docs_clean"

AUTHORITY_RANK = {"wiki": 0, "baike": 1, "serp": 2}
COMMON_EN = set("""the of and is in to a for with on by an as at it from or are was be this that
has have not but they which their can also were been its there when more than into will one all
about out up other after would could some these those his her she he we you your our such over
between during before while under against among without within including known used made first
second new may many most other than""".split())
BOILER_PATTERNS = re.compile(
    r"(登录|注册|签到|下载APP|扫描二维码下载|关注我们|版权声明|免责声明|网站地图|"
    r"版权所有|备案号|加入购物车|收藏|库存|发货|包邮|"
    r"跳转到内容|主菜单|移至侧栏|分类索引|特色内容|新闻动态|随机条目|联络我们|"
    r"关于维基百科|维基社群|互助客栈|最近更改|特殊页面|创建账号|个人工具|"
    r"编辑入门|IRC即时聊天|资助维基百科|此页面始终|内容会尽可能|"
    r"cookie|cookies|privacy policy|terms of use|sign in|sign up|log in|"
    r"subscribe|advertisement|breadcrumb|skip to (main )?content|all rights reserved|"
    r"add to (cart|favorites|bag)|free shipping|in stock|out of stock|sale price|"
    r"original price|\d+\s*%\s*off|read more|show more|see all|view all)",
    re.IGNORECASE)
# 商品列表/价格 tile（电商 SERP 抓取页的主形态；与合法正文的可区分信号）
COMMERCE_PATTERNS = re.compile(
    r"(sale price|original price|\d+\s*%\s*off|free shipping|add to (cart|favorites)|"
    r"[\$￥¥]\s?\d{1,6}(?:[.,]\d{2})?|\(\d{2,5}\)\s*(?:reviews?|ratings?|sold)?|"
    r"ships? from|items?\b.*\bsort by|relevancy|lowest price|highest price)",
    re.IGNORECASE)
MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
BARE_URL = re.compile(r"https?://\S+")
HTML_TAG = re.compile(r"<[^>]{0,300}>")
HTML_MARKERS = ("<div", "<script", "<style", "javascript:", "<!doctype", 'class="', "<table")
WS = re.compile(r"[ \t\r\f\v]+")
# wiki/markdown 残留（引用脚注、模板、方括号注记、反斜杠转义）
WIKI_REF = re.compile(r"\[\[\s*[a-z0-9]{1,5}\s*\]\]\s*(\([^)]{0,80}\))?|\[\[\d+\]\]")
NUM_REF = re.compile(r"\[\s?\d{1,3}(?:\s?[-–,]\s?\d{1,3}){0,3}\s?\]")
UNDERSCORE_EMPH = re.compile(r"(?<!\w)_([^_\n]{1,80})_(?!\w)")
MD_TEMPLATE = re.compile(r"\{\{[^{}]{0,400}\}\}")
BRACKET_NOTE = re.compile(
    r"\[(citation needed|edit|来源请求|注\s*\d*|需要更多来源|clarification needed)\]",
    re.IGNORECASE)
ESCAPES = re.compile(r"\\([()\[\]*_#|^~`<>!-])")


def _wc(line: str) -> int:
    """有效词数：拉丁按空白分词；CJK 无空格，按字符折算（2 字≈1 词）。

    不折算会让中文页每行只算 1 词 → 永远进不了散文模式（系统性歧视中文页）。
    """
    latin = len(re.findall(r"[A-Za-z0-9]+", line))
    cjk = sum(1 for ch in line if "\u4e00" <= ch <= "\u9fff")
    return latin + cjk // 2


PROSE_MIN_WC = 10          # 散文行最少词数
PROSE_MIN_CHARS = 60       # 散文行最少字符
PROSE_FLOOR = 600          # 散文行总字数门槛：达标则只留散文行
COMMERCE_GATE = 0.30       # 商品 tile 行占比 ≥ 此值 → listing_page 剔除
ENDER = re.compile(r"[。.!?！？；;]")


def is_prose_line(ln: str) -> bool:
    return (_wc(ln) >= PROSE_MIN_WC and len(ln) >= PROSE_MIN_CHARS
            and bool(ENDER.search(ln))
            and not BOILER_PATTERNS.search(ln)
            and not COMMERCE_PATTERNS.search(ln))


def extract_prose(lines: list[str]) -> tuple[list[str], dict]:
    """散文行抽取。

    散文行 = ≥10 词 且 ≥60 字 且 含句末标点 且 非样板/非商品 tile。
    散文行总字数达标（≥PROSE_FLOOR）→ **只留散文行**：导航菜单、电商列表
    tile、页脚自然被剔（这类行普遍无句末标点或命中商品/样板模式）；
    不达标 → 原样返回，交由后续闸门（low_density/nav_listing/listing_page）判。
    """
    prose = [l for l in lines if is_prose_line(l)]
    prose_chars = sum(len(l) for l in prose)
    n = max(1, len(lines))
    info = {
        "prose_lines": len(prose),
        "prose_chars": prose_chars,
        "prose_line_frac": round(len(prose) / n, 4),
        "commerce_frac": round(
            sum(1 for l in lines if COMMERCE_PATTERNS.search(l)) / n, 4),
        "prose_mode": prose_chars >= PROSE_FLOOR,
    }
    return (prose if info["prose_mode"] else lines), info


def clean_lines(raw: str) -> list[str]:
    """标记剥离与行级归一（不含散文抽取）。"""
    t = MD_IMG.sub(" ", raw)                       # 装饰图整体去除
    t = MD_TEMPLATE.sub(" ", t)                    # {{模板}}
    t = BRACKET_NOTE.sub(" ", t)                   # [citation needed] 等
    t = WIKI_REF.sub(" ", t)                       # [[a]](…) 引用脚注
    t = NUM_REF.sub(" ", t)                        # [4] / [9-10] 数字引用标记
    t = UNDERSCORE_EMPH.sub(r"\1", t)              # _强调_ 去包裹
    t = MD_LINK.sub(r"\1", t)                      # 内联链接留锚文本
    t = BARE_URL.sub(" ", t)
    t = HTML_TAG.sub(" ", t)
    t = ESCAPES.sub(r"\1", t)                      # \( \) \_ 等转义还原
    lines = []
    seen_lines: set[str] = set()
    for ln in t.splitlines():
        s = ln.strip()
        if not s:
            continue
        s = re.sub(r"^#{1,6}\s*", "", s)           # 标题去 # 保留文本
        s = re.sub(r"^[-*+]\s+", "", s)            # 列表去项目符号
        s = re.sub(r"^\s*\|+\s*", "", s)           # 表格边框
        s = s.replace("|", " ").strip()
        s = re.sub(r"^>+ ?", "", s)                # 引用标记
        s = re.sub(r"^[=`*_~\-—_]{3,}$", "", s).strip()   # 分隔线
        s = re.sub(r"\*\*|__|~~|\*(?=\S)|(?<=\S)\*", "", s)   # 强调标记
        if len(s) < 2:
            continue
        if BOILER_PATTERNS.search(s):
            continue
        key = s.lower()
        if key in seen_lines:                      # 页内重复行（站点外壳）只留一次
            continue
        seen_lines.add(key)
        lines.append(s)
    return lines


def clean_text(raw: str) -> tuple[str, dict]:
    """markdown/HTML 混合正文 → 可读纯文本；返回 (正文, 度量信息)。"""
    lines = clean_lines(raw)
    kept, info = extract_prose(lines)
    return WS.sub(" ", "\n".join(kept)).strip(), info


def metrics(text: str) -> dict:
    n = max(1, len(text))
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    toks = re.findall(r"[A-Za-z]{2,}", text)
    common = sum(1 for w in toks if w.lower() in COMMON_EN)
    dense = len(re.findall(r"\w{3,}", text))
    lines = [l for l in text.splitlines() if l.strip()]
    wc = [_wc(l) for l in lines] or [0]
    enders = sum(text.count(p) for p in "。.!?！？；;")
    return {
        "chars": len(text),
        "dense_tokens": dense,
        "cjk_ratio": round(cjk / n, 4),
        "common_en_ratio": round(common / len(toks), 4) if toks else 0.0,
        "markers": sum(text.lower().count(m.lower()) for m in HTML_MARKERS),
        "lang": "zh" if cjk / n >= 0.10 else ("en" if toks else "?"),
        "prose_frac": round(sum(1 for w in wc if w >= 8) / len(wc), 4),
        "enders_per_1k": round(enders / n * 1000, 2),
        "n_lines": len(lines),
    }


def load_page_meta() -> dict:
    """镜像 docs*.jsonl → {page_sha: 元数据}（跨节点去重写：同 sha 取首见）。"""
    meta: dict = {}
    for f in sorted(glob.glob(f"{SYNC_MANIFESTS}/*/docs*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = r.get("path")
                if not isinstance(p, str) or not p.startswith("pages/"):
                    continue
                sha = r.get("page_sha") or p.rsplit("/", 1)[-1].split(".")[0]
                meta.setdefault(sha, {
                    "url": r.get("url", ""),
                    "authority": r.get("authority", "?"),
                    "title": r.get("title", ""),
                    "concepts": list(r.get("concepts") or []),
                    "n_passages": r.get("n_passages", 0),
                    "n_images": r.get("n_images", 0),
                    "query": r.get("query", ""),
                    "fetched_at": r.get("fetched_at", ""),
                    "mirror": os.path.basename(os.path.dirname(f)),
                })
    return meta


def classify(raw: str, cleaned: str, m: dict, info: dict, seen_content: dict,
             authority: str) -> tuple[str, str | None]:
    """返回 (判定, 拒因)；判定 ∈ {keep, keep_replaces, reject}。"""
    if len(raw.strip()) < 200:
        return "reject", "short_raw"
    if m["dense_tokens"] < 40:
        return "reject", "low_density"
    if m["chars"] < 300 or (m["prose_frac"] < 0.02 and m["enders_per_1k"] < 1.5):
        return "reject", "nav_listing"
    if info["commerce_frac"] >= COMMERCE_GATE:
        return "reject", "listing_page"
    raw_markers = sum(raw.lower().count(mk.lower()) for mk in HTML_MARKERS)
    if raw_markers >= 10 and m["dense_tokens"] < 200:
        return "reject", "html_noise"
    if m["cjk_ratio"] < 0.05 and m["common_en_ratio"] < 0.06:
        return "reject", "gibberish"
    csha = hashlib.sha256(cleaned.encode()).hexdigest()
    prev = seen_content.get(csha)
    if prev is not None:
        pa = AUTHORITY_RANK.get(prev[1], 9)
        ca = AUTHORITY_RANK.get(authority, 9)
        if ca >= pa:
            return "reject", "duplicate"
        seen_content[csha] = (prev[0], authority)      # 更优来源顶替
        return "keep_replaces", prev[0]
    seen_content[csha] = (None, authority)
    return "keep", None


def main() -> None:
    ap = argparse.ArgumentParser(description="docs 页清洗出净版（原始页只读）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 页（抽样试跑）")
    ap.add_argument("--max-concept-pages", type=int, default=5,
                    help="每概念聚合的净页数上限")
    ap.add_argument("--max-body-chars", type=int, default=12000,
                    help="concept_docs body 字数上限")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    meta = load_page_meta()
    files = []
    for d in sorted(os.listdir(PAGES_ROOT)):
        sub = PAGES_ROOT / d
        if sub.is_dir():
            files += sorted(sub.iterdir())
    files = [f for f in files if f.is_file()]
    if args.limit:
        files = files[:args.limit]
    print(f"[input] 原始页 {len(files):,}（元数据在册 {len(meta):,}）")

    args.out.mkdir(parents=True, exist_ok=True)
    seen_content: dict = {}
    kept: list[dict] = []
    rejected_c = Counter()
    by_auth = defaultdict(Counter)
    dom_stat = defaultdict(Counter)
    lang_c = Counter()
    chars_kept = []
    replaced = 0

    with (args.out / "rejected.jsonl").open("w", encoding="utf-8") as fr:
        for f in files:
            sha = f.name.rsplit(".", 1)[0]
            md = meta.get(sha, {})
            auth = md.get("authority", "?")
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                rejected_c["unreadable"] += 1
                continue
            cleaned, info = clean_text(raw)
            m = metrics(cleaned)
            m.update({k: info[k] for k in ("prose_lines", "prose_chars",
                                          "prose_line_frac", "commerce_frac",
                                          "prose_mode")})
            verdict, reason = classify(raw, cleaned, m, info, seen_content, auth)
            dom = re.sub(r"^https?://", "", md.get("url", "?")).split("/")[0]
            if verdict == "reject":
                rejected_c[reason] += 1
                by_auth[auth][f"reject:{reason}"] += 1
                dom_stat[dom][reason] += 1
                fr.write(json.dumps({"page_sha": sha, "reason": reason,
                                     "authority": auth, "url": md.get("url", ""),
                                     "title": md.get("title", ""),
                                     "concepts": md.get("concepts", []),
                                     "chars_raw": len(raw), **m},
                                    ensure_ascii=False) + "\n")
                continue
            if verdict == "keep_replaces":
                replaced += 1
                kept = [k for k in kept if k["page_sha"] != reason]
            rec = {"page_sha": sha, "authority": auth, "url": md.get("url", ""),
                   "title": md.get("title", ""), "domain": dom,
                   "concepts": md.get("concepts", []),
                   "n_passages": md.get("n_passages", 0),
                   "n_images": md.get("n_images", 0),
                   "query": md.get("query", ""), "fetched_at": md.get("fetched_at", ""),
                   "lang": m["lang"], "chars_raw": len(raw),
                   "chars_clean": m["chars"], "dense_tokens": m["dense_tokens"],
                   "cjk_ratio": m["cjk_ratio"],
                   "common_en_ratio": m["common_en_ratio"],
                   "prose_frac": m["prose_frac"],
                   "enders_per_1k": m["enders_per_1k"],
                   "n_lines": m["n_lines"],
                   "prose_mode": m["prose_mode"],
                   "prose_lines": m["prose_lines"],
                   "prose_chars": m["prose_chars"],
                   "prose_line_frac": m["prose_line_frac"],
                   "commerce_frac": m["commerce_frac"], "text": cleaned}
            kept.append(rec)
            by_auth[auth]["keep"] += 1
            dom_stat[dom]["keep"] += 1
            lang_c[m["lang"]] += 1
            chars_kept.append(m["chars"])

    kept.sort(key=lambda r: r["page_sha"])
    with (args.out / "pages_clean.jsonl").open("w", encoding="utf-8") as fk:
        for r in kept:
            fk.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 按概念聚合净版（authority 优先 + 长度降序，截断到配额）
    per_concept: dict = defaultdict(list)
    for r in kept:
        for c in r["concepts"]:
            per_concept[c].append(r)
    concept_out = 0
    body_chars = []
    with (args.out / "concept_docs.jsonl").open("w", encoding="utf-8") as fc:
        for name in sorted(per_concept):
            rows = sorted(per_concept[name],
                          key=lambda r: (AUTHORITY_RANK.get(r["authority"], 9),
                                         -r["chars_clean"]))[:args.max_concept_pages]
            body, srcs, used = [], [], 0
            for i, r in enumerate(rows, 1):
                head = f"【来源{i}｜{r['authority']}｜{r['title'] or r['domain']}｜{r['url']}】"
                room = args.max_body_chars - used - len(head) - 2
                if room <= 50:
                    break
                chunk = r["text"][:room]
                body.append(f"{head}\n{chunk}")
                used += len(head) + len(chunk) + 2
                srcs.append({"page_sha": r["page_sha"], "authority": r["authority"],
                             "chars": len(chunk), "url": r["url"]})
            if not body:
                continue
            txt = "\n\n".join(body)
            body_chars.append(len(txt))
            concept_out += 1
            fc.write(json.dumps({"name": name, "kind": "passages", "body": txt,
                                 "n_pages": len(srcs), "sources": srcs},
                                ensure_ascii=False) + "\n")

    bench = set()
    bpath = ROOT / "state" / "collect" / "concepts_batch_200.json"
    if bpath.exists():
        bench = {r["name"] for r in json.loads(bpath.read_text(encoding="utf-8"))["concepts"]}

    report = {
        "input_pages": len(files),
        "kept_pages": len(kept),
        "rejected": dict(rejected_c.most_common()),
        "dup_replaced": replaced,
        "keep_ratio": round(len(kept) / max(1, len(files)), 4),
        "chars_raw_total": sum(r["chars_raw"] for r in kept),
        "chars_clean_total": sum(r["chars_clean"] for r in kept),
        "chars_clean_median": sorted(chars_kept)[len(chars_kept) // 2] if chars_kept else 0,
        "lang": dict(lang_c),
        "by_authority": {a: dict(c) for a, c in sorted(by_auth.items())},
        "concept_docs": concept_out,
        "concepts_with_pages": len(per_concept),
        "bench283_covered": len(bench & set(per_concept)),
        "bench283_total": len(bench),
        "body_chars_median": sorted(body_chars)[len(body_chars) // 2] if body_chars else 0,
        "top_domains_keep": sorted(((d, c["keep"]) for d, c in dom_stat.items()),
                                   key=lambda x: -x[1])[:10],
        "top_domains_reject": sorted(
            ((d, sum(v for k, v in c.items() if k != "keep")) for d, c in dom_stat.items()),
            key=lambda x: -x[1])[:10],
    }
    (args.out / "clean_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[clean] 保留 {len(kept):,} / {len(files):,}"
          f"（{report['keep_ratio']*100:.0f}%），剔除 {dict(rejected_c.most_common())}")
    print(f"[clean] 净正文字数：共 {report['chars_clean_total']:,}"
          f"（中位 {report['chars_clean_median']}），lang {dict(lang_c)}")
    print(f"[clean] concept_docs：{concept_out:,} 概念"
          f"（bench283 覆盖 {report['bench283_covered']}/{report['bench283_total']}）")
    print(f"[clean] 产物 → {args.out}/")


if __name__ == "__main__":
    main()
