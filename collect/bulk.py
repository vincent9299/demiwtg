"""整包数据集摄入（bulk ingest）——数据集驱动的"反向打标"进水口。

与搜索驱动模式（按实例 query 逐源检索 API）相反：直接流式解析开源数据集的
原生标签元数据，按实例名单预筛命中图，下载入湖后交 VLM 精校。省掉逐实例
检索阶段，长尾召回不受搜索词/限速约束。

首源 danbooru2023（nyanko7/danbooru2023，HF）：
- metadata/posts.tar.gz（2.8GB 压缩 JSONL）：全量 post 的 tag_string/score/rating/
  尺寸/md5/file_url，Range 支持，分段缓存至 state/ 后扫描不再过网；
- 图片 URL：posts 元数据自带 file_url（cdn.donmai.us/original/<md5 分桶>/
  <md5>.<ext>），单图按需直取；HF 图片区是 1000 个 ~8.6GB 的 data-NNNN.tar
  整包，无单图直链，不走。

流程（AGENTS.md 合规：预筛→sha256 入湖→粗打标→VLM 精校）：
1. 从 instances.json 的 query/aliases 构建 booru tag → 实例名索引；
2. 流式扫 posts：rating 过滤 + tag 交集命中 → 每实例按 score 留 top K，
   选单落 state/collect/bulk/<dataset>/selected.jsonl（--reuse 断点复用）；
3. 命中的图走 download_and_store（sha256 落 blobs、解码复验、分辨率门），
   成功 flush 进 images.jsonl（pipeline._update_master_manifest，实例名为粗
   打标）并投递打标队列（curation.annotate_vlm.enqueue_annotate）。

用法：
    python3 collect/cli.py bulk --taxonomy data/taxonomy/instances.json
    python3 collect/cli.py bulk --taxonomy data/taxonomy/instances.json \
        --max-posts 200000 --per-instance 2 --dry-run     # 小规模试算
    python3 collect/cli.py bulk --taxonomy ... --jobs 初音未来 --reuse
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import io
import json
import os
import re
import sys
import tarfile
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# 命名冲突防护：与 stream.py 同理（`python3 collect/cli.py` 时 sys.path[0]=collect/）
_here = os.path.dirname(os.path.abspath(__file__))
_shadow = [p for p in sys.path if p and os.path.abspath(p) == _here]
for _p in _shadow:
    sys.path.remove(_p)
if "queue" in sys.modules and getattr(sys.modules["queue"], "__file__", "") and \
        os.path.abspath(sys.modules["queue"].__file__) == os.path.join(_here, "queue.py"):
    del sys.modules["queue"]
try:
    import queue as stdqueue  # noqa: F401  仅防遮蔽，本模块暂未直接使用
finally:
    sys.path[:] = _shadow + sys.path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from collect import downloader, models, pipeline
    from collect.config import DEFAULTS, EffectiveConfig
    from collect.util import RateLimiter
else:
    from . import downloader, models, pipeline
    from .config import DEFAULTS, EffectiveConfig
    from .util import RateLimiter

# 打标队列归属消费方模块（curation）；collect 只是生产者（同 stream.py 契约）
try:
    from curation.annotate_vlm import enqueue_annotate
except (ImportError, SystemExit):
    enqueue_annotate = None

_HF = "https://huggingface.co/datasets/nyanko7/danbooru2023/resolve/main"

# 数据集 profile（新增整包源在此登记；授权姿态按源事实填写）
DATASETS = {
    "danbooru2023": {
        "posts_url": _HF + "/metadata/posts.tar.gz",
        # LFS sha256（HF API lfs.oid）：分段下载可能被 CDN 重试污染，落盘后必验
        "posts_sha256": "5228396c538424bd4ac87806be09a0b4943dd8756e88d264b4143c89883a4428",
        "posts_member": "posts.json",
        "landing_tpl": "https://danbooru.donmai.us/posts/{pid}",
        "source": "bulk_danbooru2023",
        "ratings": ("g",),                    # 与 danbooru 适配器一致：只采全年龄向
        "authorized": False,                  # 社区上传作品，license 逐图未知
        "license_default": "danbooru2023 dump (license unknown)",
        "dl_suffixes": ("cdn.donmai.us",),    # 图片取元数据自带 file_url（donmai CDN）
    },
}

MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
}

FLUSH_EVERY = 20          # 成功缓冲满 N 条 flush 一次主清单

_TAG_OK = re.compile(r"^[a-z0-9_:()!?.\-]+$")


def _repo_root(meta_dir: str) -> str:
    """AGENTS.md 约定：仓库根由 --meta 向上三级推导（与 cli/pipeline 一致）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(os.path.normpath(meta_dir)))))


