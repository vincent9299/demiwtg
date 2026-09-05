#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search_kb_sources.py — search agent 直供源扩充（第二轮，2026-08-29）。

源清单（通道/用法均 2026-08-29 探针实证，见 /tank/tmp/kilo/search_agent/probe_sources*.py）：
    inaturalist  代理  v1/taxa?q=学名/英文俗名（q 对中文名是垃圾匹配，只走拉丁/
                     英文别名；命中校验用 name 或 preferred_common_name 精确）；
                     wikipedia_summary 已废弃为空，真正载荷是 wikipedia_url 解析出
                     正确英文 wiki 标题（学名≠条目名，此处桥接 en-wiki intro）
    anilist      代理  GraphQL；复合别名查询 404，必须 Character/Media 拆两次；
                     人名匹配词序无关（Gojo Satoru=Satoru Gojo）
    jikan(mal)   直连  /v4/characters.about + /v4/anime.synopsis（上游偶发 504，
                     infra 按 transient 重试后认缺；不阻塞其他源）
    steam        代理  storesearch(term,cc)→appdetails(l=schinese)：short_description
                     是中文 gameplay 向简介；storesearch 本身无简介必须二跳
    openlibrary  代理  search.json（书籍：作者/首版年/首句/主题）
    dbpedia      直连  lookup /api/search（须 format=json；label 去 <B> 高亮后校验）
    wikidata     代理  wbsearchentities(zh, uselang=zh) → zh 描述 + en 标签桥接
                     en-wiki intro（无英文别名实体的统一桥，REPORT §2 已验证）

探针否决、未接入的候选（教训沉淀）：
    booru tag wiki  safebooru wiki 的 search= 参数被无视、view&title= 返回空页；
                    yande.re 词条覆盖稀疏且页面结构不稳定；实体英文别名≠booru
                    tag 约定（surname_givenname/char_series 后缀）缺映射层——
                    整源放弃，ACG 视觉证据由萌百/anilist/SERP 页面通道承担
    fandom          全局搜索仍被 Cloudflare 拦（collect 同因挂起），不建直连；
                    其词条经 SERP→正文通道覆盖（fandom.com 在 FOREIGN_PAGE_DOMAINS
                    + PAGE_PREF 加权）

