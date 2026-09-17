"""重点补图池抽样：--include 必收概念 + 分层新抽概念 → mini 概念表。

为「重点下载链」生成输入：chain --instances <mini表> 起独立链补图，
配 --skip-covered N（纯图数口径：--min-quality 0 --no-require-identity）
实现「每概念补到 ≥N 张、超了跳过、supervise 迭代到全达标」。

与 benchmark 抽样同款均匀口径：(L1,L2) 分支 sqrt 配额（最大余数法）；
抽样单位是概念（不是图）。新抽候选 = 全量在册概念，默认只排除
include 名单本身（无质量门、无图数门；2026-08-31 用户拍板），
--exclude-bench-history 可选叠加 benchmark 三赛道历史样本概念并集
（samples*.jsonl 的 instances 并集 + questions*.jsonl 经 _sample_image
文件名 sha8 关联出的概念）。

产物均为运行时状态（state/collect/，不进 git、不进 meta/）：
    mini 概念表：schema 同 concepts.json（四字段契约 name/aliases/
    carriers/taxonomy，记录整条拷贝），meta 注明来源与口径。
    抽样报告：分支分布、来源拆分（include / 新抽）、排除集规模。

用法：
    python3 curation/focus_sample.py \
        --n 300 --include state/collect/focus_bench_v1.json \
        --out state/collect/focus500_concepts.json \
        --report state/collect/focus500_report.json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

SUB_DIR = Path(__file__).resolve().parent            # curation/
REPO_ROOT = SUB_DIR.parent                           # 仓库根（模块已提升根目录，2026-09-05）
sys.path.insert(0, str(REPO_ROOT))

from taxonomy.mount_map import load_mount_map        # noqa: E402

META_DIR = REPO_ROOT / "datasets" / "demiwtg" / "meta"
BENCH_ROOT = REPO_ROOT / "benchmark"
SHA8_RE = re.compile(r"_([0-9a-f]{8})\.\w+$")


def bench_instance_union() -> set:
    """benchmark 三赛道全部历史样本实例并集（samples + questions 关联）。"""
    insts: set = set()
    sha2i: dict = {}
    for p in sorted(BENCH_ROOT.glob("*/data/samples*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sha2i[r["sha256"][:8]] = r["instance"]
                for i in (r.get("instances") or []):
                    insts.add(i)
                if r.get("instance"):
                    insts.add(r["instance"])
    for p in sorted(BENCH_ROOT.glob("*/data/**/questions*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for i in (r.get("instances") or []):
                    insts.add(i)
                if r.get("instance"):
                    insts.add(r["instance"])
                img = r.get("_sample_image") or ""
                m = SHA8_RE.search(img)
                if m and m.group(1) in sha2i:
                    insts.add(sha2i[m.group(1)])
    return insts


def branch_of(mounts: dict, name: str) -> tuple:
    paths = mounts.get(name) or []
    for p in paths:
        segs = [s.strip() for s in p.split(" / ")]
        if len(segs) >= 3:
            return segs[1], segs[2]
    return ("未挂载", "未挂载")


def instance_image_counts(manifest: Path) -> dict:
    """每概念当前行数（纯图数口径，与链 --skip-covered + --min-quality 0 一致）。"""
    import duckdb
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT i, count(*) FROM read_json_auto('{manifest}', "
        f"maximum_object_size=1073741824), UNNEST(instances) AS t(i) "
        f"GROUP BY i").fetchall()
    return dict(rows)


def stratified_pick(cands: list, n: int, seed: int) -> list:
    """按 (L1,L2) sqrt 配额抽 n 个概念名（最大余数法，分支序确定性）。"""
    by_branch = defaultdict(list)
    for name, br in cands:
        by_branch[br].append(name)
    weights = {b: math.sqrt(len(v)) for b, v in by_branch.items()}
    total_w = sum(weights.values())
    raw = {b: n * w / total_w for b, w in weights.items()}
    quota = {b: int(raw[b]) for b in raw}
    rem = n - sum(quota.values())
    for b in sorted(quota, key=lambda x: -(raw[x] - int(raw[x]))):
        if rem <= 0:
            break
        quota[b] += 1
        rem -= 1
    rng = random.Random(seed)
    picked = []
    for b in sorted(by_branch):
        if quota.get(b, 0) <= 0:
            continue
        names = by_branch[b].copy()
        rng.shuffle(names)
        picked.extend(names[:quota[b]])
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(
        description="重点补图池抽样（概念级，benchmark 同款分层口径）")
    ap.add_argument("--n", type=int, default=300, help="新抽概念数")
    ap.add_argument("--include", type=Path, required=True,
                    help="必收概念名单（json 数组文件；如 bench_v1 200 概念）")
    ap.add_argument("--min-images", type=int, default=20,
                    help="补图目标张数（仅报告统计口径，不筛候选）")
    ap.add_argument("--exclude-bench-history", action="store_true",
                    help="新抽额外排除 benchmark 三赛道历史样本概念并集")
    ap.add_argument("--seed", type=int, default=20260831)
    ap.add_argument("--manifest", type=Path,
                    default=META_DIR / "images.jsonl")
    ap.add_argument("--taxonomy", type=Path, default=META_DIR / "taxonomy.json")
    ap.add_argument("--concepts-src", type=Path,
                    default=META_DIR / "concepts.json",
                    help="概念权威源（mini 表整条拷贝记录）")
    ap.add_argument("--out", type=Path, required=True, help="mini 实例表输出（覆盖写）")
    ap.add_argument("--report", type=Path, default=None, help="抽样报告输出")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    include_names = json.loads(args.include.read_text(encoding="utf-8"))
    exclude = set(include_names)
    if args.exclude_bench_history:
        exclude |= bench_instance_union()
    print(f"排除集：{len(exclude)} 概念（include 必收名单"
          f"{' + benchmark 历史并集' if args.exclude_bench_history else ''}）",
          flush=True)

    counts = instance_image_counts(args.manifest)
    mounts = load_mount_map(str(args.taxonomy))
    inst_src = json.loads(args.concepts_src.read_text(encoding="utf-8"))
    records = {r["name"]: r for r in inst_src["concepts"]}

    missing = [i for i in include_names if i not in records]
    if missing:
        sys.exit(f"[错误] include 中 {len(missing)} 个概念不在概念表：{missing[:5]} …")

    cands = []
    for name in records:
        if name in exclude:
            continue
        cands.append((name, branch_of(mounts, name)))
    print(f"新抽候选池（全量在册概念，无过滤）：{len(cands)} 概念", flush=True)

    picked = stratified_pick(cands, args.n, args.seed)
    print(f"实抽 {len(picked)} / --n {args.n}", flush=True)

    pool = list(dict.fromkeys(include_names + picked))
    branches = defaultdict(int)
    for name in pool:
        branches[branch_of(mounts, name)] += 1
    src_split = {"include": len(include_names), "新抽": len(picked)}
    covered_now = sum(1 for n in pool if counts.get(n, 0) >= args.min_images)
    print(f"重点池合计 {len(pool)} 概念（{src_split}），"
          f"当前已 ≥{args.min_images} 张 {covered_now} 个", flush=True)
    print("分支分布（Top 15）：")
    for b in sorted(branches, key=lambda x: -branches[x])[:15]:
        print(f"  {b[0]} / {b[1]}: {branches[b]}")

    if args.dry_run:
        print("[dry-run] 不落盘")
        return

    out_records = [records[n] for n in pool]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "schema_version": inst_src.get("schema_version"),
        "meta": {
            "what": "重点补图池 mini 概念表（chain --instances 输入）",
            "source": "curation.focus_sample",
            "include": str(args.include),
            "n_new": args.n,
            "min_images": args.min_images,
            "exclude_bench_history": args.exclude_bench_history,
            "seed": args.seed,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "concepts": out_records,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"mini 概念表已写：{args.out}（{len(out_records)} 条）", flush=True)

    if args.report:
        args.report.write_text(json.dumps({
            "pool_size": len(pool),
            "src_split": src_split,
            "already_covered": covered_now,
            "gap_images": sum(max(0, args.min_images - counts.get(n, 0))
                              for n in pool),
            "branch_dist": {f"{b[0]} / {b[1]}": c
                            for b, c in sorted(branches.items())},
            "picked": picked,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"抽样报告已写：{args.report}", flush=True)


if __name__ == "__main__":
    main()
