#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search_kb_supervise.py — search agent 全量跑监督器（2026-08-29）。

为什么存在：本容器内单进程高并发（workers>=24）实测会被宿主侧无声 SIGKILL 或
事件循环僵死（py-spy 栈：loop idle、全部 await 永不唤醒；与 Python 3.10/3.14
无关、非 cgroup OOM）。管线本身幂等（done 过滤 + 证据缓存），所以正确姿势是
collect_v2/supervise.py 同款：**切片循环**——每次起一个子进程跑 --limit N 个
实体、干净退出（产物全部 flush、源账本落盘），子进程死透就再来一片。进程级
崩溃被降级为「损失在途数实体，下一片重跑」。

用法（全量 296k，池先行尾量殿后）：
    setsid nohup /tank/demiwtg/.venv/bin/python -u data/taxonomy/search_kb_supervise.py \
        --all --batch full --workers 16 --glm-concurrency 12 \
        --slice 300 --one-shot --planner-scope pool --serp-threshold 3 \
        --max-attempts 4 \
        >> state/taxonomy/search_kb/supervise.log 2>&1 &

只做循环与计数，不复制管线参数语义；search_kb.py 的参数原样透传（--slice 转成
每片的 --limit）。停止：kill 监督器 pid（子进程当前片跑完自然收；两代都杀则
kill 监督器与子进程两个 pid）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent.parent
PY = sys.executable
KB = str(ROOT / "data" / "taxonomy" / "search_kb.py")
CARDS = ROOT / "state" / "taxonomy" / "search_kb" / "cards.jsonl"

PASSTHROUGH = ("--batch", "--workers", "--glm-concurrency", "--one-shot",
               "--planner-scope", "--serp-threshold", "--max-attempts",
               "--domains", "--only-empty", "--max-tokens", "--all",
               "--no-planner")   # 文档性清单；实际透传见 main() 的剥参逻辑


def terminal_counts(max_attempts: int) -> tuple:
    """(终态实体数, 状态分布, retry 态数)——直接复用管线的终态判定。"""
    from collect_v2.search_kb import load_done_entities, MAX_ATTEMPTS
    done = load_done_entities(max_attempts)
    st = Counter(v[0] for v in done.values())
    return len(done), st, st.get("retry", 0)


def main():
    ap = argparse.ArgumentParser(description="search_kb 切片监督器")
    ap.add_argument("--slice", type=int, default=300, help="每片实体数（转 --limit）")
    ap.add_argument("--max-rounds", type=int, default=2000, help="最大片数（保险丝）")
    ap.add_argument("--stall-rounds", type=int, default=3,
                    help="连续 N 片零进展即收工（防死循环）")
    args, extra = ap.parse_known_args()
    # 透传：剥掉监督器自身参数（--slice/--max-rounds/--stall-rounds 及其值），
    # 其余 argv 原样传给 search_kb.py（空格分隔的值不会被白名单误伤）
    own_flags = {"--slice", "--max-rounds", "--stall-rounds"}
    passthrough, skip_next = [], False
    for a in extra:
        if skip_next:
            skip_next = False
            continue
        if a in own_flags:
            skip_next = True
            continue
        if any(a.startswith(f + "=") for f in own_flags):
            continue
        passthrough.append(a)

    max_attempts = 4
    for i, a in enumerate(passthrough):
        if a.startswith("--max-attempts"):
            if "=" in a:
                max_attempts = int(a.split("=", 1)[1])
            elif i + 1 < len(passthrough):
                max_attempts = int(passthrough[i + 1])

    n0, st0, _ = terminal_counts(max_attempts)
    print(f"[supervise] 起点：终态 {n0} 实体 {dict(st0)}", flush=True)
    stall = 0
    for rnd in range(1, args.max_rounds + 1):
        cmd = [PY, "-u", KB, "--limit", str(args.slice)] + passthrough
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(ROOT))
        dt = time.time() - t0
        n1, st1, retry = terminal_counts(max_attempts)
        delta = n1 - n0 - (st1.get("retry", 0) - st0.get("retry", 0))
        print(f"[supervise] 第 {rnd} 片：exit={p.returncode} 用时 {dt/60:.1f} 分 | "
              f"终态 +{delta} → {n1} | 状态 {dict(st1)} | retry {retry}",
              flush=True)
        if delta <= 0:
            stall += 1
            if stall >= args.stall_rounds and retry == 0:
                print("[supervise] 连续零进展且无 retry 态：收工。", flush=True)
                break
            if stall >= args.stall_rounds * 3:
                print("[supervise] 连续多片零进展（有 retry 卡死）：收工，人工介入。",
                      flush=True)
                break
        else:
            stall = 0
        n0, st0 = n1, st1
        time.sleep(3)
    n, st, _ = terminal_counts(max_attempts)
    print(f"[supervise] 结束：终态 {n} | {dict(st)}", flush=True)


if __name__ == "__main__":
    main()