def _state_dir(meta_dir: str, dataset: str) -> str:
    """运行时状态：state/collect/bulk/<dataset>/（不进数据湖、不入 git）。"""
    d = os.path.join(_repo_root(meta_dir), "state", "collect", "bulk", dataset)
    os.makedirs(d, exist_ok=True)
    return d


def _norm_tag(s: str) -> str:
    """实例 query/别名 → booru tag 形态（小写、空格/连字符转下划线）。"""
    return re.sub(r"[\s\-]+", "_", (s or "").strip().lower())


def build_tag_index(taxonomy_path: str, jobs_substr=None) -> dict:
    """tag → [实例名] 反查索引。只收 ASCII 词（booru tag 无中文）；
    query 与 aliases 同池，去重。"""
    with open(taxonomy_path, encoding="utf-8") as f:
        insts = json.load(f)["instances"]
    idx = defaultdict(list)
    for it in insts:
        name = it.get("name", "")
        if jobs_substr and not any(t in name for t in jobs_substr):
            continue
        toks = list(it.get("query") or []) + list(it.get("aliases") or [])
        seen = set()
        for tok in toks:
            tag = _norm_tag(tok)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            if not _TAG_OK.match(tag) or len(tag) < 3 or not any(c.isalpha() for c in tag):
                continue
            idx[tag].append(name)
    return dict(idx)


