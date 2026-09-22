#!/usr/bin/env python3
"""B2 慢行看门狗（湖侧常驻）：TransientFetchError 毒行自动摘除 + 超龄认领回收。

每轮：
1. fleet 扫 b2_{si,inat,met,oi}.log 的 TransientFetchError 行 → 唯一 extid 集；
2. 新毒行（未处理过的）定位所在批（SI 用清单顺序重放缓存映射；met/oi 小队列直接扫批）；
3. 从 COS 批文件摘行（写 pending_slow_rows.jsonl 追加档）；
4. queue-b2-{si,inat,met} requeue_stale(2h) 回收死 worker 认领。
日志：/tmp/b2_watchdog.log；幂等，可安全重启。
"""
import gzip
import io as _io
import json
import subprocess
import sys
import time

sys.path.insert(0, '/yzp/zhaozy/yangzepeng/0905/demiflow')
from demiflow.collect.cosio import COSCreds, COSIO, build_host
from demiflow.collect.cosqueue import COSQueue

LISTS = '/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch2/lists'
HOSTS = [f'r{i}' for i in range(1, 21)]
INTERVAL = 300
io = COSIO(COSCreds.from_file(
    '/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/.cos_creds'),
    build_host('lhcos-368f6-1256345599', 'ap-singapore'))

_done_poisons = set()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build_media_index():
    """SI 清单首现序 → 批号映射（一次构建，常驻缓存）。"""
    t0 = time.time()
    idx, n = {}, 0
    with gzip.open(f'{LISTS}/fetch_smithsonian.tsv.gz', 'rt') as f:
        seen = set()
        for line in f:
            p = line.rstrip('\n').split('\t')
            if len(p) < 7:
                continue
            m = p[0]
            if m in seen:
                continue
            seen.add(m)
            idx[m] = n // 2000
            n += 1
    log(f"SI media→批映射 {len(idx)} 条 ({time.time()-t0:.0f}s)")
    return idx


def scan_errors():
    extids = set()
    for h in HOSTS:
        r = subprocess.run(
            ['timeout', '20', 'ssh', '-o', 'ConnectTimeout=8', '-o', 'BatchMode=yes',
             h, 'grep -h TransientFetchError ~/wk_b2/b2_si.log ~/wk_b2/b2_inat.log '
                '~/wk_b2/b2_met.log 2>/dev/null'],
            capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            e = line.split('TransientFetchError: ')[1].split(':')[0].strip()
            extids.add(e)
    return extids


def strip_si(media_index, extids):
    global _done_poisons
    todo = {e for e in extids if e.startswith('media:')} - _done_poisons
    if not todo:
        return 0
    Q = 'lhcos-data/demiwtg-data/queue-b2-si'
    by_bid = {}
    for e in todo:
        b = media_index.get(e)
        if b is not None:
            by_bid.setdefault(b, []).append(e)
    removed = 0
    for b, evils in sorted(by_bid.items()):
        key = f'{Q}/batches/b{b:06d}.jsonl.gz'
        raw = io.get_bytes(key)
        if raw is None:
            continue
        evil_set, kept = set(evils), []
        with gzip.GzipFile(fileobj=_io.BytesIO(raw)) as gz:
            for line in _io.TextIOWrapper(gz, encoding='utf-8'):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('extid') in evil_set:
                    removed += 1
                    with open('/tmp/pending_slow_append.jsonl', 'a') as f:
                        f.write(json.dumps(r, ensure_ascii=False) + '\n')
                else:
                    kept.append(line)
        buf = _io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
            gz.write(('\n'.join(kept) + '\n').encode())
        io.put_bytes(key, buf.getvalue())
    _done_poisons |= todo
    return removed


def strip_small_queues(extids):
    """met/oi 小队列：直接扫受影响批文件。"""
    global _done_poisons
    n = 0
    for src, qpre in [('met', 'queue-b2-met'), ('oi', 'queue-b2-oi')]:
        todo = {e for e in extids if not e.startswith('media:')} - _done_poisons
        if not todo or src not in ('met',):
            continue
        Q = f'lhcos-data/demiwtg-data/{qpre}'
        for k in io.list_prefix(f'{Q}/batches/'):
            raw = io.get_bytes(k)
            if raw is None:
                continue
            kept, hit = [], False
            with gzip.GzipFile(fileobj=_io.BytesIO(raw)) as gz:
                for line in _io.TextIOWrapper(gz, encoding='utf-8'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get('extid') in todo:
                        hit = True
                        n += 1
                        with open('/tmp/pending_slow_append.jsonl', 'a') as f:
                            f.write(json.dumps(r, ensure_ascii=False) + '\n')
                    else:
                        kept.append(line)
            if hit:
                buf = _io.BytesIO()
                with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
                    gz.write(('\n'.join(kept) + '\n').encode())
                io.put_bytes(k, buf.getvalue())
        _done_poisons |= todo
    return n


def flush_pending():
    try:
        rows = open('/tmp/pending_slow_append.jsonl').read()
        if rows.strip():
            prev = io.get_bytes('lhcos-data/demiwtg-data/queue-b2-si/pending_slow_rows.jsonl') or b''
            io.put_bytes('lhcos-data/demiwtg-data/queue-b2-si/pending_slow_rows.jsonl',
                         prev + rows.encode())
            open('/tmp/pending_slow_append.jsonl', 'w').close()
            log("pending_slow 已归档")
    except FileNotFoundError:
        pass


def main():
    log("看门狗启动")
    media_index = build_media_index()
    while True:
        extids = scan_errors()
        new = extids - _done_poisons
        n1 = strip_si(media_index, extids)
        n2 = strip_small_queues(extids)
        if new:
            log(f"新毒行 {len(new)}，摘除 {n1 + n2} 行")
        flush_pending()
        for src in ['si', 'inat', 'met']:
            q = COSQueue(io, f'lhcos-data/demiwtg-data/queue-b2-{src}')
            stale = q.requeue_stale(max_age_s=2 * 3600)
            if stale:
                log(f"{src} 回收超龄认领 {len(stale)}")
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
