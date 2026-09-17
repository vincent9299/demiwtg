"""thumb1200 存量重收(2026-09-17): 账本 tier=thumb1200 行按 (qid,
commons_file) 原图重取, 写独立账本(同 schema, tier=orig), 断点续跑。

背景: 旧守门对原图 >10MB 者存 1200px 缩略图, 口径已废除; 本工具把
存量 34 万行重收为原图。复用修复后的 flow_images_batch 组件(Fetcher/
Sink, 已带 200 校验+429 退避+HTML 兜底)。

用法(fleet 分片同 flow_images_batch):
  PYTHONPATH=<仓库根> python3 backfill_orig.py \
    --ledger /lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz \
    --blobs-root /lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs \
    --manifest qid_images-orig-backfill-shard-i.jsonl --shard i/N

注意:
- 目标行原图均 >10MB; --hard-cap-mb 调封顶(默认 64, 内存上界 =
  dl_conc × cap, 调大前先算内存); 超封顶者计 miss_dl, 巨物级全景图
  (数百 MB)如需收需另行磁盘流式改造。
- 旧 thumb blob 重收后成无主引用, 清理需按账本引用计数, 勿直接删。
- 收完的并账/替换旧行由 merge 侧处理(保留 tier=orig 新行)。
"""
import argparse
import asyncio
import gzip
import json

from flow_images_batch import BATCH, Fetcher, Sink, load_done


def iter_ledger_tasks(ledger: str, tiers: set, done: set,
                      shard_i: int, shard_n: int):
    """账本流 → (qid, commons_file) 任务行: 命中 tier、去重、分片、跳已收。"""
    seen = set()
    idx = 0
    op = "rb" if ledger.endswith(".gz") else "r"
    opener = gzip.open if op == "rb" else open
    with opener(ledger, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("tier") not in tiers:
                continue
            key = (r["qid"], r["commons_file"])
            if key in seen:
                continue
            seen.add(key)
            if idx % shard_n == shard_i and key not in done:
                yield {"qid": r["qid"], "commons_file": r["commons_file"]}
            idx += 1


def iter_file_tasks(tasks_path: str, done: set,
                    shard_i: int, shard_n: int):
    """任务文件(jsonl[.gz], 行含 qid+commons_file) → 任务行; 审计毒行重收用。"""
    seen = set()
    idx = 0
    op = "rb" if tasks_path.endswith(".gz") else "r"
    opener = gzip.open if op == "rb" else open
    with opener(tasks_path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (r["qid"], r["commons_file"])
            if key in seen:
                continue
            seen.add(key)
            if idx % shard_n == shard_i and key not in done:
                yield {"qid": r["qid"], "commons_file": r["commons_file"]}
            idx += 1


async def run(args):
    import time
    global_hard_cap = args.hard_cap_mb << 20
    import flow_images_batch as fb
    fb.HARD_CAP_BYTES = global_hard_cap
    done = load_done(args.manifest)
    fetcher = Fetcher(args)
    sink = Sink(args.blobs_root, args.manifest)
    t0 = time.time()
    hit_q: asyncio.Queue = asyncio.Queue(maxsize=64)

    def progress():
        el = time.time() - t0
        print(f"[backfill] sunk={fetcher.sunk:,} ({fetcher.sunk/el:.2f}/s) "
              f"miss_meta={fetcher.miss_meta:,} miss_dl={fetcher.miss_dl:,} "
              f"miss_html={fetcher.miss_html:,}", flush=True)

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
                print(f"[backfill] sink 异常 {type(e).__name__}: "
                      f"{str(e)[:80]}", flush=True)
                continue
            fetcher.sunk += 1
            if fetcher.sunk % 100 == 0:
                progress()

    workers = [asyncio.create_task(dl_worker()) for _ in range(args.dl_conc)]
    batches, buf = 0, []
    it = (iter_file_tasks(args.tasks_file, done, args.shard_i, args.shard_n)
          if args.tasks_file else
          iter_ledger_tasks(args.ledger, args.tiers, done,
                            args.shard_i, args.shard_n))
    for task in it:
        buf.append(task)
        if len(buf) >= BATCH:
            try:
                infos = await fetcher.meta_batch(
                    [r["commons_file"] for r in buf])
                for r in buf:
                    info = infos.get(
                        (r["commons_file"].strip().lower().replace("_", " ")))
                    if info is None:
                        fetcher.miss_meta += 1
                    else:
                        await hit_q.put((r, info))
            except Exception as e:
                print(f"[backfill] meta 弃批({len(buf)} 题): "
                      f"{type(e).__name__} {str(e)[:100]}", flush=True)
            batches += 1
            buf = []
    if buf:
        try:
            infos = await fetcher.meta_batch(
                [r["commons_file"] for r in buf])
            for r in buf:
                info = infos.get(
                    (r["commons_file"].strip().lower().replace("_", " ")))
                if info is None:
                    fetcher.miss_meta += 1
                else:
                    await hit_q.put((r, info))
        except Exception as e:
            print(f"[backfill] meta 弃批(尾批): {type(e).__name__}", flush=True)
    for _ in workers:
        await hit_q.put(None)
    await asyncio.gather(*workers)
    await fetcher.aclose()
    progress()
    print("[backfill] 完成", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ledger", default="",
                   help="合并账本 qid_images.jsonl[.gz](按 --tier 筛时必填)")
    p.add_argument("--tasks-file", default="",
                   help="直喂任务文件 jsonl[.gz](行含 qid+commons_file; "
                        "审计毒行/自定义清单重收用, 优先于 --ledger)")
    p.add_argument("--blobs-root", required=True, help="COS kb/blobs 池根")
    p.add_argument("--manifest", required=True, help="重收账本 jsonl(断点续跑)")
    p.add_argument("--shard", required=True, metavar="I/N")
    p.add_argument("--tier", default="thumb1200",
                   help="重收的 tier 集合(逗号分隔; 默认 thumb1200)")
    p.add_argument("--api-rate", type=float, default=2.0)
    p.add_argument("--dl-rate", type=float, default=2.0)
    p.add_argument("--dl-conc", type=int, default=4)
    p.add_argument("--hard-cap-mb", type=int, default=64,
                   help="单图字节封顶(MB); 内存上界 ≈ dl_conc × cap")
    args = p.parse_args()
    i, n = (int(x) for x in args.shard.split("/"))
    args.shard_i, args.shard_n = i, n
    args.tiers = {t.strip() for t in args.tier.split(",") if t.strip()}
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
