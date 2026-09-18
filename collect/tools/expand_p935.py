#!/usr/bin/env python
"""P935 图库页展开:批量 API 拉文件列表(+图注),输出 (qid, filename[, caption])。

用法: expand_p935.py <p935-titles.tsv.gz> <out.tsv.gz>
~7.6 万页 / 50 页一批 ≈ 1530 请求,2 rps 限速 ≈ 13 分钟。
"""
import gzip
import json
import os
import re
import sys
import time
from urllib.parse import quote, urlencode

import httpx

API = "https://commons.wikimedia.org/w/api.php"
UA = ("demiwtg-kb-phase3/1.0 (Wikimedia bulk; contact: "
      f"{os.environ.get('DEMIWTG_CONTACT', 'ops@demiwtg.example')})")
IMG_EXT = {".jpg", ".jpeg", ".png", ".svg", ".tif", ".tiff", ".gif", ".webp"}
CAP = re.compile(
    r"(?:^|\n)\s*(?:File|Image|文件|檔案)\s*:\s*([^|\n\]]+?)"
    r"\s*(?:\|\s*(?:\d+\s*=\s*)?([^\n]*))?(?=\n|$)", re.IGNORECASE)


def norm(name: str) -> str:
    return name.strip().replace("_", " ")


def parse_captions(wt: str) -> dict:
    out = {}
    for name, cap in CAP.findall(wt or ""):
        k = norm(name)
        c = re.sub(r"\[\[([^|\]]*\|)?([^\]]+)\]\]", r"\2", (cap or "").strip())
        c = re.sub(r"'{2,}|<[^>]+>|\{\{[^}]*\}\}", "", c).strip()
        if k and k not in out:
            out[k] = c
    return out


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    pairs = []
    seen_t = set()
    with gzip.open(src, "rt", encoding="utf-8") as f:
        for line in f:
            qid, _, title = line.rstrip("\n").partition("\t")
            t = title.strip()
            if t and t not in seen_t:
                seen_t.add(t)
            pairs.append((qid, t))
    titles = sorted(seen_t)
    sys.stderr.write(f"[p935] qids={len(pairs)} uniq_titles={len(titles)}\n")

    t2q: dict = {}
    for qid, t in pairs:
        t2q.setdefault(t, []).append(qid)

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=60, write=30, pool=15),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))
    out = gzip.open(dst, "wt", encoding="utf-8")
    n_files = n_pages = 0
    t0 = time.time()

    def base_params(batch):
        return {"action": "query", "format": "json", "formatversion": "2",
                "titles": "|".join(batch),
                "redirects": "1", "prop": "images|revisions",
                "imlimit": "max", "rvprop": "content", "rvslots": "main"}

    async def run() -> None:
        nonlocal n_files, n_pages
        rate_next = [0.0]

        async def take():
            import asyncio
            while True:
                now = asyncio.get_running_loop().time()
                w = rate_next[0] - now
                if w <= 0:
                    rate_next[0] = now + 0.55
                    return
                await asyncio.sleep(w)

        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            params = base_params(batch)
            acc: dict = {}
            redirects: dict = {}
            for _round in range(6):
                await take()
                url = API + "?" + urlencode(params, safe="|,_", quote_via=quote)
                try:
                    r = await client.get(url, headers={"User-Agent": UA})
                except httpx.HTTPError:
                    await take()
                    continue
                if r.status_code == 429:
                    await __import__("asyncio").sleep(30)
                    continue
                if r.status_code != 200:
                    continue
                body = r.json()
                q = body.get("query", {})
                for rd in q.get("redirects", []):
                    redirects[norm(rd.get("to", ""))] = norm(rd.get("from", ""))
                for pg in q.get("pages", []):
                    if pg.get("missing") or pg.get("ns", 0) != 0:
                        continue
                    t = norm(pg.get("title", ""))
                    d = acc.setdefault(t, {"files": [], "wt": ""})
                    d["files"] += [x.get("title", "") for x in pg.get("images", [])]
                    try:
                        d["wt"] += pg["revisions"][0]["slots"]["main"]["content"]
                    except (KeyError, IndexError, TypeError):
                        pass
                cont = body.get("continue") or {}
                if not cont:
                    break
                params = {**params, **cont}
            for t, d in acc.items():
                origin = redirects.get(t, t)
                caps = parse_captions(d["wt"])
                n_pages += 1
                for f in dict.fromkeys(d["files"]):
                    name = norm(f.removeprefix("File:"))
                    if os.path.splitext(name)[1].lower() not in IMG_EXT:
                        continue
                    for qid in t2q.get(origin, []):
                        out.write(f"{qid}\t{name}\t{caps.get(name, '')}\n")
                        n_files += 1
            if (i // 50) % 50 == 0:
                el = time.time() - t0
                sys.stderr.write(f"[p935] {i}/{len(titles)} titles "
                                 f"pages={n_pages} files={n_files} "
                                 f"{el:.0f}s\n")

    import asyncio

    async def run_all():
        await run()
        await client.aclose()

    asyncio.run(run_all())
    out.close()
    sys.stderr.write(f"[p935] DONE pages={n_pages} files={n_files} "
                     f"{time.time()-t0:.0f}s\n")


if __name__ == "__main__":
    main()
