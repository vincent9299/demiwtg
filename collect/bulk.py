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
2. 流式扫 posts：rating 过滤 + 价值门（score/分辨率/格式/负面 tag/争议标记，
   元数据预筛零成本，避免下载后才被拒）+ tag 交集命中 → 每实例按 score 留
   top K → 按 pixel_hash 跨实例去重，选单落 state/collect/bulk/<dataset>/
   selected.jsonl（--reuse 断点复用）；
3. 命中的图走 download_and_store（sha256 落 blobs、解码复验、分辨率门），
   成功 flush 进 images.jsonl（pipeline._update_master_manifest，实例名为粗
   打标）并投递打标队列（curation.annotate_vlm.enqueue_annotate）。

代表作模式（--by-character）：不做 instances 体系匹配，价值门（rating/
删档/争议/待审/格式/分辨率/负面 tag）全过后按 tag_string_character 分组，
每角色留 top1，按代表作分数门（--min-score）裁剪后下载。角色 tag 恰命中
体系词表时挂实例名粗打标，否则 instances 为空交 VLM 精校/人审。

用法：
    python3 collect/cli.py bulk --taxonomy data/taxonomy/instances.json
    python3 collect/cli.py bulk --taxonomy data/taxonomy/instances.json \
        --max-posts 200000 --per-instance 2 --dry-run     # 小规模试算
    python3 collect/cli.py bulk --taxonomy ... --jobs 初音未来 --reuse
    python3 collect/cli.py bulk --by-character --min-score 10  # 代表作模式
    python3 collect/cli.py bulk --recover-meta    # 回捞已入湖 post 的原始元数据
