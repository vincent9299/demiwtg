"""④ 配图·批式采集器(正式版,2026-09-14 晋升自夜航工作区 kb_night/batch_fetch.py)。

实战履历: 2026-09-12~14 连续 30+ 小时, 25 机 fleet 全速 ~100 张/秒,
五波共收 882 万图(P18/嵌入图/属性图/P935图库/档2扩展), 零数据丢失。
与流引擎版(flow_images.py)的数据契约完全一致: 账本行 schema、
(qid,file) 幂等键、内容寻址 blobs、idx%N 分片, 可互换混跑。
批式核心: MediaWiki 50 题批量查询(API 配额利用率×50)+两级流水+
令牌桶限速(礼貌口径与 operators/commons.py 同源, 归一化复用
_norm_file)。绕开流引擎的原因见 NIGHT_WATCH/交接文档: 引擎存在
偶发 worker 无超时 Future 卡死(stream.py:139, 两次复现未根因),
修复后可在算子层与本件合流。
"""
import re
import sys
import time
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

API = "https://commons.wikimedia.org/w/api.php"
ORIG_GUARD_BYTES = 10 << 20
HARD_CAP_BYTES = 64 << 20
THUMB_WIDTH = 1200
BATCH = 50
RETRY_BACKOFF = (30.0, 120.0, 300.0)
UA = ("demiwtg-kb-phase3/1.0 (Wikimedia bulk; contact: "
      f"{os.environ.get('DEMIWTG_CONTACT', 'ops@demiwtg.example')})")
_TAG_RE = re.compile("<[^>]+>")

import httpx
from operators.commons import _norm_file  # 同一归一化,勿分叉


class Bucket:
    """朴素令牌桶:rate 上限,不带突发(与平台限速语义对齐)。"""

    def __init__(self, rate: float):
        self._interval = 1.0 / rate
        self._next_at = 0.0

    async def take(self):
        while True:
            now = asyncio.get_running_loop().time()
            wait = self._next_at - now
            if wait <= 0:
                self._next_at = now + self._interval
                return
            await asyncio.sleep(wait)


def load_done(manifest: str) -> set:
    done = set()
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done.add((r["qid"], r["commons_file"]))
    return done


def iter_tasks(concepts_path: str, done: set, shard_i: int, shard_n: int):
    """(qid, commons_file) 任务流:全量扫描,按任务序分片,跳已收。"""
    op = "rb" if concepts_path.endswith(".gz") else "r"
    opener = gzip.open if op == "rb" else open
    idx = 0
    with opener(concepts_path, "rt" if op == "rb" else "r",
                encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = row.get("qid")
            for raw in row.get("p18") or []:
                fn = _norm_file(raw)
                if not fn:
                    continue
                if idx % shard_n == shard_i and (qid, fn) not in done:
                    yield {"qid": qid, "commons_file": fn}
                idx += 1


def title_key(name: str) -> str:
    return name.strip().lower().replace("_", " ")


class Fetcher:
    def __init__(self, args):
        self.api_bucket = Bucket(args.api_rate)
        self.dl_bucket = Bucket(args.dl_rate)
        self.dl_sem = asyncio.Semaphore(args.dl_conc)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=30, pool=15),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=0))
        self.sunk = 0
        self.miss_meta = 0
        self.miss_dl = 0

    async def aclose(self):
        await self.client.aclose()

    async def meta_batch(self, titles):
        """50 题一批查 imageinfo(continue ≤3 轮);返回 {title_key: info}。"""
        params = {"action": "query", "format": "json",
                  "titles": "|".join(f"File:{t}" for t in titles),
                  "prop": "imageinfo", "iiprop": "url|size|extmetadata",
                  "iiurlwidth": THUMB_WIDTH}
        url = API + "?" + urlencode(params, safe="|", quote_via=quote)
        out, last, rounds = {}, None, 0
        for attempt in range(4):
            await self.api_bucket.take()
            try:
                r = await self.client.get(url, headers={"User-Agent": UA})
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(2)
                continue
            if r.status_code == 200:
                body = r.json()
                rounds += 1
                for page in ((body.get("query") or {}).get("pages")
                             or {}).values():
                    info = (page.get("imageinfo") or [None])[0]
                    if info:
                        out[title_key(
                            (page.get("title") or "").removeprefix("File:"))
                            ] = info
                cont = body.get("continue") or {}
                if not cont or rounds >= 4:
                    return out
                url = API + "?" + urlencode(
                    {**params, **cont}, safe="|", quote_via=quote)
                continue
            if r.status_code == 429:
                await asyncio.sleep(RETRY_BACKOFF[
                    min(attempt, len(RETRY_BACKOFF) - 1)])
                last = f"HTTP {r.status_code}"
                continue
            r.raise_for_status()          # 403 等:抛错由上层弃批
        raise RuntimeError(f"meta_batch 重试用尽 last={last}")

    async def download(self, info):
        """按守门规则取字节;返回 (bytes, tier) 或 None。"""
        if info.get("size", 0) <= ORIG_GUARD_BYTES:
            url, tier = info.get("url") or "", "orig"
        else:
            url, tier = info.get("thumburl") or "", "thumb1200"
        if not url:
            return None
        async with self.dl_sem:
            await self.dl_bucket.take()
            data = b""
            async with self.client.stream("GET", url,
                                          headers={"User-Agent": UA}) as resp:
                async for chunk in resp.aiter_bytes(1 << 16):
                    data += chunk
                    if len(data) > HARD_CAP_BYTES:
                        return None
        return data, tier


