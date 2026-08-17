"""Fandom（fandom.com）来源适配器：西方 IP wiki 农场（未授权国际源）。

架构现状（2026-08-16 实测）：主站全局搜索 API（GlobalSearchController）与
wiki 目录页均已死（404/403），但各分站的 MediaWiki API 完全可用。故采用
「精选大 wiki 池 + 分站检索」方案：

- 对每个 job，从 _WIKIS 池按 hash(实例名) 轮转取 _SEARCH_WIKIS_PER_JOB 个
  wiki 逐个检索（轮转起点避免所有 job 都砸同一批头部 wiki）。
- 单请求取图：generator=search + gsrnamespace=6（File 命名空间）+
  prop=imageinfo，一步拿到原图 URL/宽高（图床 static.wikia.nocookie.net，
  本环境可达）。
- 累计候选 >= target_count * 2 即早停，控制单 job 请求量。
- 过滤：只留 jpg/png/webp/gif（wiki 里 svg 图标/noicon 占位图极多）；
  标题含 Icon/Logo/Flag 之类装饰词的跳过。
- UA 用项目标识式（MediaWiki API 礼仪），限速交给 per-host 限速器。
- 社区 wiki 内容非 CC → source_authorized=False。
"""

from __future__ import annotations

import re
import zlib
from typing import Dict, List

from .base import SourceAdapter, register
from ..models import (
    Candidate,
    SOURCE_KIND_UNAUTHORIZED,
    STATUS_CANDIDATE,
)
from ..config import Job
from ..util import fetch_json


# 精选 IP wiki 池（子域名，覆盖西方影视/游戏/动漫与日系大 IP）。
# 新增直接往这里加；死站由健康账本反馈后再摘。
_WIKIS = [
    "marvel", "dc", "disney", "starwars", "harrypotter", "lotr",
    "pokemon", "digimon", "naruto", "onepiece", "dragonball", "bleach",
    "gundam", "evangelion", "sailormoon", "swordartonline", "attackontitan",
    "myheroacademia", "jujutsu-kaisen", "kimetsu-no-yaiba",
    "finalfantasy", "zelda", "mario", "kirby", "metroid", "sonic",
    "halo", "masseffect", "fallout", "elderscrolls", "witcher",
    "minecraft", "terraria", "leagueoflegends", "overwatch", "apexlegends",
    "genshin-impact", "honkai-star-rail", "arknights", "azur-lane",
    "star-trek", "stargate", "doctorwho", "whedonverse", "memory-alpha",
    "gameofthrones", "hobbit", "tolkiengateway", "rpg", "dnd",
    "avatar", "transformers", "tmnt", "powerpuff", "cartoonnetwork",
    "pixar", "dreamworks", "disneyprincess", "frozen", "hitchhikers",
]

_SEARCH_WIKIS_PER_JOB = 4          # 单 job 轮询的 wiki 数（控请求量）
_EARLY_STOP_FACTOR = 2            # 候选 >= target_count * 2 即停
_OK_EXT = ("jpg", "jpeg", "png", "webp", "gif")
_DECOR_RE = re.compile(r"(icon|logo|flag|symbol|emblem|badge|button|map)\b", re.I)


class FandomAdapter(SourceAdapter):
    name = "fandom"
    source_kind = SOURCE_KIND_UNAUTHORIZED
    allowed_suffixes = ("fandom.com", "static.wikia.nocookie.net")
    lang = "en"
    is_authorized = False

    def search(self, job: Job) -> List[Dict]:
        q = (job.query or "").strip()
        if not q:
            return []
        cfg = job.effective
        # 轮转起点：同一实例稳定、不同实例分散
        start = zlib.crc32((job.instance or "").encode("utf-8")) % len(_WIKIS)
        out: List[Dict] = []
        limit = max(5, min(20, cfg.target_count * 3))
        for i in range(_SEARCH_WIKIS_PER_JOB):
            wiki = _WIKIS[(start + i) % len(_WIKIS)]
            api = f"https://{wiki}.fandom.com/api.php"
            try:
                data = fetch_json(
                    api,
                    allowed_suffixes=("fandom.com",),
                    params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": q,
                        "gsrnamespace": "6",   # File: 命名空间
                        "gsrlimit": str(limit),
                        "prop": "imageinfo",
                        "iiprop": "url|size",   # 无 ext 选项，扩展名从 URL 取
                        "format": "json",
                    },
                    timeout=cfg.timeout_sec,
                    max_retries=1,            # 多 wiki 轮询自带兜底，不叠加重试
                )
            except Exception:
                continue  # 单个 wiki 失败不影响其余
            pages = (data or {}).get("query", {}).get("pages") or {}
            for pg in pages.values():
                if not isinstance(pg, dict):
                    continue
                ii = (pg.get("imageinfo") or [{}])[0]
                u = ii.get("url") or ""
                if not u.startswith("https://static.wikia.nocookie.net/"):
                    continue
                # URL 尾部可能带 /revision/latest 路径段，先剥再取扩展名
                u_base = u.split("/revision/")[0].split("?")[0]
                ext = u_base.rsplit(".", 1)[-1].lower()
                if ext not in _OK_EXT:
                    continue
                title = pg.get("title") or ""
                if _DECOR_RE.search(title):
                    continue
                out.append({
                    "url": u_base,
                    "w": ii.get("width"),
                    "h": ii.get("height"),
                    "title": title,
                    "wiki": wiki,
                    "pageid": pg.get("pageid"),
                })
            if len(out) >= cfg.target_count * _EARLY_STOP_FACTOR:
                break
        return out[:40]

    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        wiki = raw.get("wiki") or ""
        pageid = raw.get("pageid")
        landing = (f"https://{wiki}.fandom.com/wiki/{raw.get('title', '').replace(' ', '_')}"
                   if wiki else "")
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=f"fandom-{wiki}-{pageid}",
            instance=job.instance,
            query=job.query,
            landing_url=landing,
            content_url=raw["url"],
            declared_mime=None,  # 下载后由 Pillow 复验
            declared_width=raw.get("w"),
            declared_height=raw.get("h"),
            declared_size=None,
            author=None,
            credit=f"{wiki}.fandom.com: {raw.get('title')}",
            license_raw="未知(未授权来源,非CC)",
            source_authorized=False,
            source_score=None,
            evidence=raw,
            status=STATUS_CANDIDATE,
        )


register(FandomAdapter())
