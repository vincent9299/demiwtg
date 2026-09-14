#!/usr/bin/env python
"""sitelink 过滤器(档2):批量查 Wikidata,保留"至少有一个语言维基页面"的实体。

用法: filter_sitelinks.py <candidates.tsv.gz> <out-qids.txt> <shard_i> <shard_n>
读 (qid, filename) 候选表,取 qid 按 idx%N==i 分片去重,50 实体/批
wbgetentities props=sitelinks,2 rps 限速;有 sitelink 的 qid 写出。
"""
import gzip
import json
import os
import sys
import time
from urllib.parse import quote, urlencode

import httpx

API = "https://www.wikidata.org/w/api.php"
UA = ("demiwtg-kb-phase3/1.0 (Wikimedia bulk; contact: "
      f"{os.environ.get('DEMIWTG_CONTACT', 'ops@demiwtg.example')})")


def main() -> None:
    src, dst, si, sn = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    qids = []
    seen = set()
    with gzip.open(src, "rt", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx % sn != si:
                continue
            q = line.split("\t", 1)[0]
            if q and q not in seen:
                seen.add(q)
                qids.append(q)
    sys.stderr.write(f"[sitelink] shard {si}/{sn}: {len(qids)} qids\n")

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=60, write=30, pool=15),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))
    kept = 0
    t0 = time.time()
    out = open(dst, "w", encoding="utf-8")

    async def run() -> None:
        nonlocal kept
        import asyncio
        rate_next = [0.0]

        async def take():
            while True:
                now = asyncio.get_running_loop().time()
                w = rate_next[0] - now
                if w <= 0:
                    rate_next[0] = now + 0.55
                    return
                await asyncio.sleep(w)

        for i in range(0, len(qids), 50):
            batch = qids[i:i + 50]
            params = {"action": "wbgetentities", "format": "json",
                      "ids": "|".join(batch), "props": "sitelinks"}
            url = API + "?" + urlencode(params, safe="|", quote_via=quote)
            body = None
            for attempt in range(4):
                await take()
                try:
                    r = await client.get(url, headers={"User-Agent": UA})
                except httpx.HTTPError:
                    await asyncio.sleep(2)
                    continue
                if r.status_code == 200:
                    body = r.json()
                    break
                if r.status_code == 429:
                    await asyncio.sleep((30, 120, 300)[min(attempt, 2)])
                    continue
                await asyncio.sleep(2)
            if not body:
                continue
            for qid, ent in (body.get("entities") or {}).items():
                if "missing" in ent:
                    continue
                if ent.get("sitelinks"):
                    out.write(qid + "\n")
                    kept += 1
            if (i // 50) % 100 == 0:
                el = time.time() - t0
                sys.stderr.write(f"[sitelink] {i}/{len(qids)} kept={kept} "
                                 f"{el:.0f}s\n")
        await client.aclose()

    import asyncio
    asyncio.run(run())
    out.close()
    sys.stderr.write(f"[sitelink] DONE kept={kept} {time.time()-t0:.0f}s\n")


if __name__ == "__main__":
    main()
