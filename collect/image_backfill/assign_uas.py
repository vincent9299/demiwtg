#!/usr/bin/env python3
"""诚实 UA 中央分配：每个出口（代理 IP / r 机直连）绑定唯一清单条目。

单一事实来源：hub/ua_pool.txt（80 条）→ hub/ua_assign.tsv
行格式：key<TAB>UA，key 为 proxy:<ip> 或 host:<rN>。
数量关系：61 代理 + 20 台 r 机 = 81 = 池长，一一对应；
若池长不足则报错退出（不允许静默重复）。
部署器与 fleet_curl/fleet_dl 默认值都查这张表，改分配只需重跑本脚本
并重新 scp ua_assign.tsv 到 r 机。"""
import os
import sys

HUB = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/checkpoints/hub"
R_HOSTS = [f"m{i}" for i in range(1, 6)]   # 第三批轻量机（2026-09-19，路由实测达标）


def main() -> None:
    pool = [l.strip() for l in open(os.path.join(HUB, "ua_pool.txt"),
                                    encoding="utf-8") if l.strip()]
    proxies = [l.split(":")[0] for l in open(os.path.join(HUB, "proxies", "master_final.txt"))
               if l.strip()]
    keys = [f"proxy:{ip}" for ip in proxies] + [f"host:{h}" for h in R_HOSTS]
    if len(keys) > len(pool):
        sys.exit(f"出口 {len(keys)} 个 > 池 {len(pool)} 条，无法不重复分配；"
                 f"请扩充清单或缩减出口")
    out = os.path.join(HUB, "ua_assign.tsv")
    with open(out, "w", encoding="utf-8") as f:
        for k, ua in zip(keys, pool):
            f.write(f"{k}\t{ua}\n")
    used = set(zip(keys, pool))
    assert len({ua for _, ua in used}) == len(keys), "分配出现重复 UA"
    print(f"分配完成：{len(proxies)} 代理 + {len(R_HOSTS)} r 机 = {len(keys)} 出口，"
          f"UA 唯一数 {len({ua for _, ua in used})}，写入 {out}")


if __name__ == "__main__":
    main()