# ---------------------------------------------------------------------------
# 阶段一：扫 posts 元数据（先并行分段缓存 tar.gz 到 state 再扫，重扫不再过网）
# ---------------------------------------------------------------------------
def _dl_range(url: str, start: int, end: int, path: str, timeout: int = 300) -> None:
    """分段下载 [start, end) 写到 path 的对应偏移（Range + seek 写）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "multimodal-collector/1.0",
                      "Range": f"bytes={start}-{end - 1}"})
    for attempt in range(3):
        try:
            got = 0
            with urllib.request.urlopen(req, timeout=timeout) as resp, \
                    open(path, "r+b") as f:
                f.seek(start)
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    f.write(chunk)
            if got != end - start:
                # CDN 重试可能切到不同边缘返回短读/错段，宁重下不静默
                raise IOError(f"分段长度不符: {got} != {end - start}")
            return
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            print(f"[bulk] 分段 {start/1e6:.0f}-{end/1e6:.0f} MB 重试: {e}",
                  flush=True)
            time.sleep(2 ** attempt)


def _ensure_posts_cache(profile: dict, cache_dir: str,
                        dl_threads: int = 8, limit_mb: int = 0) -> str:
    """posts.tar.gz 并行分段下载缓存（gzip 顺序解压只需前缀完整，limit_mb
    截断配合 --max-posts 试点用）；返回本地路径。"""
    cache = os.path.join(cache_dir, "posts.tar.gz")
    done = cache + ".done"
    want = profile.get("posts_sha256")
    # 完成标记记录“字节数 sha256”：两者都匹配才复用，否则重下（旧格式/损坏一律重走）
    if os.path.exists(done) and os.path.exists(cache):
        try:
            with open(done) as f:
                parts = f.read().split()
            if int(parts[0]) == os.path.getsize(cache) and \
                    (not want or (len(parts) > 1 and parts[1] == want)):
                return cache
        except (ValueError, OSError, IndexError):
            pass
    # 探总长（HEAD）
    hreq = urllib.request.Request(
        profile["posts_url"], method="HEAD",
        headers={"User-Agent": "multimodal-collector/1.0"})
    with urllib.request.urlopen(hreq, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
    if limit_mb:
        total = min(total, limit_mb * 1024 * 1024)
    if os.path.exists(cache) and os.path.getsize(cache) != total:
        os.remove(cache)          # 上次下载目标不同，重下
    if not os.path.exists(cache):
        with open(cache, "wb") as f:
            f.truncate(total)
    chunk = max(4 * 1024 * 1024, total // (dl_threads * 16) if total else 0)
    spans = [(s, min(s + chunk, total)) for s in range(0, total, chunk)]
    # 分段完成账本（pid:start:end）：上次跑挂后续传只补缺段，不整文件重下；
    # pid 不匹配（进程换命）则作废。最终以 sha256 验收兑底，账本误记不会引入脏数据
    seg_done = cache + ".segs"
    done_spans = set()
    try:
        with open(seg_done) as f:
            parts = f.read().split()
        if parts and parts[0] == str(os.getpid()):
            pass  # pid 巧合重叠不可信，作废
        elif parts:
            done_spans = {(int(a), int(b)) for a, b in
                          (p.split(":") for p in parts[1:] if ":" in p)}
    except (OSError, ValueError):
        done_spans = set()
    seg_lock = threading.Lock()
    seg_f = open(seg_done, "a", encoding="utf-8")
    if not done_spans:
        seg_f.truncate(0)
        seg_f.write(f"{os.getpid()}\n")
        seg_f.flush()
    n_done = [0]
    lock = threading.Lock()
    t0 = time.time()

    def _job(span):
        if span in done_spans:
            with lock:
                n_done[0] += 1
            return
        _dl_range(profile["posts_url"], span[0], span[1], cache)
        with seg_lock:
            seg_f.write(f"{span[0]}:{span[1]}\n")
            seg_f.flush()
        with lock:
            n_done[0] += 1
            if n_done[0] % 16 == 0 or n_done[0] == len(spans):
                done_mb = n_done[0] * chunk / 1e6
                dt = max(time.time() - t0, 1)
                print(f"[bulk] posts 缓存 {min(done_mb, total/1e6):.0f}/{total/1e6:.0f} MB "
                      f"({done_mb/dt:.1f} MB/s)", flush=True)

    todo = [s for s in spans if s not in done_spans]
    print(f"[bulk] posts 缓存下载：{total/1e6:.0f} MB / {len(spans)} 段 / "
          f"{dl_threads} 线程"
          + (f"（续传：已完成 {len(done_spans)} 段，只下 {len(todo)} 段）"
             if todo and done_spans else ""), flush=True)
    with ThreadPoolExecutor(max_workers=dl_threads) as ex:
        list(ex.map(_job, todo))
    seg_f.close()
    # sha256 验收（防分段下载静默损坏）：不过则删缓存报错，杜绝 .done 误标；
    # limit_mb 截断试点本就不完整，不验（done 会被上游 max_posts 逻辑删除）
    got_sha = ""
    if want and not limit_mb:
        h = hashlib.sha256()
        with open(cache, "rb") as f:
            while True:
                blk = f.read(8 << 20)
                if not blk:
                    break
                h.update(blk)
        got_sha = h.hexdigest()
        if got_sha != want:
            os.remove(cache)
            if os.path.exists(seg_done):
                os.remove(seg_done)   # 账本作废：重下必须从零来
            raise SystemExit(
                f"[bulk] posts 缓存 sha256 不符（分段下载被污染），已删除，重跑重下: {cache}")
    if os.path.exists(seg_done):
        os.remove(seg_done)           # 验收通过，账本使命结束
    with open(done, "w") as f:
        f.write(f"{total} {got_sha}\n")
    print(f"[bulk] posts 缓存就绪 {total/1e6:.0f} MB → {cache} "
          f"({time.time()-t0:.0f}s)", flush=True)
    return cache


def scan_posts(profile: dict, tag_index: dict, per_instance: int,
               sel_path: str, cache_dir: str, max_posts: int = 0,
               dl_threads: int = 8, limit_mb: int = 0) -> int:
    """逐行匹配本地缓存的 posts JSONL，每实例按 score 留 top K。返回选中条数。"""
    best = {}          # instance -> [(score, row)] 小顶堆
    n_posts = n_hit = 0
    t0 = time.time()
    posts_path = _ensure_posts_cache(profile, cache_dir, dl_threads, limit_mb)
    truncated = False
    with open(posts_path, "rb") as raw:
        try:
            tf = tarfile.open(fileobj=raw, mode="r|gz")
        except Exception:
            raise SystemExit("[bulk] posts 缓存损坏，删除后重试: " + posts_path)
        ratings = set(profile["ratings"])
        try:
            for m in tf:
                if not m.isfile() or os.path.basename(m.name) != profile["posts_member"]:
                    continue
                f = tf.extractfile(m)
                try:
                    for line in f:
                        n_posts += 1
                        if max_posts and n_posts > max_posts:
                            break
                        try:
                            rec = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if rec.get("rating") not in ratings:
                            continue
                        if rec.get("is_deleted"):   # 删档 post 文件已被抹除，早筛
                            continue
                        tags = set((rec.get("tag_string") or "").split())
                        hit_tags = tags & tag_index.keys()
                        if not hit_tags:
                            continue
                        row = {
                            "post_id": rec["id"], "score": rec.get("score") or 0,
                            "md5": rec.get("md5"), "w": rec.get("image_width"),
                            "h": rec.get("image_height"),
                            "file_url": rec.get("file_url"),
                            "file_ext": rec.get("file_ext"),
                            "tags": sorted(hit_tags),
                        }
                        # 同一 post 可能经多个 tag 命中同一实例（如 hatsune_miku/miku）：
                        # 按实例去重后再入堆，避免 (score, post_id) 重复后比较 dict
                        inst_tag = {}
                        for tag in hit_tags:
                            for inst in tag_index[tag]:
                                inst_tag.setdefault(inst, tag)
                        for inst, tag in inst_tag.items():
                            row_i = {**row, "instance": inst, "matched_tag": tag}
                            # 堆元素 (score, post_id, row)：post_id 作 tie-break
                            heap = best.setdefault(inst, [])
                            if len(heap) < per_instance:
                                heapq.heappush(heap, (row_i["score"], row_i["post_id"], row_i))
                            elif row_i["score"] > heap[0][0]:
                                heapq.heapreplace(heap, (row_i["score"], row_i["post_id"], row_i))
                            n_hit += 1
                        if n_posts % 500000 == 0:
                            print(f"[bulk] 已扫 {n_posts:,} posts / 命中 {n_hit:,} / "
                                  f"实例 {len(best):,} / {time.time()-t0:.0f}s",
                                  flush=True)
                except (OSError, EOFError, tarfile.TarError) as e:
                    truncated = True   # limit_mb 截断或缓存尾部损坏：读到不完整流尾即止
                    if not limit_mb:
                        print(f"[warn] posts 流提前中断（缓存可能损坏）: {e}",
                              flush=True)
                    break
                if max_posts and n_posts > max_posts:
                    break
        finally:
            tf.close()
    rows = [r for heap in best.values() for _, _, r in heap]
    rows.sort(key=lambda r: (-r["score"], r["post_id"]))
    tmp = sel_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, sel_path)
    print(f"[bulk] 扫描完成：{n_posts:,} posts / 命中 {n_hit:,} / "
          f"选中 {len(rows):,} 条（覆盖 {len(best):,} 实例）/ {time.time()-t0:.0f}s",
          flush=True)
    return len(rows)


def load_selected(sel_path: str) -> list:
    rows = []
    with open(sel_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# 阶段二：命中图下载入湖（复用 downloader/pipeline/打标队列）
# ---------------------------------------------------------------------------
def _existing_asset_ids(meta_dir: str) -> set:
    """images.jsonl 已登记的 asset_ids 值集合（断点续跑去重：同 post 不重下）。"""
    out = set()
    mpath = os.path.join(meta_dir, "images.jsonl")
    if not os.path.exists(mpath):
        return out
    with open(mpath, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for v in (rec.get("asset_ids") or {}).values():
                if v:
                    out.add(str(v))
    return out


def _candidate_of(profile: dict, row: dict) -> "models.Candidate":
    pid = row["post_id"]
    ext = (row.get("file_ext") or "").lower()
    return models.Candidate(
        source=profile["source"],
        source_kind=models.SOURCE_KIND_DATASET,
        asset_id=f"danbooru2023-{pid}",
        instance=row["instance"],
        query=row["matched_tag"],
        query_lang="en",
        landing_url=profile["landing_tpl"].format(pid=pid),
        content_url=row["file_url"],
        declared_mime=MIME_BY_EXT.get(ext),
        declared_width=row.get("w"),
        declared_height=row.get("h"),
        author=None,
        credit=None,
        license_raw=profile["license_default"],
        source_authorized=profile["authorized"],
        evidence={"matched_tags": row["tags"], "dataset": "danbooru2023",
                  "md5": row.get("md5")},
        source_score=row.get("score"),
        status=models.STATUS_CANDIDATE,
    )


def download_selected(profile: dict, rows: list,
                      meta_dir: str, images_dir: str, run_id: str,
                      workers: int, interval: float) -> dict:
    cfg = EffectiveConfig.resolve(dict(DEFAULTS), {})
    limiter = RateLimiter(max(interval, 0.05))
    have = _existing_asset_ids(meta_dir)
    # file_url 白名单前缀（与 dl_suffixes 同源）：元数据脏值/缺值早筛
    url_ok = tuple("https://" + s + "/" for s in profile["dl_suffixes"])
    todo, skip_done, skip_nofile = [], 0, 0
    for r in rows:
        if f"danbooru2023-{r['post_id']}" in have:
            skip_done += 1
            continue
        url = r.get("file_url") or ""
        ext = (r.get("file_ext") or "").lower()
        if not url.startswith(url_ok) or ext not in MIME_BY_EXT:
            skip_nofile += 1        # 无 CDN 直链或非图片格式，不入湖
            continue
        todo.append(_candidate_of(profile, r))
    print(f"[bulk] 待下载 {len(todo)} / 已入湖跳过 {skip_done} / "
          f"无直链或非图跳过 {skip_nofile}", flush=True)

    stats = defaultdict(int)
    buf, url_done = [], {}
    lock = threading.Lock()
    health = defaultdict(lambda: defaultdict(int))

    def _flush(force=False):
        with lock:
            if not buf or (not force and len(buf) < FLUSH_EVERY):
                return
            batch, buf[:] = buf[:], []
        pipeline._update_master_manifest(meta_dir, batch, run_id)
        for d in batch:
            if enqueue_annotate is not None:
                try:
                    enqueue_annotate(meta_dir, d.sha256,
                                     [d.instance] if d.instance else [],
                                     pipeline._rel_path(d.local_path))
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] 打标队列投递失败（回填兜底）: {e}", flush=True)
        print(f"[bulk] flush {len(batch)} 张进 images.jsonl", flush=True)

    def _worker(c: "models.Candidate"):
        with lock:
            prev = url_done.get(c.content_url)
        if prev is not None:
            # 同一 post 命中多个实例：不重复下载，复用已落盘产物补标签关联
            sha, local = prev
            c.sha256, c.local_path = sha, local
            c.status = models.STATUS_DOWNLOADED
            with lock:
                buf.append(c)
            return
        limiter.acquire(profile["dl_suffixes"][0])
        ok, dl = downloader.download_and_store(
            c, cfg, allowed_suffixes=profile["dl_suffixes"],
            images_dir=images_dir, rate_limiter=limiter)
        d = dl[0]
        if ok:
            with lock:
                url_done[c.content_url] = (d.sha256, d.local_path)
                buf.append(d)
                stats["ok"] += 1
                stats["bytes"] += d.actual_size or 0
            health[profile["source"]]["dl_ok"] += 1
            if stats["ok"] % 50 == 0:
                print(f"[bulk] 下载 {stats['ok']}/{len(todo)} "
                      f"({stats['bytes']/1e6:.0f} MB)", flush=True)
        else:
            stats["fail"] += 1
            health[profile["source"]][
                "dl_dead" if d.fail_kind in ("dead_link", "hotlink_forbidden")
                else "dl_fail"] += 1
            if stats["fail"] <= 5:
                print(f"[warn] 下载失败 {d.content_url}: "
                      f"{d.fail_reason or d.reject_reason or '未知'}", flush=True)

    t0 = time.time()
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(_worker, todo))
    _flush(force=True)
    if health:
        pipeline._merge_health(meta_dir, dict(health))
    dt = time.time() - t0
    print(f"[bulk] 下载完成：成功 {stats['ok']} / 失败 {stats['fail']} / "
          f"{stats['bytes']/1e6:.0f} MB / {dt:.0f}s"
          + ("" if enqueue_annotate is not None
             else "（打标队列不可用，由存量回填兜底）"), flush=True)
    return dict(stats)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def bulk(meta_dir: str, taxonomy: str, dataset: str, images_dir: str,
         run_id: str, per_instance: int, max_posts: int, workers: int,
         interval: float, jobs_substr=None, dry_run: bool = False,
         rescan: bool = False, dl_threads: int = 8, cache_mb: int = 0) -> None:
    profile = DATASETS[dataset]
    state_dir = _state_dir(meta_dir, dataset)
    sel_path = os.path.join(state_dir, "selected.jsonl")
    done_flag = os.path.join(state_dir, "selected.done")

    tag_index = build_tag_index(taxonomy, jobs_substr)
    print(f"[bulk] tag 索引 {len(tag_index):,} 条"
          + (f"（--jobs 过滤: {jobs_substr}）" if jobs_substr else ""), flush=True)
    if not tag_index:
        print("[bulk] tag 索引为空，退出", flush=True)
        return

    if os.path.exists(sel_path) and os.path.exists(done_flag) and not rescan:
        rows = load_selected(sel_path)
        print(f"[bulk] 复用既有选单 {len(rows):,} 条（--rescan 可重扫）", flush=True)
    else:
        n = scan_posts(profile, tag_index, per_instance, sel_path,
                       state_dir, max_posts, dl_threads, cache_mb)
        with open(done_flag, "w", encoding="utf-8") as f:
            f.write(f"{n}\n")
        if max_posts:
            os.remove(done_flag)   # 截断扫描不算完成，下次默认重扫
        rows = load_selected(sel_path)

    if dry_run:
        by_inst = defaultdict(int)
        for r in rows:
            by_inst[r["instance"]] += 1
        print(f"[bulk][dry-run] 命中 {len(rows):,} 条 / 覆盖 {len(by_inst):,} 实例；"
              f"示例：", flush=True)
        for r in rows[:10]:
            print(f"   {r['instance']} ← {r['matched_tag']} "
                  f"(post {r['post_id']}, score {r['score']})", flush=True)
        return

    download_selected(profile, rows, meta_dir, images_dir, run_id,
                      workers, interval)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="整包数据集摄入（数据集驱动反向打标：预筛→入湖→粗打标→VLM 精校）")
    ap.add_argument("--taxonomy", default="data/taxonomy/instances.json",
                    help="标签体系实例元文件（默认 data/taxonomy/instances.json）")
    ap.add_argument("--meta", default="data/dataset/meta",
                    help="元数据根目录（默认 data/dataset/meta）")
    ap.add_argument("--images-dir", default="data/dataset/blobs",
                    help="图片内容寻址存储根目录（默认 data/dataset/blobs）")
    ap.add_argument("--dataset", default="danbooru2023", choices=sorted(DATASETS),
                    help="整包数据集（默认 danbooru2023）")
    ap.add_argument("--per-instance", type=int, default=8,
                    help="每实例最多保留候选数（按 score，默认 8）")
    ap.add_argument("--max-posts", type=int, default=0,
                    help="扫描 post 数上限（默认 0=全量；试点用，如 200000）")
    ap.add_argument("--workers", type=int, default=4, help="下载线程数（默认 4）")
    ap.add_argument("--dl-threads", type=int, default=8,
                    help="posts 元数据缓存并行分段数（默认 8）")
    ap.add_argument("--cache-mb", type=int, default=0,
                    help="posts 缓存字节上限 MB（默认 0=全量 2.8G；试点截断配合 --max-posts）")
    ap.add_argument("--interval", type=float, default=0.5,
                    help="下载限速间隔秒（默认 0.5；HF CDN 抗压能力强于搜索 API）")
    ap.add_argument("--jobs", default="",
                    help="实例名子串过滤（逗号分隔，试点用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只扫描匹配，不下载（看命中面）")
    ap.add_argument("--rescan", action="store_true",
                    help="忽略既有选单重新扫描（默认复用 selected.jsonl）")
    args = ap.parse_args(argv)

    run_id = time.strftime("bulk_%Y%m%d_%H%M%S")
    bulk(args.meta, args.taxonomy, args.dataset, args.images_dir, run_id,
         per_instance=args.per_instance, max_posts=args.max_posts,
         workers=args.workers, interval=args.interval,
         jobs_substr=[t for t in args.jobs.split(",") if t],
         dry_run=args.dry_run, rescan=args.rescan,
         dl_threads=args.dl_threads, cache_mb=args.cache_mb)


if __name__ == "__main__":
    main()
