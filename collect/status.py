"""采集运行状态一次性看板（只读，可安全对正在运行的批次反复执行）。

用法：python3 collect/status.py [--watch N]   # --watch N 每 N 秒刷新一次

汇总五层观测点：
1. 进程：collect/cli 存活分片数（按 run 前缀分组）
2. 主清单：images.jsonl 行数（与上次查看的差值）
3. 阶段一：state/collect/runs/<run_id>/candidates.jsonl 候选累积
4. 阶段二：.dlq_<queue_id>.sqlite3 队列状态（pending/claimed/done/skipped 按源汇总）
5. 源健康：source_health.json 里最近活跃的源
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(_REPO, "data", "dataset", "meta", "images.jsonl")
STATE = os.path.join(_REPO, "state", "collect")
RUNS = os.path.join(STATE, "runs")
LOGS = os.path.join(_REPO, "logs")
HEALTH = os.path.join(STATE, "source_health.json")
_BASELINE = os.path.join(STATE, ".status_baseline")


def _line_count(path: str) -> int:
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


def _proc_groups() -> dict:
    """ps 出 collect/cli 进程，按 run-id 前缀（去 _sN 尾缀）分组计数。"""
    try:
        out = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    groups: dict = {}
    for line in out.splitlines():
        if "collect/cli.py" not in line or "status.py" in line:
            continue
        m = re.search(r"--run-id\s+(\S+)", line)
        rid = m.group(1) if m else "?"
        prefix = re.sub(r"_s\d+$", "", rid)
        groups[prefix] = groups.get(prefix, 0) + 1
    return groups


def _shard_log_progress(rid: str) -> tuple:
    """从分片日志解析 (执行任务总数, 阶段一已处理数, 是否分片启动)；
    日志缺失返 (None, None, False)。非分片启动的日志无 [shard] 行，
    其任务数是未切分的全量，同批多进程会重复计。
    注：阶段一进度 print 无 flush，日志可能滞后（缓冲未满未刷盘）。"""
    total = done = None
    try:
        with open(os.path.join(LOGS, f"{rid}.log"), encoding="utf-8",
                  errors="replace") as f:
            text = f.read()
    except OSError:
        return None, None, False
    sharded = "[shard]" in text
    m = re.findall(r"-> 执行 (\d+)", text)
    if m:
        total = int(m[-1])
    m = re.findall(r"\[阶段一\] 进度 (\d+)/(\d+) 任务", text)
    if m:
        done = int(m[-1][0])
    if re.search(r"\[阶段一\] 检索完成", text):
        done = total  # 已完成阶段一
    return total, done, sharded


_SRC_PAT = re.compile(rb'"source":\s*"([^"]+)"')
_INST_PAT = re.compile(rb'"instance":\s*"([^"]*)"')


def _phase1_stats(groups: dict) -> dict:
    """按 run 前缀汇总各分片：候选数 + 按源分布 + 实例粒度进展。
    只统计进程仍存活的批次（groups 来自 _proc_groups）；任务进展取
    分片日志「阶段一 进度 i/N」，非分片重启（日志显示全量任务数）
    按重复次数去重。"""
    cutoff = time.time() - 6 * 3600
    files = []  # (prefix, rid, path)
    for f in glob.glob(os.path.join(RUNS, "*", "candidates.jsonl")):
        if os.path.islink(os.path.dirname(f)):
            continue  # _latest 软链，不重复计
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        rid = os.path.basename(os.path.dirname(f))
        prefix = re.sub(r"_s\d+$", "", rid)
        if prefix.startswith("smoke") or prefix not in groups:
            continue  # 冒烟/已收工批次不上看板
        files.append((prefix, rid, f))

    acc: dict = {}
    for prefix, rid, f in files:
        s = acc.setdefault(prefix, {"shards": 0, "cands": 0,
                                    "total": 0, "done": 0, "srcs": {},
                                    "n_unsharded": 0,
                                    "hit_inst": set(), "log_ok": True})
        total, done, sharded = _shard_log_progress(rid)
        if total is None:
            s["log_ok"] = False
        else:
            s["total"] += total
            s["done"] += done or 0
            if not sharded:
                s["n_unsharded"] += 1

    # 非分片启动（--out 复用候选）时每个进程日志都是全量任务数，去重
    for prefix, s in acc.items():
        if s["log_ok"] and s["n_unsharded"] > 1:
            s["total"] //= s["n_unsharded"]
            s["done"] //= s["n_unsharded"]

    # 候选文件单次扫描：计行 + 按源分布（+ 日志缺失时抽实例下限）
    for prefix, rid, f in files:
        s = acc[prefix]
        s["shards"] += 1
        need_inst = not s["log_ok"]
        try:
            with open(f, "rb") as fh:
                for line in fh:
                    s["cands"] += 1
                    m = _SRC_PAT.search(line)
                    if m:
                        src = m.group(1).decode()
                        s["srcs"][src] = s["srcs"].get(src, 0) + 1
                    if need_inst:
                        m = _INST_PAT.search(line)
                        if m:
                            s["hit_inst"].add(m.group(1))
        except OSError:
            pass
    return acc


def _phase2_stats() -> dict:
    """读近 6h 活跃的 .dlq_*.sqlite3（只读模式），按状态/源汇总。"""
    out = {}
    cutoff = time.time() - 6 * 3600
    for db in glob.glob(os.path.join(STATE, ".dlq_*.sqlite3")):
        try:
            if os.path.getmtime(db) < cutoff:
                continue
        except OSError:
            continue
        qid = os.path.basename(db)[len(".dlq_"):-len(".sqlite3")]
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM items GROUP BY status").fetchall()
            by_src = conn.execute(
                "SELECT source, SUM(status='done'), SUM(status='claimed'), "
                "SUM(status='skipped'), COUNT(*) FROM items "
                "GROUP BY source ORDER BY 5 DESC").fetchall()
            conn.close()
            out[qid] = {"status": dict(rows),
                        "by_src": [(s, d or 0, c or 0, k or 0, t)
                                   for s, d, c, k, t in by_src]}
        except sqlite3.Error as e:
            out[qid] = {"error": str(e)[:80]}
    return out


def _health_recent(days: int = 1) -> list:
    try:
        with open(HEALTH, encoding="utf-8") as f:
            h = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    cutoff = time.time() - days * 86400
    rows = []
    for name, st in (h.get("sources") or h).items():
        if not isinstance(st, dict):
            continue
        ts = st.get("updated_at") or 0
        if ts >= cutoff:
            rows.append((name, st.get("dl_ok", 0), st.get("dl_fail", 0)
                         + st.get("dl_dead", 0) + st.get("dl_timeout", 0), ts))
    rows.sort(key=lambda r: -r[3])
    return rows[:10]


def render(prev_manifest: int) -> int:
    print("=" * 68)
    print(f"采集状态  {time.strftime('%m-%d %H:%M:%S')}")
    # 1) 进程
    groups = _proc_groups()
    total = sum(groups.values())
    procs = ", ".join(f"{k}×{v}" for k, v in sorted(groups.items())) or "无"
    print(f"[进程] {total} 个分片存活: {procs}")
    # 2) 主清单
    try:
        n = _line_count(META)
        delta = f"（本轮 +{n - prev_manifest}）" if prev_manifest else ""
        print(f"[主清单] images.jsonl {n} 行{delta}")
    except OSError:
        pass
    # 3) 阶段二先算好，供阶段一按源行交叉引用 done
    q2 = _phase2_stats()
    # 4) 阶段一（实例粒度进展：日志有进度行用日志，否则给命中实例数下限）
    for prefix, s in sorted(_phase1_stats(groups).items()):
        head = f"[阶段一] {prefix}: {s['shards']} 片"
        if s["log_ok"] and s["total"]:
            pct = s["done"] / s["total"] * 100 if s["total"] else 0
            print(f"{head} | 任务 {s['done']:,}/{s['total']:,} ({pct:.0f}%) "
                  f"| 候选 {s['cands']:,} 条")
        else:
            print(f"{head} | 候选 {s['cands']:,} 条 "
                  f"| 已命中实例 ≥{len(s['hit_inst']):,}（日志未刷盘，仅下限）")
        top = sorted(s["srcs"].items(), key=lambda kv: -kv[1])[:6]
        if top:
            # 同前缀下载队列的按源 done（投递入队≠候选数，仅交叉参考）
            dmap = {src: d for src, d, _, _, _ in
                    q2.get(prefix, {}).get("by_src", [])}
            dist = " ".join(
                f"{n} {v:,}({v / s['cands'] * 100:.0f}%)"
                + (f" done {dmap[n]:,}" if n in dmap else "")
                for n, v in top)
            print(f"    候选按源: {dist}")
    # 5) 阶段二
    if not q2:
        print("[阶段二] 下载队列尚未创建（分片都还在阶段一）")
    for qid, s in sorted(q2.items()):
        if "error" in s:
            print(f"[阶段二] {qid}: 读取失败 {s['error']}")
            continue
        st = s["status"]
        done = st.get("done", 0)
        tot = sum(st.values())
        pct = done / tot * 100 if tot else 0
        print(f"[阶段二] {qid}: 候选 {tot:,} | done {done:,} ({pct:.1f}%) "
              f"/ claimed {st.get('claimed', 0):,} "
              f"/ pending {st.get('pending', 0):,} / skipped {st.get('skipped', 0):,}")
        # 按源进展：done/total（取候选量前 8 源，有动静的全显示）
        for src, d, c, k, t in s["by_src"][:8]:
            extra = ""
            if c:
                extra += f" 进行中 {c:,}"
            if k:
                extra += f" 跳过 {k:,}"
            print(f"    {src}: done {d:,}/{t:,}{extra}")
    # 6) 源健康（近 24h）
    recent = _health_recent()
    if recent:
        top = ", ".join(f"{n}({ok}成/{fa}败)" for n, ok, fa, _ in recent[:6])
        print(f"[源健康24h] {top}")
    print("=" * 68)
    return _line_count(META) if os.path.exists(META) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", type=int, default=0,
                    help="每 N 秒刷新（0=只打印一次）")
    args = ap.parse_args(argv)

    prev = 0
    if os.path.exists(_BASELINE):
        try:
            prev = int(open(_BASELINE).read().strip())
        except ValueError:
            prev = 0
    else:
        # 首次运行记下基线，供后续看增量
        try:
            prev = _line_count(META)
            with open(_BASELINE, "w") as f:
                f.write(str(prev))
        except OSError:
            pass

    cur = render(prev)
    while args.watch > 0:
        time.sleep(args.watch)
        cur = render(cur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
