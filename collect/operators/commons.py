"""kb 线 Phase 3 算子(2026-09-10):概念 P18 主图 → Commons 原图回池。

链路定位:增肥后概念带 p18(主图文件名),本算子逐图调 Commons API
(一次拿齐 原图URL/尺寸/许可证 extmetadata)并下载字节,只取原图
(2026-09-17 口径变更:废除 >10MB 降级 1200px 缩略图的守门,历史
tier=thumb1200 行由 backfill_orig.py 重收)。字节落 COS kb/blobs
(内容寻址,与旧图池同构、同 sha 自动重合),账本 qid_images.jsonl
复用 AppendManifestStore(幂等续跑零重复)。

行契约:
- 任务行(源展开):{qid, commons_file}
- 产物行(CommonsFetchStage):+ {sha256, ext, data, license, author,
  license_url, content_url, tier=orig(历史行含 thumb1200), page_bytes,
  width, height}
- 落盘(CommonsBlobSink):blob kb/blobs/aa/sha.ext;账本行 {qid,
  commons_file, sha256, ext, tier, license, author, license_url,
  content_url, page_bytes, width, height, fetched_at}

礼貌口径(Wikimedia 批量下载规范):API 2rps/并发4,下载 4rps/并发8;
UA 自报项目与联系方式(env DEMIWTG_CONTACT 可覆盖)。429 属瞬态,
平台 net 层有界重试后认缺,重跑补收(账本幂等)。
"""

from __future__ import annotations

import hashlib
import json
import re
import time

import httpx

from demiflow.collect import net
from demiflow.collect.store import AppendManifestStore
from demiflow.data.plan import StreamStage

API = "https://commons.wikimedia.org/w/api.php"
HARD_CAP_BYTES = 64 << 20         # 字节封顶(API 尺寸谎报兜底)
# 200 状态错误页兜底(2026-09-17 事故:429 的 HTML 体曾被批式采集器当图落库)
HTML_ERR_HEADS = (b"<!doctype html", b"<html")

net.register_limits({
    "commons_api": net.SourceLimits(rate=2.0, concurrency=4),
    "dl:commons": net.SourceLimits(rate=4.0, concurrency=8),
})

_UA = (f"demiwtg-kb-phase3/1.0 (Wikimedia bulk; contact: "
       f"{__import__('os').environ.get('DEMIWTG_CONTACT', 'ops@demiwtg.example')})")
# 注:contact 必须是格式完整邮箱——无点后缀的假邮箱会被 WAF 403(实测)
_TAG_RE = re.compile(r"<[^>]+>")


def _norm_file(v: str) -> str:
    """P18 值归一化:truthy 转储里是 `<http://…/Special:FilePath/A%20B.jpg>`
    完整 URI 形态,API 查询需纯文件名(A B.jpg);兼容已是纯名/File: 前缀。
    原始值直拼 `File:<http://…>` 会被边缘以非法标题拒掉(实测连坐 429)。"""
    s = (v or "").strip()
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1]
    if "Special:FilePath/" in s:
        s = s.rsplit("Special:FilePath/", 1)[1]
    if s.startswith("File:"):
        s = s[len("File:"):]
    from urllib.parse import unquote
    return unquote(s).strip()


