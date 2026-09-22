#!/usr/bin/env python3
"""第 1 批队列护航：快照 + 假完成审计回收 + 超龄认领回收（cron 周期跑）。

自愈三件事（全部幂等，空跑零副作用）：
1. snapshot：done/claimed/todo 计数 + 完成率；
2. done 标记 rc 审计：rc≠0 且无 rc=0 兜底的批 → 删 done+claims 回队
   （19 台旧内存代码 worker 仍可能在崩溃时写假 done——已知风险，靠此守卫兜底）；
3. requeue_stale：认领超 2h 未完成（worker 崩溃残留）→ 回队。

退出码：0=正常在跑/空跑；3=队列已排空（收官信号）。
"""
import json
import sys
import concurrent.futures as cf
from collections import Counter

sys.path.insert(0, '/yzp/zhaozy/yangzepeng/0905/demiflow')
from demiflow.collect.cosio import COSCreds, COSIO, build_host
from demiflow.collect.cosqueue import COSQueue, _bid_of

QUEUES = [('lhcos-data/demiwtg-data/queue', 947),
           ('lhcos-data/demiwtg-data/queue-b2-oi', 1445),
           ('lhcos-data/demiwtg-data/queue-b2-met', 29)]


def main():
    creds = COSCreds.from_file('/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds')
    io = COSIO(creds, build_host('lhcos-368f6-1256345599', 'ap-singapore'))
    rc = 0
    for QUEUE, TOTAL in QUEUES:
        rc = max(rc, guard_one(io, QUEUE, TOTAL))
    return rc


def guard_one(io, QUEUE, TOTAL):
    q = COSQueue(io, QUEUE)

    snap = q.snapshot()
    inflight = len(snap.claimed - snap.done)
    print(f"[guard:{QUEUE.rsplit('/',1)[-1]}] done={len(snap.done)}/{TOTAL} 在途={inflight} todo={len(snap.todo)}")

    # 假完成审计（读全部 done 标记体）
    done_keys = io.list_prefix(f'{QUEUE}/done/')

    def rd(k):
        try:
            return k, json.loads(io.call('GET', k)[2]).get('rc')
        except Exception:
            return k, 'ERR'
    with cf.ThreadPoolExecutor(12) as ex:
        results = list(ex.map(rd, done_keys))
    dist = Counter(rc for _, rc in results)
    print(f"[guard] done rc 分布: {dict(dist)}")

    healthy = {_bid_of(k) for k, rc in results if rc == 0}
    bad_keys = [k for k, rc in results if rc != 0 and _bid_of(k) not in healthy]
    bad_bids = {_bid_of(k) for k in bad_keys}
    if bad_bids:
        claims = [c for c in io.list_prefix(f'{QUEUE}/claims/')
                  if _bid_of(c) in bad_bids]
        for k in bad_keys + claims:
            io.delete(k)
        print(f"[guard] !! 回收假完成 {len(bad_bids)} 批（rc≠0），已回队")
        snap = q.snapshot()

    stale = q.requeue_stale(max_age_s=2 * 3600)
    if stale:
        print(f"[guard] 回收超龄认领 {len(stale)} 批: {sorted(stale)[:5]}...")

    inflight = len(snap.claimed - snap.done)
    if not snap.todo and inflight == 0 and len(snap.done) >= TOTAL:
        print("[guard] === 第 1 批队列已排空（收官）===")
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