"""

from __future__ import annotations

import argparse
import copy
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

# 价值门：命中负面 tag 直接弃（低质/坏链/翻译搬运图）
BAD_TAGS = frozenset({
    "lowres", "bad_id", "bad_link", "bad_pixiv_id", "bad_twitter_id",
    "watermark", "watermarked", "translated", "commentary",
    "third-party_edit", "censored", "spoilers", "downscaled",
})


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
               dl_threads: int = 8, limit_mb: int = 0,
               min_score: int = 0, min_side: int = 0) -> int:
    """逐行匹配本地缓存的 posts JSONL，每实例按 score 留 top K。返回选中条数。

    价值门（元数据预筛）：min_score 投票分下限；min_side 短边像素下限
    （与下载阶段 min_resolution 同口径，提前拦住注定被拒的图）；非图片
    格式（mp4/webm/zip）与负面 tag/争议标记（is_flagged/is_pending）直接弃。"""
    best = {}          # instance -> [(score, row)] 小顶堆
    n_posts = n_hit = 0
    reject = defaultdict(int)     # 各价值门拒绝计数（诊断用）
    img_exts = set(MIME_BY_EXT)
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
                        if rec.get("is_flagged") or rec.get("is_pending"):
                            reject["flagged"] += 1  # 争议/待审图不要
                            continue
                        if min_score and (rec.get("score") or 0) < min_score:
                            reject["score"] += 1
                            continue
                        if (rec.get("file_ext") or "").lower() not in img_exts:
                            reject["format"] += 1   # mp4/webm/zip 非图，占 topK 名额没意义
                            continue
                        w, h = rec.get("image_width") or 0, rec.get("image_height") or 0
                        if min_side and min(w, h) < min_side:
                            reject["resolution"] += 1
                            continue
                        tags = set((rec.get("tag_string") or "").split())
                        if tags & BAD_TAGS:
                            reject["bad_tags"] += 1
                            continue
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
                            # 价值字段：fav_count 供排序参考；pixel_hash（media_asset
                            # 内）跨图去重，缺失时退化用 md5（同为内容指纹）
                            "fav_count": rec.get("fav_count") or 0,
                            "pixel_hash": (rec.get("media_asset") or {}).get("pixel_hash")
                            or rec.get("md5"),
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
    # pixel_hash 跨实例去重：同一张图内容命中多个实例时只留最高分那条，
    # 其余靠下载阶段 url_done 复用兜底（此处提前省掉重复行）
    seen_hash = set()
    deduped = []
    for r in rows:
        ph = r.get("pixel_hash")
        if ph:
            if ph in seen_hash:
                reject["pixel_dup"] += 1
                continue
            seen_hash.add(ph)
        deduped.append(r)
    rows = deduped
    tmp = sel_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, sel_path)
    rej_msg = " / ".join(f"{k} {v:,}" for k, v in sorted(reject.items())) or "无"
    print(f"[bulk] 扫描完成：{n_posts:,} posts / 命中 {n_hit:,} / "
          f"选中 {len(rows):,} 条（覆盖 {len(best):,} 实例）/ {time.time()-t0:.0f}s",
          flush=True)
    print(f"[bulk] 价值门拒绝：{rej_msg}", flush=True)
    return len(rows)


def _load_char_vocab(cache_dir: str) -> set:
    """danbooru 官方 character tag 词表（category=4，tags API 拉取落盘于
    state/.../character_tags.json）；代表作模式只认词表内角色，滤掉 alias/
    拼写变体等非规范 tag，与口径分析（44,509 角色基线）对齐。"""
    p = os.path.join(cache_dir, "character_tags.json")
    if not os.path.exists(p):
        return set()
    with open(p, encoding="utf-8") as f:
        return {t["name"] for t in json.load(f)["tags"]}


def scan_posts_by_character(profile: dict, tag_index: dict, sel_path: str,
                            cache_dir: str, max_posts: int = 0,
                            dl_threads: int = 8, limit_mb: int = 0,
                            min_score: int = 10, min_side: int = 768) -> int:
    """代表作模式选单：价值门与 scan_posts 同口径（rating/删档/争议/待审/
    格式/分辨率/负面 tag，扫描期不卡分数），但不做 instances 匹配——过门后
    按 tag_string_character 分组（仅认官方词表内的规范角色 tag），每角色留
    top1（score 并列取 post_id 小者），top1 >= min_score 的角色出线。
    多角色图（跨界合集等）只归 tag_string_character 首位角色（与口径分析
    44,509/20,135 基线一致；否则一张 13 角色合集图会同时充当 13 个角色的
    代表作，长尾角色虚胖）。

    首位归属下每 post 至多进一个角色的堆，选单行数 = 出线角色数，无重复
    post；角色 tag 恰命中 tag_index（实例 query/aliases 词表）→ 挂实例名
    作粗打标，未命中 instance 留空。扫描漏斗与代表作分布打印供口径核对。
    返回选单行数。"""
    best = {}          # character -> (score, post_id, row)
    n_posts = n_rating = n_pass = 0
    reject = defaultdict(int)
    img_exts = set(MIME_BY_EXT)
    t0 = time.time()
    posts_path = _ensure_posts_cache(profile, cache_dir, dl_threads, limit_mb)
    vocab = _load_char_vocab(cache_dir)
    if vocab:
        print(f"[bulk] 角色词表 {len(vocab):,} 个（仅规范 tag 计入角色）",
              flush=True)
    else:
        print("[warn] 无 character_tags.json 词表，tag_string_character 全收", flush=True)
    ratings = set(profile["ratings"])
    with open(posts_path, "rb") as raw:
        try:
            tf = tarfile.open(fileobj=raw, mode="r|gz")
        except Exception:
            raise SystemExit("[bulk] posts 缓存损坏，删除后重试: " + posts_path)
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
                        n_rating += 1
                        if rec.get("is_deleted"):
                            reject["deleted"] += 1
                            continue
                        if rec.get("is_flagged") or rec.get("is_pending"):
                            reject["flagged"] += 1
                            continue
                        if (rec.get("file_ext") or "").lower() not in img_exts:
                            reject["format"] += 1
                            continue
                        w, h = rec.get("image_width") or 0, rec.get("image_height") or 0
                        if min_side and min(w, h) < min_side:
                            reject["resolution"] += 1
                            continue
                        tags = set((rec.get("tag_string") or "").split())
                        if tags & BAD_TAGS:
                            reject["bad_tags"] += 1
                            continue
                        n_pass += 1
                        chars = (rec.get("tag_string_character") or "").split()
                        if vocab:
                            chars = [c for c in chars if c in vocab]
                        if not chars:
                            continue
                        chars = chars[:1]   # 首位角色归属（多角色图不重复计）
                        sc = rec.get("score") or 0
                        pid = rec["id"]
                        row = None
                        for ch in chars:
                            cur = best.get(ch)
                            if cur is None or sc > cur[0] or \
                                    (sc == cur[0] and pid < cur[1]):
                                if row is None:
                                    row = {
                                        "post_id": pid, "score": sc,
                                        "md5": rec.get("md5"),
                                        "w": w, "h": h,
                                        "file_url": rec.get("file_url"),
                                        "file_ext": rec.get("file_ext"),
                                        "fav_count": rec.get("fav_count") or 0,
                                        "pixel_hash": (rec.get("media_asset") or {})
                                        .get("pixel_hash") or rec.get("md5"),
                                    }
                                best[ch] = (sc, pid, row)
                        if n_posts % 500000 == 0:
                            print(f"[bulk] 已扫 {n_posts:,} posts / 过门 {n_pass:,} / "
                                  f"角色 {len(best):,} / {time.time()-t0:.0f}s",
                                  flush=True)
                except (OSError, EOFError, tarfile.TarError) as e:
                    if not limit_mb:
                        print(f"[warn] posts 流提前中断（缓存可能损坏）: {e}",
                              flush=True)
                    break
                if max_posts and n_posts > max_posts:
                    break
        finally:
            tf.close()
    # 代表作分布（口径核对用：先取 top1 再卡分与先卡分再取 top1 等价）
    top1 = [sc for sc, _, _ in best.values()]
    b100 = sum(1 for s in top1 if s >= 100)
    b30 = sum(1 for s in top1 if 30 <= s < 100)
    b10 = sum(1 for s in top1 if 10 <= s < 30)
    b1 = sum(1 for s in top1 if 1 <= s < 10)
    b0 = len(top1) - b100 - b30 - b10 - b1
    sel_chars = sorted(((ch, sc, pid, row) for ch, (sc, pid, row) in best.items()
                        if sc >= min_score), key=lambda t: (-t[1], t[2]))
    rows = []
    for ch, sc, pid, row in sel_chars:
        # 一个角色一行（张数=角色数是口径硬要求）；同 tag 映射到多实例时
        # instances 存列表，下载阶段逐实例补关联
        rows.append({**row, "tags": [ch], "character": ch,
                     "instance": "", "instances": tag_index.get(ch) or [],
                     "matched_tag": ch})
    tmp = sel_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, sel_path)
    rej_msg = " / ".join(f"{k} {v:,}" for k, v in sorted(reject.items())) or "无"
    print(f"[bulk] 漏斗：全量 {n_posts:,} → rating {n_rating:,} → "
          f"过卫生/格式/分辨率/负面tag门 {n_pass:,} / {time.time()-t0:.0f}s",
          flush=True)
    print(f"[bulk] 各门拒绝：{rej_msg}", flush=True)
    print(f"[bulk] 角色（有角色 tag）{len(best):,} 个，代表作分布："
          f"≥100 {b100:,} / 30–99 {b30:,} / 10–29 {b10:,} / 1–9 {b1:,} / ≤0 {b0:,}",
          flush=True)
    print(f"[bulk] 选中：角色 {len(sel_chars):,}（top1≥{min_score}）/ "
          f"选单 {len(rows):,} 行（张数=角色数）", flush=True)
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


def _danbooru_pids(meta_dir: str) -> set:
    """images.jsonl 中 bulk_danbooru2023 图的 post id 集合（asset_ids 解析）。"""
    pids = set()
    mpath = os.path.join(meta_dir, "images.jsonl")
    if not os.path.exists(mpath):
        return pids
    with open(mpath, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("source") != DATASETS["danbooru2023"]["source"]:
                continue
            aid = str((rec.get("asset_ids") or {}).get("bulk_danbooru2023") or "")
            if aid.startswith("danbooru2023-"):
                try:
                    pids.add(int(aid.split("-", 1)[1]))
                except ValueError:
                    continue
    return pids


def _candidate_of(profile: dict, row: dict) -> "models.Candidate":
    pid = row["post_id"]
    ext = (row.get("file_ext") or "").lower()
    insts = row.get("instances") or []
    return models.Candidate(
        source=profile["source"],
        source_kind=models.SOURCE_KIND_DATASET,
        asset_id=f"danbooru2023-{pid}",
        instance=insts[0] if insts else (row.get("instance") or ""),
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
        todo[-1]._extra_instances = (r.get("instances") or [])[1:]
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
        if enqueue_annotate is not None:
            # 同 sha 多条（多实例补关联）归并后投一次：队列按首次 instances 定稿
            by_sha = {}
            for d in batch:
                if not d.sha256:
                    continue
                ent = by_sha.setdefault(d.sha256, [set(), d.local_path])
                if d.instance:
                    ent[0].add(d.instance)
            for sha, (inst_set, local) in by_sha.items():
                try:
                    enqueue_annotate(meta_dir, sha, sorted(inst_set),
                                     pipeline._rel_path(local))
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
                for extra in (getattr(c, "_extra_instances", None) or []):
                    c2 = copy.copy(c)
                    c2.instance = extra
                    buf.append(c2)
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
                # 代表作模式多实例映射：额外实例补关联记录（同 sha 主清单并集）
                for extra in (getattr(c, "_extra_instances", None) or []):
                    d2 = copy.copy(d)
                    d2.instance = extra
                    buf.append(d2)
                stats["ok"] += 1
                stats["bytes"] += d.actual_size or 0
                health[profile["source"]]["dl_ok"] += 1
                ok_now, bytes_now = stats["ok"], stats["bytes"]
            if ok_now % 50 == 0:
                print(f"[bulk] 下载 {ok_now}/{len(todo)} "
                      f"({bytes_now/1e6:.0f} MB)", flush=True)
            # 增量 flush 必须在锁外调（_flush 自取 lock）：满 FLUSH_EVERY 即落
            # 主清单+投打标队列，进程中断只丢缓冲残余，断点续跑才有据可查
            _flush()
        else:
            with lock:
                stats["fail"] += 1
                health[profile["source"]][
                    "dl_dead" if d.fail_kind in ("dead_link", "hotlink_forbidden")
                    else "dl_fail"] += 1
                fail_now = stats["fail"]
            if fail_now <= 5:
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
# 原始元数据回捞：已入湖 post 的完整 tag_string 等字段持久落盘
# ---------------------------------------------------------------------------
def recover_meta(meta_dir: str, dataset: str = "danbooru2023",
                 dl_threads: int = 8) -> int:
    """把已入湖图的原始 post 记录从 posts 缓存捞出，持久落 posts_meta.jsonl。

    背景：scan_posts* 流式扫描后 tag_string 即弃，打标/审计再要用只能重扫。
    本函数按 images.jsonl 的 asset_ids 圈定 pid，整记录（含 tag_string/
    tag_string_character/tag_string_copyright/tag_string_artist/score 等）
    逐行 append 进 state/collect/bulk/<dataset>/posts_meta.jsonl；已落 pid
    跳过（中断重跑幂等）。posts.tar.gz 缓存本身亦保留，即原始元数据双保险。
    返回本次新增记录数。"""
    profile = DATASETS[dataset]
    state_dir = _state_dir(meta_dir, dataset)
    out_path = os.path.join(state_dir, "posts_meta.jsonl")
    want = _danbooru_pids(meta_dir)
    if not want:
        print("[bulk] images.jsonl 无 %s 图，无需回捞" % profile["source"],
              flush=True)
        return 0
    got = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    got.add(int(json.loads(line)["id"]))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    todo = want - got
    print(f"[bulk] 回捞目标 {len(want):,} pid / 已落 {len(got):,} / "
          f"待捞 {len(todo):,}", flush=True)
    if not todo:
        return 0
    posts_path = _ensure_posts_cache(profile, state_dir, dl_threads)
    n = n_scan = 0
    t0 = time.time()
    with open(posts_path, "rb") as raw, \
            open(out_path, "a", encoding="utf-8") as out:
        tf = tarfile.open(fileobj=raw, mode="r|gz")
        try:
            for m in tf:
                if not m.isfile() or \
                        os.path.basename(m.name) != profile["posts_member"]:
                    continue
                f = tf.extractfile(m)
                try:
                    for line in f:
                        n_scan += 1
                        # pid 先串前缀快筛，命中才全量解析（千万级行省解析开销；
                        # dump 行形如 {"id":1,...}，id 恒为首字段）
                        if b'"id":' not in line[:16]:
                            continue
                        try:
                            rec = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        pid = rec.get("id")
                        if pid in todo:
                            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                            todo.discard(pid)
                            n += 1
                            if not todo:
                                break
                        if n_scan % 1000000 == 0:
                            print(f"[bulk] 已扫 {n_scan:,} / 捞回 {n:,} / "
                                  f"剩 {len(todo):,} / {time.time()-t0:.0f}s",
                                  flush=True)
                except (OSError, EOFError, tarfile.TarError) as e:
                    print(f"[warn] posts 流提前中断: {e}", flush=True)
                    break
                if not todo:
                    break
        finally:
            tf.close()
    print(f"[bulk] 回捞完成：新增 {n:,} 条 → {out_path}（剩 {len(todo):,} 未命中："
          f"dump 后删档或 pid 异常）/ {time.time()-t0:.0f}s", flush=True)
    return n


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def bulk(meta_dir: str, taxonomy: str, dataset: str, images_dir: str,
         run_id: str, per_instance: int, max_posts: int, workers: int,
         interval: float, jobs_substr=None, dry_run: bool = False,
         rescan: bool = False, dl_threads: int = 8, cache_mb: int = 0,
         min_score: int = 0, min_side: int = 0, by_character: bool = False) -> None:
    profile = DATASETS[dataset]
    state_dir = _state_dir(meta_dir, dataset)
    sel_name = "selected_bychar" if by_character else "selected"
    sel_path = os.path.join(state_dir, sel_name + ".jsonl")
    done_flag = os.path.join(state_dir, sel_name + ".done")

    tag_index = build_tag_index(taxonomy, jobs_substr)
    print(f"[bulk] tag 索引 {len(tag_index):,} 条"
          + (f"（--jobs 过滤: {jobs_substr}）" if jobs_substr else "")
          + ("（代表作模式：词表仅作粗打标映射）" if by_character else ""),
          flush=True)
    if not tag_index and not by_character:
        print("[bulk] tag 索引为空，退出", flush=True)
        return

    if os.path.exists(sel_path) and os.path.exists(done_flag) and not rescan:
        rows = load_selected(sel_path)
        print(f"[bulk] 复用既有选单 {len(rows):,} 条（--rescan 可重扫）", flush=True)
    else:
        if by_character:
            n = scan_posts_by_character(profile, tag_index, sel_path,
                                        state_dir, max_posts, dl_threads,
                                        cache_mb, min_score, min_side)
        else:
            n = scan_posts(profile, tag_index, per_instance, sel_path,
                           state_dir, max_posts, dl_threads, cache_mb,
                           min_score, min_side)
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
            print(f"   {r.get('instance') or '(无体系实例)'} ← {r['matched_tag']} "
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
    ap.add_argument("--min-score", type=int, default=10,
                    help="价值门：投票分下限（默认 10；0=不限）")
    ap.add_argument("--min-side", type=int, default=768,
                    help="价值门：短边像素下限（默认 768，与下载分辨率门同口径；0=不限）")
    ap.add_argument("--interval", type=float, default=0.5,
                    help="下载限速间隔秒（默认 0.5；HF CDN 抗压能力强于搜索 API）")
    ap.add_argument("--jobs", default="",
                    help="实例名子串过滤（逗号分隔，试点用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只扫描匹配，不下载（看命中面）")
    ap.add_argument("--rescan", action="store_true",
                    help="忽略既有选单重新扫描（默认复用 selected.jsonl）")
    ap.add_argument("--by-character", action="store_true",
                    help="代表作模式：不做 instances 匹配，按角色 tag 分组每角色取 "
                         "top1（选单 selected_bychar.jsonl；配合 --min-score/--min-side）")
    ap.add_argument("--recover-meta", action="store_true",
                    help="只回捞已入湖 post 的原始元数据（tag_string 等）持久落 "
                         "posts_meta.jsonl，不扫描选单不下载")
    args = ap.parse_args(argv)

    if args.recover_meta:
        recover_meta(args.meta, args.dataset, args.dl_threads)
        return

    run_id = time.strftime("bulk_%Y%m%d_%H%M%S")
    bulk(args.meta, args.taxonomy, args.dataset, args.images_dir, run_id,
         per_instance=args.per_instance, max_posts=args.max_posts,
         workers=args.workers, interval=args.interval,
         jobs_substr=[t for t in args.jobs.split(",") if t],
         dry_run=args.dry_run, rescan=args.rescan,
         dl_threads=args.dl_threads, cache_mb=args.cache_mb,
         min_score=args.min_score, min_side=args.min_side,
         by_character=args.by_character)


if __name__ == "__main__":
    main()
