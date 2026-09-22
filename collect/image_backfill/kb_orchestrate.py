#!/usr/bin/env python3
"""kb 批次采集编排入口（2026-09-18）—— demiflow 编排示范。

分层关系（用户拍板架构）：
- demiflow（框架仓）      ：collect/exec_curl.py 短命 curl 传输 + AIMD 节拍
                            + 出口身份；collect/fleet.py systemd-run 发射/巡检
- demiwtg/collect（项目仓）：本文件 = 批次控制面，只做三件事——
                            ① 组装 worker 命令（kb_backfill 负载）
                            ② 委托 demiflow fleet 发射/停止/巡检
                            ③ 任务清单与身份表的项目侧分发

用法：
  PYTHONPATH=/yzp/zhaozy/yangzepeng/0905/demiflow python3 kb_orchestrate.py \
      --tasks-key <cos清单key> --workers 81 [--rps-start 0.25] deploy
  ... kb_orchestrate.py --manifest-glob 'run_kb_*/manifest.jsonl' patrol
  ... kb_orchestrate.py stop
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, "/yzp/zhaozy/yangzepeng/0905/demiflow")
from demiflow.collect.fleet import (FleetConfig, deploy_fleet, patrol_fleet,
                                    stop_fleet, worker_plan)
from demiflow.collect.exec_curl import EgressIdentity

HUB = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/checkpoints/hub"
HERE = os.path.dirname(os.path.abspath(__file__))
COS_PREFIX = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs"


def make_cfg() -> FleetConfig:
    proxies = [l.strip() for l in open(f"{HUB}/proxies/master_final.txt") if l.strip()]
    return FleetConfig(hosts=[f"r{i}" for i in range(1, 21)],
                       proxy_specs=proxies, workdir="~/wk_backfill",
                       unit_prefix="kbw")


def tag_of(tasks_key: str) -> str:
    b = os.path.basename(tasks_key)
    for suf in (".jsonl.gz", ".jsonl"):
        if b.endswith(suf):
            return b[: -len(suf)]
    return b


def ensure_machine_deps(cfg: FleetConfig, identity: EgressIdentity, tasks_key: str):
    """依赖/清单/身份下发（幂等）：scp 工具 + 拉清单 + 写 ua.env。"""
    from demiflow.collect.fleet import sh
    import subprocess
    tag = tag_of(tasks_key)
    files = " ".join(f"{HERE}/{f}" for f in
                     ("kb_backfill.py", "fleet_curl.py", "cos_util.py", ".cos_creds"))
    for h in cfg.hosts:
        subprocess.run(["timeout", "120", "scp", "-q", "-o", "ConnectTimeout=15",
                        *files, f"{HUB}/ua_pool.txt", f"{HUB}/ua_assign.tsv",
                        f"{h}:~/wk_backfill/"], capture_output=True)
        sh(h, "chmod 600 ~/wk_backfill/.cos_creds")
        # ua.env：本机唯一直连身份（供 kb_backfill 默认取）
        sh(h, f"awk -F'\\t' -v k=host:{h} '$1==k{{print \"UA=\" $2}}' "
              f"~/wk_backfill/ua_assign.tsv > ~/wk_backfill/ua.env")
        r = sh(h, f"test -s ~/wk_backfill/tasks_{tag}.jsonl.gz && echo exist", 30)
        if "exist" not in r.stdout:
            sh(h, "cd ~/wk_backfill && python3 -c \""
                  "import cos_util, urllib.request\n"
                  f"k='{tasks_key}'\n"
                  "sid,sk=cos_util.creds()\n"
                  "a=cos_util._sig('GET','/'+k,{},sid,sk)\n"
                  "r=urllib.request.Request('https://'+cos_util.HOST+'/'+k,"
                  "headers={'authorization':a})\n"
                  f"open('tasks_{tag}.jsonl.gz','wb').write("
                  "urllib.request.urlopen(r,timeout=800).read())\"", 900)
        print(f"{h} deps+tasks ready", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["deploy", "patrol", "stop"])
    ap.add_argument("--tasks-key", default="")
    ap.add_argument("--workers", type=int, default=81)
    ap.add_argument("--nw", type=int, default=81, help="分片总数 I/N 的 N")
    ap.add_argument("--rps-start", default="0.25")
    ap.add_argument("--manifest-glob", default="run_kb_*/manifest.jsonl")
    args = ap.parse_args()
    cfg = make_cfg()
    ident = EgressIdentity(f"{HUB}/ua_assign.tsv", f"{HUB}/ua_pool.txt")

    if args.action == "deploy":
        assert args.tasks_key, "--tasks-key 必填"
        tag = tag_of(args.tasks_key)
        ensure_machine_deps(cfg, ident, args.tasks_key)

        def payload(item):
            ua = ident.pick(item["proxy"]) or ""
            # systemd-run + bash -c 单引号包裹：命令内不得出现单引号
            parts = [f"exec python3 -u kb_backfill.py",
                     f"--tasks tasks_{tag}.jsonl.gz",
                     f"--shard {item['w']}/{args.nw}",
                     f"--manifest run_kb_{tag}_w{item['w']}/manifest.jsonl"]
            if item["proxy"]:
                parts.append(f"--proxy {item['proxy']}")
                if ua:
                    parts.append(f'--ua "{ua}"')
            parts += [f"--rps-start {args.rps_start}", "--hard-cap-mb 64"]
            return " ".join(parts)

        plan = worker_plan(args.workers, cfg)
        for r in deploy_fleet(plan, payload, cfg):
            print(r, flush=True)
    elif args.action == "patrol":
        import json
        st = patrol_fleet(cfg, args.manifest_glob)
        print(json.dumps({k: v for k, v in st.items() if k != "per_host"},
                         ensure_ascii=False))
        bad = {h: v for h, v in st["per_host"].items() if v["units"] == 0}
        if bad:
            print("无存活 unit 的机器:", ",".join(bad), flush=True)
    elif args.action == "stop":
        stop_fleet(cfg)


if __name__ == "__main__":
    main()
