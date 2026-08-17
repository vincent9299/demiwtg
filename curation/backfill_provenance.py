"""从 state/collect/runs/*/downloads_success.jsonl 回填 images.jsonl 的采集溯源字段。

背景：2026-08-16 起 collect/pipeline.py 落盘 queries/query_langs/asset_ids 三字段；
历史主清单缺失，本脚本用尚存的批次产物按 sha256（回落 content_url）join 回填。
早期 run 已清理的部分补不上，留空即可——不阻塞新字段上线。

用法：
    python3 curation/backfill_provenance.py            # dry-run 预览
    python3 curation/backfill_provenance.py --write    # 实际写回（持 meta_lock，原子替换）
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from collect.util import meta_lock

MAP_FIELDS = ("queries", "query_langs", "asset_ids")


def _scan_runs(runs_dir: str) -> tuple:
    """扫描全部批次的 downloads_success.jsonl，建 sha256/content_url 两套索引。

    索引值：该资产出现过的溯源映射（按实例/来源对齐，先见优先）。
    """
    by_sha, by_url = {}, {}

    def _merge(idx: dict, key: str, inst: str, row: dict) -> None:
        if not key:
            return
        slot = idx.setdefault(key, {"queries": {}, "query_langs": {}, "asset_ids": {}})
        src = row.get("source") or ""
        if inst and row.get("query"):
            slot["queries"].setdefault(inst, row["query"])
        if inst and row.get("query_lang"):
            slot["query_langs"].setdefault(inst, row["query_lang"])
        if src and row.get("asset_id"):
            slot["asset_ids"].setdefault(src, row["asset_id"])

    if not os.path.isdir(runs_dir):
        return by_sha, by_url
    n_rows = n_runs = 0
    for name in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, name, "downloads_success.jsonl")
        if not os.path.isfile(path):
            continue
        n_runs += 1
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n_rows += 1
                inst = row.get("instance") or ""
                _merge(by_sha, row.get("sha256") or "", inst, row)
                _merge(by_url, row.get("content_url") or "", inst, row)
    print(f"扫描 {n_runs} 个批次 / {n_rows} 条成功记录 → sha 索引 {len(by_sha)}、url 索引 {len(by_url)}")
    return by_sha, by_url


def _apply(rec: dict, slot: dict) -> bool:
    """把索引槽位的溯源映射并入记录（已有 key 不覆盖）；返回是否有变更。"""
    changed = False
    for fld in MAP_FIELDS:
        add = slot.get(fld) or {}
        if not add:
            continue
        base = rec.get(fld)
        if not isinstance(base, dict):
            base = {}
        new = {k: v for k, v in add.items() if k not in base}
        if new:
            base.update(new)
            rec[fld] = base
            changed = True
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "dataset", "meta"))
    ap.add_argument("--write", action="store_true", help="实际写回（缺省 dry-run 只报统计）")
    args = ap.parse_args()

    meta_dir = os.path.abspath(args.meta)
    mpath = os.path.join(meta_dir, "images.jsonl")
    if not os.path.exists(mpath):
        sys.exit(f"主清单不存在：{mpath}")
    runs_dir = os.path.join(ROOT, "state", "collect", "runs")
    by_sha, by_url = _scan_runs(runs_dir)

    records, raw_lines = [], []
    with open(mpath, encoding="utf-8") as f:
        for line in f:
            raw_lines.append(line)
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    records.append(None)

    n_hit_sha = n_hit_url = n_changed = 0
    for rec in records:
        if not rec:
            continue
        slot = by_sha.get(rec.get("sha256") or "")
        if slot:
            n_hit_sha += 1
        else:
            slot = by_url.get(rec.get("content_url") or "")
            if slot:
                n_hit_url += 1
        if slot and _apply(rec, slot):
            n_changed += 1

    print(f"主清单 {len(records)} 行：sha 命中 {n_hit_sha}，url 回落命中 {n_hit_url}，"
          f"未命中 {len(records) - n_hit_sha - n_hit_url}，实际变更 {n_changed} 行")
    if not args.write:
        print("dry-run（加 --write 落盘）")
        return
    if not n_changed:
        print("无变更，跳过写盘")
        return

    with meta_lock(meta_dir):
        tmp_path = mpath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            ri = 0
            for line in raw_lines:  # 保序重写：坏行原样保留
                if line.strip():
                    rec = records[ri]
                    ri += 1
                    if rec:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        continue
                f.write(line)
        os.replace(tmp_path, mpath)
    print(f"已写回 {mpath}")


if __name__ == "__main__":
    main()