def iter_p18_tasks(concepts_path: str, done: set | None = None,
                   batch_size: int = 50):
    """增肥概念 → 任务行流(展开 p18 列表;done=已收 (qid,file) 跳过)。

    from_iter 的 factory 体:文件级惰性流,零物化。
    batch_size>0 时按批产出(list 行),供 CommonsFetchStage 走
    MediaWiki 多标题查询(一次 ≤50 题,API 配额利用率 ×50);
    分片 islice 按批步进,25 分片并集仍覆盖全部任务(块状交错)。
    """
    def factory():
        op = "rb" if concepts_path.endswith(".gz") else "r"
        import gzip
        opener = gzip.open if op == "rb" else open
        enc = None if op == "rb" else "utf-8"
        batch = []
        with opener(concepts_path, "rt" if op == "rb" else "r",
                    encoding=enc or "utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for raw in row.get("p18") or []:
                    fn = _norm_file(raw)
                    if fn and (not done or (row["qid"], fn) not in done):
                        if batch_size <= 0:
                            yield {"qid": row["qid"], "commons_file": fn}
                            continue
                        batch.append({"qid": row["qid"], "commons_file": fn})
                        if len(batch) >= batch_size:
                            yield batch
                            batch = []
        if batch:
            yield batch
    return factory


def _title_key(name: str) -> str:
    """标题匹配键:下划线/空格与首字母大小写归一(API 侧同规则)。"""
    return name.strip().lower().replace("_", " ")


class CommonsFetchStage(StreamStage):
    """任务行 → 产物行(API 元数据 + 字节下载 + 守门降级)。"""

    label = "commons_fetch"
    catch = (net.InfraError, httpx.HTTPError)   # 认缺白名单:429/超时等

    def __init__(self):
        self.fetched = 0

    async def _meta(self, file_title: str) -> dict | None:
        r = await self._meta_batch([file_title])
        return r.get(_title_key(file_title))

    async def _meta_batch(self, file_titles: list) -> dict:
        """多标题元数据查询(一次 ≤50 题,支持 continue 续页)。

        返回 {标题匹配键: imageinfo};不存在/被删的文件不在结果里。
        批量化动机:单文件查询时 API 限速 2/s 直接封顶下载吞吐,
        而老转储命中率仅个位数百分比——批查询把配额利用率 ×50。
        """
        key_infos: dict = {}
        # 注意:titles 的 `|` 分隔符必须裸传——httpx 会编码成 %7C,
        # 而 Wikimedia WAF 对 titles 中的编码竖杠回 403(实测,curl 裸 | 200)。
        from urllib.parse import urlencode, quote
        params = {"action": "query", "format": "json",
                  "titles": "|".join(f"File:{t}" for t in file_titles),
                  "prop": "imageinfo", "iiprop": "url|size|extmetadata"}
        base = API + "?" + urlencode(params, safe="|", quote_via=quote)
        for _round in range(4):                      # continue 最多追 3 轮
            r = await net.request("commons_api", "GET", base,
                                  headers={"User-Agent": _UA})
            body = r.json()
            q = body.get("query") or {}
            for page in (q.get("pages") or {}).values():
                info = (page.get("imageinfo") or [None])[0]
                if info:
                    key_infos[_title_key(
                        (page.get("title") or "").removeprefix("File:"))] = info
            cont = body.get("continue") or {}
            if not cont:
                break
            base = API + "?" + urlencode({**params, **cont},
                                         safe="|", quote_via=quote)
        return key_infos

    async def __call__(self, row):
        # 兼容单行;批行(list)走一次多标题查询再逐张下载
        rows = row if isinstance(row, list) else [row]
        by_key = await self._meta_batch([r["commons_file"] for r in rows])
        out = []
        for r in rows:
            info = by_key.get(_title_key(r["commons_file"]))
            if info is None:
                continue                               # 文件不存在/被删
            product = await self._fetch_one(r, info)
            if product is not None:
                out.append(product)
        return out or None

    async def _fetch_one(self, row: dict, info: dict):
        url = info.get("url") or ""
        if not url:
            return None
        data = b""
        async with net.stream("dl:commons", "GET", url,
                              headers={"User-Agent": _UA}) as resp:
            async for chunk in resp.aiter_bytes(1 << 16):
                data += chunk
                if len(data) > HARD_CAP_BYTES:
                    return None                       # 超封顶认缺
        # 非空校验+200 错误页兜底(net 层已拦非 2xx, 此为 2026-09-17 事故加固)
        if not data or data[:15].lstrip().lower().startswith(HTML_ERR_HEADS):
            return None
        em = info.get("extmetadata") or {}
        sha = hashlib.sha256(data).hexdigest()
        self.fetched += 1
        return {
            **row,
            "sha256": sha,
            "ext": row["commons_file"].rsplit(".", 1)[-1].lower(),
            "data": data,
            "tier": "orig",
            "page_bytes": len(data),
            "width": info.get("width"), "height": info.get("height"),
            "content_url": info.get("descriptionurl") or url,
            "license": (em.get("LicenseShortName") or {}).get("value"),
            "license_url": (em.get("LicenseUrl") or {}).get("value"),
            "author": _TAG_RE.sub(
                "", (em.get("Artist") or {}).get("value") or "").strip()
            or None,
        }


class CommonsBlobSink(StreamStage):
    """产物行 → blobs 池 + qid_images 账本(AppendManifestStore)。"""

    label = "commons_sink"
    concurrency = 1

    def __init__(self, *, blobs_root: str, manifest: str):
        self._root = blobs_root
        self._store = AppendManifestStore(manifest=manifest,
                                          lock_path=manifest + ".lock")
        self._store.load_index(
            key_of=lambda r: (r["qid"], r["sha256"]))
        self.sunk = 0

    async def __call__(self, row: dict):
        rel = f"{row['sha256'][:2]}/{row['sha256']}.{row['ext']}"
        record = {k: row.get(k) for k in
                  ("qid", "commons_file", "sha256", "ext", "tier",
                   "license", "license_url", "author", "content_url",
                   "page_bytes", "width", "height")}
        record["fetched_at"] = time.time()
        record["path"] = f"blobs/{rel}"
        await self._store.write(data=row["data"],
                                blob_path=f"{self._root}/{rel}",
                                key=(row["qid"], row["sha256"]),
                                record=record)
        self.sunk += 1
        return record