契约：每连接器返回 [evidence dict]（src/title/url/text），miss/校验不过返回 []；
错实体校验在连接器内部完成（防串味入包）。请求全部走 infra 闸门（sk: 前缀，
在 search_kb.SOURCE_LIMITS 登记）。不改 instances.json，不落任何盘。
"""
from __future__ import annotations

import logging
import re
import urllib.parse

from collect_v2.infra import request as infra_request, DeterministicError, \
    TransientExhaustedError

log = logging.getLogger("search_kb.sources")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64: x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _strip(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


async def _fetch(source: str, url: str):
    try:
        return await infra_request(source, "GET", url,
                                   headers={"User-Agent": UA,
                                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    except (DeterministicError, TransientExhaustedError) as e:
        log.debug("src fetch fail %s %s: %s", source, url[:70], str(e)[:100])
        return None
    except Exception as e:                                          # noqa: BLE001
        log.debug("src fetch error %s %s: %s", source, url[:70], str(e)[:100])
        return None


def _is_ascii_dominant(s: str) -> bool:
    toks = re.findall(r"[A-Za-z][A-Za-z0-9' -]*", s)
    return bool(toks) and len("".join(toks)) >= max(3, int(len(s) * 0.7))


def _en_query(name: str, q: str) -> str:
    """英文源的查询词防御：规划器/别名给到中文词时宁可弃查（中文词打英文源
    全是垃圾匹配），回落到 ASCII 主名；全无则空串=跳过。"""
    q = (q or "").strip()
    if q and _is_ascii_dominant(q):
        return q
    name = (name or "").strip()
    return name if _is_ascii_dominant(name) else ""


def _ev(src: str, title: str, url: str, text: str) -> list:
    text = _strip(text)
    return [{"src": src, "title": title, "url": url, "text": text}] \
        if len(text) >= 60 else []


# ---------------------------------------------------------------------------
# inaturalist：学名 → 正确 en-wiki 标题桥（+ 分类阶元证据）
# ---------------------------------------------------------------------------


async def inaturalist_source(name: str, alias: str, wiki_intro) -> list:
    """v1/taxa?q=学名或英文俗名（q 对中文名是垃圾匹配，只走拉丁/英文别名）。
    命中校验：学名精确 或 preferred_common_name（locale=en）精确——q= 排序不可
    可靠（giant panda 首条是亚科），无校验会串味；taxon_names 在 search 响应里
    为空（all_names=true 也一样），俗名校验只有 preferred_common_name 一条路。
    产分类阶元证据 + wikipedia_url 桥接的 en-wiki intro（学名≠条目名的修复）。"""
    q = _en_query(name, alias)
    if not q:
        return []
    ql = q.lower()
    url = ("https://api.inaturalist.org/v1/taxa?"
           + urllib.parse.urlencode({"q": q, "per_page": 5, "locale": "en"}))
    resp = await _fetch("sk:ina", url)
    if resp is None:
        return []
    try:
        results = resp.json().get("results") or []
    except Exception:                                               # noqa: BLE001
        return []
    taxon = None
    for t in results:
        if str(t.get("name") or "").lower() == ql:
            taxon = t
            break
        if str(t.get("preferred_common_name") or "").strip().lower() == ql:
            taxon = t
            break
    if taxon is None:
        return []
    anc = [str(a.get("name") or "") for a in (taxon.get("ancestors") or [])]
    ranks = " > ".join([*anc, taxon["name"]])
    cn = str(taxon.get("preferred_common_name") or "")
    text = (f"iNaturalist 分类信息：学名 {taxon['name']}（{taxon.get('rank')}），"
            f"分类谱系 {ranks}。"
            + (f"俗名：{cn}。" if cn else ""))
    out = _ev("inaturalist", f"{taxon['name']}（iNaturalist）",
              f"https://www.inaturalist.org/taxa/{taxon.get('id')}", text)
    wurl = str(taxon.get("wikipedia_url") or "")
    if wurl:
        title = urllib.parse.unquote(wurl.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")
        intro = await wiki_intro("en", title)
        if intro:
            out += _ev("wiki_en", title, wurl, intro)
    return out


# ---------------------------------------------------------------------------
# anilist：Character / Media 两次 GraphQL（复合别名查询 404，勿合并）
# ---------------------------------------------------------------------------

def _romaji_norm(t: str) -> str:
    """罗马字变体归一：ou→o（Gojou=Gojo）、尾 u 剥离（Satoru=Sator，双侧同规）。"""
    t = re.sub(r"ou$", "o", t)
    return t[:-1] if t.endswith("u") else t


def _name_match(q: str, target: str) -> bool:
    """ACG 人名匹配：全等 / 词序无关全词集（Gojo Satoru=Satoru Gojou）；
    单 token 查询只认全等（Bourbon→Mihono Bourbon 串味教训）。"""
    ql, tl = q.strip().lower(), (target or "").strip().lower()
    if not ql or not tl:
        return False
    if ql == tl:
        return True
    qtoks = {_romaji_norm(t) for t in re.split(r"[^a-z0-9']+", ql) if len(t) >= 3}
    ttoks = {_romaji_norm(t) for t in re.split(r"[^a-z0-9']+", tl) if len(t) >= 3}
    if len(qtoks) >= 2 and ttoks and qtoks <= ttoks:
        return True
    return len(ql) >= 8 and bool(re.search(r"\b" + re.escape(ql) + r"\b", tl))


async def _anilist_one(query: str, var: str) -> dict:
    resp = await infra_request(
        "sk:anilist", "POST", "https://graphql.anilist.co",
        json={"query": query, "variables": {"q": var}},
        headers={"User-Agent": UA, "Content-Type": "application/json"})
    data = (resp.json().get("data") or {})
    return data


async def anilist_source(name: str, alias: str) -> list:
    q = _en_query(name, alias)
    if not q:
        return []
    out = []
    try:
        ch = (await _anilist_one(
            "query($q:String){Character(search:$q){name{full} description "
            "media(perPage:1){nodes{title{romaji native english}}}}}", q)
        ).get("Character") or {}
        full = str((ch.get("name") or {}).get("full") or "")
        desc = _strip(ch.get("description") or "")
        if full and desc and _name_match(q, full):
            works = [str((t.get("title") or {}).get("english")
                         or (t.get("title") or {}).get("romaji") or "")
                     for t in (ch.get("media") or {}).get("nodes") or []]
            out += _ev("anilist", f"{full}（AniList 角色）",
                       f"https://anilist.co/character/{ch.get('id', '')}",
                       f"{full}（AniList 角色，出自{'、'.join(w for w in works if w)}）：{desc[:1400]}")
        md = (await _anilist_one(
            "query($q:String){Media(search:$q,type:ANIME)"
            "{id title{romaji native english} description}}", q)
        ).get("Media") or {}
        titles = md.get("title") or {}
        mtitle = str(titles.get("english") or titles.get("romaji") or "")
        mdesc = _strip(md.get("description") or "")
        md_hit = _name_match(q, mtitle) or _name_match(
            q, str(titles.get("romaji") or ""))
        if mtitle and mdesc and md_hit:
            out += _ev("anilist", f"{mtitle}（AniList 作品）",
                       f"https://anilist.co/anime/{md.get('id', '')}",
                       f"{mtitle}（AniList 作品，日文名 {titles.get('native')}）：{mdesc[:1400]}")
    except Exception as e:                                          # noqa: BLE001
        log.debug("anilist %s 失败: %s", q, str(e)[:100])
    return out


# ---------------------------------------------------------------------------
# jikan（MAL 非官方 API，免 key）：角色 about + 动画 synopsis
# ---------------------------------------------------------------------------

async def jikan_source(name: str, alias: str) -> list:
    q = _en_query(name, alias)
    if not q:
        return []
    out = []
    for kind, path, field, label in (
            ("char", "/v4/characters", "about", "角色"),
            ("anime", "/v4/anime", "synopsis", "作品")):
        url = (f"https://api.jikan.moe{path}?"
               + urllib.parse.urlencode({"q": q, "limit": 3, "sf": "true"}))
        resp = await _fetch("sk:jikan", url)
        if resp is None:
            continue                # 上游 504 常见：认缺不阻塞
        try:
            data = resp.json().get("data") or []
        except Exception:                                           # noqa: BLE001
            continue
        for d in data:
            title = str(d.get("name") or d.get("title") or "")
            if not title or q.lower() not in title.lower():
                continue
            body = _strip(d.get(field) or "")
            if body:
                url2 = d.get("url") or f"https://myanimelist.net{path}"
                out += _ev("jikan", f"{title}（MyAnimeList {label}）", url2,
                           f"{title}（MyAnimeList {label}）：{body[:1400]}")
            break
    return out


# ---------------------------------------------------------------------------
# Steam：storesearch(term, cc) → appdetails(l=schinese)
# ---------------------------------------------------------------------------

async def steam_source(name: str, alias: str) -> list:
    """storesearch 只认英文名索引（刀塔2/蔚蓝 用 cc=CN 中文 term 全空或串歪，
    实测 2026-08-29）；第一步 cc=US 英文检索，第二步 appdetails 拿中文简介。"""
    query = (alias or (_is_ascii_dominant(name) and name) or "").strip()
    if not query:
        return []
    s1 = ("https://store.steampowered.com/api/storesearch/?"
          + urllib.parse.urlencode({"term": query, "cc": "US", "l": "english"}))
    resp = await _fetch("sk:steam", s1)
    if resp is None:
        return []
    try:
        items = resp.json().get("items") or []
    except Exception:                                               # noqa: BLE001
        return []
    item = None
    for it in items:
        iname = str(it.get("name") or "")
        if iname == query:
            item = it
            break
        if len(query) >= 6 and query.lower() in iname.lower():
            item = it
            break               # 短词子串必串味（DYG→Dygyn），<6 字符只认全名
    if item is None:
        return []
    appid = item.get("id")
    s2 = ("https://store.steampowered.com/api/appdetails/?"
          + urllib.parse.urlencode({"appids": appid, "l": "schinese", "cc": "CN"}))
    resp2 = await _fetch("sk:steam", s2)
    d = {}
    if resp2 is not None:
        try:
            d = ((resp2.json().get(str(appid)) or {}).get("data")) or {}
        except Exception:                                           # noqa: BLE001
            d = {}
    sd = _strip(d.get("short_description") or "")
    genres = [str(g.get("description") or "") for g in d.get("genres") or []]
    rel = str((d.get("release_date") or {}).get("date") or "")
    devs = ", ".join(d.get("developers") or [])
    title = d.get("name") or item.get("name") or query
    bits = [f"Steam 收录游戏《{title}》"]
    if genres:
        bits.append(f"类型：{'、'.join(genres)}")
    if devs:
        bits.append(f"开发商：{devs}")
    if rel:
        bits.append(f"发行：{rel}")
    if sd:
        bits.append(f"官方简介：{sd}")
    return _ev("steam", f"{title}（Steam）",
               f"https://store.steampowered.com/app/{appid}", "；".join(bits))


# ---------------------------------------------------------------------------
# OpenLibrary：书籍元数据（英文侧）
# ---------------------------------------------------------------------------

async def openlibrary_source(name: str, alias: str) -> list:
    q = _en_query(name, alias)
    if not q:
        return []
    url = ("https://openlibrary.org/search.json?"
           + urllib.parse.urlencode({
               "q": q, "limit": 3,
               "fields": "title,author_name,first_publish_year,first_sentence,subject"}))
    resp = await _fetch("sk:ol", url)
    if resp is None:
        return []
    try:
        docs = resp.json().get("docs") or []
    except Exception:                                               # noqa: BLE001
        return []
    for doc in docs:
        title = str(doc.get("title") or "")
        subjects = " ".join(str(s) for s in (doc.get("subject") or [])[:20])
        # 匹配只认查询短语连续出现在标题里（主题域/全词落标题都会串到邻近书：
        # 门神→The universe next door、Nobel Prize in Economics→Jewish Nobel
        # Prize in Economics，两次教训）
        if q.lower() not in title.lower():
            continue
        authors = ", ".join((doc.get("author_name") or [])[:3])
        year = doc.get("first_publish_year") or ""
        fs = doc.get("first_sentence")
        if isinstance(fs, dict):
            fs = fs.get("english") or next(iter(fs.values()), None)
        if isinstance(fs, list):
            fs = fs[0].get("value") if fs and isinstance(fs[0], dict) else (fs[0] if fs else "")
        bits = [f"OpenLibrary 条目《{title}》"]
        if authors:
            bits.append(f"作者：{authors}")
        if year:
            bits.append(f"首版 {year}")
        if fs:
            bits.append(f"开篇：{_strip(str(fs))[:400]}")
        if len(subjects) > 60:
            bits.append(f"主题标签：{subjects[:400]}")
        ev = _ev("openlibrary", f"{title}（OpenLibrary）",
                 f"https://openlibrary.org{doc.get('key', '')}", "；".join(bits))
        if ev:
            return ev
    return []


# ---------------------------------------------------------------------------
# dbpedia lookup：英文实体补充
# ---------------------------------------------------------------------------

def _db_label(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    return _strip(str(v or "").replace("<B>", "").replace("</B>", ""))


def _db_comment(v) -> str:
    if isinstance(v, list):          # 分段数组（dbpedia 把长摘要切片返回）
        parts = []
        for item in v:
            if isinstance(item, dict):
                parts.append(str(item.get("value") or ""))
            else:
                parts.append(str(item or ""))
        s = "".join(parts)
    else:
        s = str(v or "")
    return _strip(s.replace("<B>", "").replace("</B>", ""))


async def dbpedia_source(name: str, alias: str) -> list:
    q = _en_query(name, alias)
    if not q:
        return []
    url = ("https://lookup.dbpedia.org/api/search?"
           + urllib.parse.urlencode({"query": q, "maxResults": 3,
                                     "format": "json"}))
    resp = await _fetch("sk:dbpedia", url)
    if resp is None:
        return []
    try:
        docs = resp.json().get("docs") or []
    except Exception:                                               # noqa: BLE001
        return []
    for doc in docs:
        label = _db_label(doc.get("label"))
        if not label or q.lower() not in label.lower():
            continue
        desc = _db_comment(doc.get("comment"))
        if len(desc) < 60:
            continue
        rsrc = str(doc.get("resource") or "")
        return _ev("dbpedia", f"{label}（DBpedia）", rsrc,
                   f"{label}（DBpedia）：{desc[:1200]}")
    return []


# ---------------------------------------------------------------------------
# wikidata：无英文别名实体的统一桥（zh 描述 + en 标签 → en-wiki intro）
# ---------------------------------------------------------------------------

async def wikidata_source(name: str, wiki_intro) -> list:
    s1 = ("https://www.wikidata.org/w/api.php?"
          + urllib.parse.urlencode({"action": "wbsearchentities", "search": name,
                                    "language": "zh", "uselang": "zh",
                                    "limit": 5, "format": "json"}))
    resp = await _fetch("sk:wiki", s1)
    if resp is None:
        return []
    try:
        hits = resp.json().get("search") or []
    except Exception:                                               # noqa: BLE001
        return []
    hit = next((h for h in hits
                if str(h.get("label") or "") == name
                and str(h.get("description") or "")), None)
    if hit is None:
        return []
    qid = hit["id"]
    out = _ev("wikidata", f"{name}（Wikidata）",
              f"https://www.wikidata.org/wiki/{qid}",
              f"{name}（Wikidata {qid}）：{hit.get('description')}")
    s2 = ("https://www.wikidata.org/w/api.php?"
          + urllib.parse.urlencode({"action": "wbgetentities", "ids": qid,
                                    "props": "labels", "languages": "en",
                                    "format": "json"}))
    resp2 = await _fetch("sk:wiki", s2)
    if resp2 is not None:
        try:
            ent = ((resp2.json().get("entities") or {}).get(qid) or {})
            en = str(((ent.get("labels") or {}).get("en") or {}).get("value") or "")
            if en and len(out) < 2:
                intro = await wiki_intro("en", en)
                if intro:
                    out += _ev("wiki_en", en,
                               f"https://en.wikipedia.org/wiki/{urllib.parse.quote(en.replace(' ', '_'))}",
                               intro)
        except Exception:                                           # noqa: BLE001
            pass
    return out