class Sink:
    def __init__(self, root: str, manifest: str):
        self.root = root
        self.manifest = manifest
        self.lock_path = manifest + ".lock"
        os.makedirs(os.path.dirname(manifest) or ".", exist_ok=True)
        self._m = open(self.manifest, "a", encoding="utf-8")
        self._lf = open(self.lock_path, "a")

    def write(self, row, data, info, tier):
        sha = hashlib.sha256(data).hexdigest()
        ext = row["commons_file"].rsplit(".", 1)[-1].lower()
        rel = f"{sha[:2]}/{sha}.{ext}"
        path = f"{self.root}/{rel}"
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + f".tmp{os.getpid()}"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)          # 原子落;重名同内容无害
        em = info.get("extmetadata") or {}
        record = {"qid": row["qid"], "commons_file": row["commons_file"],
                  "sha256": sha, "ext": ext, "tier": tier,
                  "license": (em.get("LicenseShortName") or {}).get("value"),
                  "license_url": (em.get("LicenseUrl") or {}).get("value"),
                  "author": _TAG_RE.sub(
                      "", (em.get("Artist") or {}).get("value") or "").strip()
                  or None,
                  "content_url": info.get("descriptionurl"),
                  "page_bytes": len(data),
                  "width": info.get("width"), "height": info.get("height"),
                  "fetched_at": time.time(), "path": f"blobs/{rel}"}
        fcntl.flock(self._lf, fcntl.LOCK_EX)
        try:
            self._m.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._m.flush()
            os.fsync(self._m.fileno())
        finally:
            fcntl.flock(self._lf, fcntl.LOCK_UN)
        return True


async def run(args):
    done = load_done(args.manifest)
    fetcher = Fetcher(args)
    sink = Sink(args.blobs_root, args.manifest)
    t0 = time.time()
    hit_q = asyncio.Queue(maxsize=256)

    def progress():
        el = time.time() - t0
        print(f"[batch] sunk={fetcher.sunk:,} ({fetcher.sunk/el:.2f}/s) "
              f"miss_meta={fetcher.miss_meta:,} miss_dl={fetcher.miss_dl:,}",
              flush=True)

    async def dl_worker():
        while True:
            item = await hit_q.get()
            if item is None:
                return
            r, info = item
            try:
                got = await fetcher.download(info)
            except Exception:
                got = None
            if not got:
                fetcher.miss_dl += 1
                continue
            data, tier = got
            try:
                await asyncio.to_thread(sink.write, r, data, info, tier)
            except Exception as e:
                print(f"[batch] sink 异常 {type(e).__name__}: {str(e)[:80]}",
                      flush=True)
                continue
            fetcher.sunk += 1
            if fetcher.sunk % 500 == 0:
                progress()

    workers = [asyncio.create_task(dl_worker())
               for _ in range(args.dl_conc)]

    batches = 0
    buf = []
    for task in iter_tasks(args.concepts, done, args.shard_i, args.shard_n):
        buf.append(task)
        if len(buf) >= BATCH:
            try:
                infos = await fetcher.meta_batch(
                    [r["commons_file"] for r in buf])
                for r in buf:
                    info = infos.get(title_key(r["commons_file"]))
                    if info is None:
                        fetcher.miss_meta += 1
                    else:
                        await hit_q.put((r, info))
            except Exception as e:
                print(f"[batch] meta 弃批({len(buf)} 题): {type(e).__name__} "
                      f"{str(e)[:100]}", flush=True)
            batches += 1
            buf = []
    if buf:
        try:
            infos = await fetcher.meta_batch([r["commons_file"] for r in buf])
            for r in buf:
                info = infos.get(title_key(r["commons_file"]))
                if info is None:
                    fetcher.miss_meta += 1
                else:
                    await hit_q.put((r, info))
        except Exception as e:
            print(f"[batch] meta 弃批(尾批): {type(e).__name__}", flush=True)
    for _ in workers:
        await hit_q.put(None)
    await asyncio.gather(*workers)
    await fetcher.aclose()
    progress()
    print(f"[batch] 完成", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--concepts", required=True)
    p.add_argument("--blobs-root", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--shard", required=True, metavar="I/N")
    p.add_argument("--api-rate", type=float, default=2.0)
    p.add_argument("--dl-rate", type=float, default=4.0)
    p.add_argument("--dl-conc", type=int, default=8)
    args = p.parse_args()
    i, n = (int(x) for x in args.shard.split("/"))
    args.shard_i, args.shard_n = i, n
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
