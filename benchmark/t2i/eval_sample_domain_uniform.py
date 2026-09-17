"""t2i v6.0 影子批抽样：29 域等概率、二级分支限一实例。

与 eval_sample.py（按二级分支 sqrt 配额的图级分层抽样）不同，本脚本抽的是
**实例**而不是图：域等概率轮转，每域每次取一个实例，域内保证每个二级分支
至多取一个实例（跨域天然不撞二级分支）；每实例取其质量分最高的一张合格图
作代表。

流程：
1. 过滤：质量门（默认 t2i 抽样定案口径）
   quality >= 9.0 AND identity = true AND least(width, height) >= 768；
2. 实例化：每实例取质量分最高的一行作代表图；
3. 域等概率轮转抽样（种子固定），域内二级分支不重复；
4. 排除集：三赛道 data/samples*.jsonl 的 sha256 全量剔除（防与历史样本共图）。

产物（评测数据，不入 git）：
    benchmark/t2i/data/samples_v60_uniform.jsonl
    benchmark/t2i/data/images_v60/<nnnn>_<实例>_<sha8>.<ext>   # 代表图拷贝

用法：
    python3 benchmark/t2i/eval_sample_domain_uniform.py --n 10 [--seed 20260830]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

SUB_DIR = Path(__file__).resolve().parent                     # t2i/
BENCH_ROOT = SUB_DIR.parent                                   # benchmark/
REPO_ROOT = BENCH_ROOT.parent                                 # 仓库根
sys.path.insert(0, str(REPO_ROOT / "data"))

from collect_v2.mount_map import load_mount_map                  # noqa: E402

META_DIR = REPO_ROOT / "datasets" / "demiwtg" / "meta"
OUT_DIR = SUB_DIR / "data"

DEFAULT_FILTER = ("quality >= 9.0 AND identity = true "
                  "AND least(width, height) >= 768")

DEFAULT_EXCLUDES = sorted({
    p for sub in ("vlm", "t2i", "edit")
    if (BENCH_ROOT / sub).is_dir()
    for p in (BENCH_ROOT / sub).rglob("samples*.jsonl")
})

OWN_IMG_RE = re.compile(r"^\d{5}_")     # 本脚本产物命名前缀（五位，避开四位旧批）
ID_BASE = 60000                          # sample_id 起点（避开主样本号段）


def clean_name(s: str, n: int = 40) -> str:
    s = re.sub(r'[\\/:*?"\'<>|\s]+', '_', str(s)).strip('_')
    return s[:n] or 'noname'


def load_exclude_shas(manifests: list) -> tuple:
    """排除清单 → (sha 集, 实例集)。带 instance 字段的清单（历史出题样本）
    同时做实例级排除——同实例换图重进池会重复出题，sha 级排除挡不住。"""
    shas, insts = set(), set()
    for p in manifests:
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("sha256"):
                    shas.add(rec["sha256"])
                if rec.get("instance"):
                    insts.add(rec["instance"])
    return shas, insts


def branch_of(paths: list) -> tuple:
    """挂载路径列表 → (域, 二级分支)；取第一条可解析路径。"""
    for p in paths or []:
        segs = [s.strip() for s in p.split(" / ")]
        if len(segs) >= 3:
            return segs[1], segs[2]
    return ("未挂载", "未挂载")


def load_instance_pool(manifest: Path, taxonomy: Path, filter_sql: str,
                       exclude_shas: set, exclude_insts: set = None) -> dict:
    """过滤 + 实例化：{实例名: 代表图行}（每实例取质量分最高的一行）。"""
    import duckdb
    exclude_insts = exclude_insts or set()
    mounts = load_mount_map(str(taxonomy))
    con = duckdb.connect()
    res = con.execute(
        f"SELECT * FROM read_json_auto('{manifest}', "
        f"maximum_object_size=1073741824) WHERE {filter_sql}")
    cols = [d[0] for d in res.description]
    best: dict = {}
    for row in res.fetchall():
        rec = dict(zip(cols, row))
        if rec["sha256"] in exclude_shas:
            continue
        insts = rec.get("instances") or []
        if not insts:
            continue
        inst = insts[0]
        if inst in exclude_insts:
            continue                    # 历史出题已用实例：整个排除（防重复出题）
        paths = mounts.get(inst) or []
        if not paths:
            continue                        # 未挂载实例无域信息，不进等概率抽样
        rec["_mount_paths"] = paths
        rec["_branch"] = branch_of(paths)
        cur = best.get(inst)
        if cur is None or (rec.get("quality") or 0) > (cur.get("quality") or 0):
            best[inst] = rec
    return best


def pick_domain_uniform(pool: dict, n: int, seed: int) -> list:
    """域等概率轮转：每域每次取一实例，域内二级分支不重复。"""
    rng = random.Random(seed)
    by_domain: dict = defaultdict(list)
    for inst, rec in pool.items():
        by_domain[rec["_branch"][0]].append((inst, rec))
    for d in by_domain:                                  # 域内候选洗牌
        rng.shuffle(by_domain[d])
    domains = sorted(by_domain)
    rng.shuffle(domains)                                 # 起始域序洗牌（等概率）

    picked, used_l2 = [], defaultdict(set)
    while len(picked) < n:
        progressed = False
        for d in domains:
            if len(picked) >= n:
                break
            for inst, rec in by_domain[d]:
                l2 = rec["_branch"][1]
                if l2 in used_l2[d]:
                    continue
                if any(p["_branch"][0] == d and p["instance"] == inst
                       for p in picked):
                    continue
                used_l2[d].add(l2)
                picked.append({**rec, "instance": inst})
                progressed = True
                break
        if not progressed:
            print(f"  [warn] 候选池耗尽，只抽到 {len(picked)} 个", file=sys.stderr)
            break
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=10, help="抽取实例数（默认 10）")
    ap.add_argument("--filter", default=DEFAULT_FILTER,
                    help="候选过滤 SQL（默认 t2i 抽样门："
                         "quality>=9.0 AND identity AND 短边>=768）")
    ap.add_argument("--seed", type=int, default=20260830)
    ap.add_argument("--id-base", type=int, default=ID_BASE,
                    help="sample_id 起号（默认 60000；新批次应避开已用号段，"
                         "如 r10 用 60001-60010，r11 从 60011 起）")
    ap.add_argument("--manifest", type=Path, default=META_DIR / "images.jsonl")
    ap.add_argument("--taxonomy", type=Path, default=META_DIR / "taxonomy.json")
    ap.add_argument("--blobs", type=Path,
                    default=REPO_ROOT / "datasets" / "demiwtg" / "blobs")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "samples_v60_uniform.jsonl")
    ap.add_argument("--img-dir", type=Path, default=OUT_DIR / "images_v60")
    ap.add_argument("--exclude", type=Path, nargs="*", default=None,
                    help="排除清单（默认三赛道 samples*.jsonl；空列表 = 不排除）")
    args = ap.parse_args()

    excludes = DEFAULT_EXCLUDES if args.exclude is None else args.exclude
    exclude_shas, exclude_insts = load_exclude_shas(excludes)
    print(f"排除集：{len(excludes)} 份清单，sha {len(exclude_shas)} 个、"
          f"实例 {len(exclude_insts)} 个（实例级排除防重复出题）")

    pool = load_instance_pool(args.manifest, args.taxonomy, args.filter,
                              exclude_shas, exclude_insts)
    dom_cnt = defaultdict(int)
    for rec in pool.values():
        dom_cnt[rec["_branch"][0]] += 1
    print(f"合格实例池：{len(pool)} 个，覆盖 {len(dom_cnt)} 个域")

    picked = pick_domain_uniform(pool, args.n, args.seed)
    print(f"实抽 {len(picked)} 个实例：")
    for p in picked:
        d, l2 = p["_branch"]
        print(f"  [{d} / {l2}] {p['instance']}")

    args.img_dir.mkdir(parents=True, exist_ok=True)
    lo, hi = args.id_base + 1, args.id_base + args.n   # 只清本 run 号段（补抽批次不清主批图）
    for p in args.img_dir.iterdir():          # 清本脚本旧产物
        m = OWN_IMG_RE.match(p.name)
        if p.is_file() and m:
            idx = int(p.name.split("_", 1)[0])
            if lo <= idx <= hi:
                p.unlink()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    n_copied = 0
    with args.out.open("w", encoding="utf-8") as fout:
        for i, rec in enumerate(picked, 1):
            sha = rec["sha256"]
            ext = rec.get("ext") or "jpg"
            inst = rec["instance"]
            idx = args.id_base + i
            name = f"{idx}_{clean_name(inst)}_{sha[:8]}.{ext}"
            src = args.blobs / sha[:2] / f"{sha}.{ext}"
            if not src.exists():
                print(f"  [warn] blob 缺失跳过: {sha[:12]}", file=sys.stderr)
                continue
            shutil.copy2(src, args.img_dir / name)
            d, l2 = rec["_branch"]
            out = {
                "sample_id": f"{idx}",
                "image": f"{args.img_dir.name}/{name}",
                "sha256": sha,
                "instance": inst,
                "instances": rec["instances"],
                "l1": d,
                "l2": l2,
                "mount_paths": rec["_mount_paths"],
                "quality": rec.get("quality"),
                "focus": rec.get("focus"),
                "kb_match": rec.get("kb_match"),
                "width": rec.get("width") or 0,
                "height": rec.get("height") or 0,
                "caption": rec.get("caption", ""),
                "queries": rec.get("queries", {}),
            }
            fout.write(json.dumps(out, ensure_ascii=False) + "\n")
            n_copied += 1
    print(f"\n完成：{n_copied} 个 -> {args.out}")


if __name__ == "__main__":
    main()
