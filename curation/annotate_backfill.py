"""补标驱动：instance_images.jsonl 原生 kb_match=None 行的 VLM 打标与回写。

背景（2026-09-02）：focus1000 补图链沿 EN 链先例 --no-annotate 纯下载，
21,370 行中 18,586 行从未打标（kb_match/richness/caption/identity/focus/quality
全 null），edit 赛道的 quality/identity 门无从生效。chain.py 预期的「后续补标」
此前只有 migrate（images.jsonl 源，该文件已于 2026-09-06 收官退役）路径，本驱动补上 metadata 原生 null 行的入口。

契约：
- 打标与采集链同口径：复用 op_annotate 的 SYSTEM_PROMPT/build_block/
  encode_for_vlm/_call_vlm/parse_annotation，五字段全打 + quality 同权重派生；
- 打标知识块查表（{概念名: {desc, aliases}}）由 concepts.json 概念行 + docs 层
  草稿（state/collect/concepts_docs_draft.jsonl）现场构建（2026-09-07 概念化迁移，
  原 op_annotate.load_instance_kb 随 instances.json 退役）；
- 断点续跑：标注结果先落 state/collect/annotate_backfill_focus1000.jsonl
  （(sha256, instance) 键控，append），崩溃/中断零损失，重跑跳过已标键；
- 回写：全部完成后一次性合并——持 meta/.meta.lock（fcntl.flock 排他，
  与下载链 sink 互斥），全量重写 instance_images.jsonl（临时文件同目录 + os.replace
  原子替换），只在 kb_match 为 null 的行上填字段，VLM 失败行保持 null；
- 与下载链并存：标注阶段不持锁（纯读 blob + 独立 state 文件），
  仅合并阶段短暂持锁（~1-2 分钟，链侧 sink 排队等待）。

用法（GPU1 上的 vLLM :8001）：
    python3 curation/annotate_backfill.py --limit 20
    python3 curation/annotate_backfill.py            # 全量
    python3 curation/annotate_backfill.py --merge-only
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent          # 仓库根（curation/ 已提升根目录）
sys.path.insert(0, str(REPO_ROOT / "data"))                 # data/ 兼容 shim（collect_v2.* re-export）
from collect_v2 import op_annotate  # noqa: E402

DATASET_DIR = REPO_ROOT / "datasets" / "demiwtg"
META_DIR = DATASET_DIR / "meta"
DOCS_DRAFT = REPO_ROOT / "state" / "collect" / "concepts_docs_draft.jsonl"

DEFAULT_INSTANCES = REPO_ROOT / "state" / "collect" / "focus1000_instances.json"
DEFAULT_STATE = REPO_ROOT / "state" / "collect" / "annotate_backfill_focus1000.jsonl"

ENDPOINT = "http://localhost:8001/v1/chat/completions"
MODEL = "qwen3.8-27b"
CONCURRENCY = 32


def scan_pending(focus: set, state_path: Path) -> list[dict]:
    """扫 instance_images.jsonl：focus 实例 ∩ kb_match=None ∩ 未在 state 里 → 待标清单。"""
    done = set()
    if state_path.exists():
        with state_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kb_match") is not None:
                    done.add((r["sha256"], r["instance"]))
    pending, seen = [], set()
    with (META_DIR / "instance_images.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            insts = row.get("instances") or []
            if row.get("kb_match") is not None or len(insts) != 1:
                continue
            inst = insts[0]
            if inst not in focus:
                continue
            key = (row["sha256"], inst)
            if key in done or key in seen:
                continue
            seen.add(key)
            pending.append({"sha256": row["sha256"], "instance": inst,
                            "path": row.get("path"), "ext": row.get("ext")})
    return pending


async def annotate_one(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                       kb: dict, rec: dict, endpoint: str) -> dict:
    rel = rec.get("path") or f"blobs/{rec['sha256'][:2]}/{rec['sha256']}.{rec['ext'] or 'bin'}"
    blob = DATASET_DIR / rel
    out = {"sha256": rec["sha256"], "instance": rec["instance"]}
    async with sem:
        try:
            data = await asyncio.to_thread(blob.read_bytes)
            b64 = await asyncio.to_thread(op_annotate.encode_for_vlm, data)
            if b64 is None:
                return out
            ann = await op_annotate._call_vlm(
                client, b64, op_annotate.build_block(rec["instance"], kb),
                endpoint=endpoint, model=MODEL)
        except Exception:  # noqa: BLE001 - 单图失败不阻断，重跑时重试
            return out
    if ann is not None:
        w_kb, w_fo, w_ri = op_annotate.QUALITY_WEIGHTS
        out.update(ann, quality=round(w_kb * ann["kb_match"] + w_fo * ann["focus"]
                                      + w_ri * ann["richness"], 1))
    return out


def load_results(state_path: Path) -> dict:
    """state 文件 → {(sha, instance): 标注 dict}（只含有成功标注的行）。"""
    res = {}
    with state_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("kb_match") is not None:
                res[(r["sha256"], r["instance"])] = r
    return res


def merge_back(state_path: Path) -> int:
    """持锁全量重写 instance_images.jsonl：null 行填标注字段。返回合并行数。"""
    res = load_results(state_path)
    manifest = META_DIR / "instance_images.jsonl"
    tmp = manifest.with_suffix(".jsonl.tmp")
    n_merged = 0
    with open(META_DIR / ".meta.lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with manifest.open(encoding="utf-8") as fin, \
                    tmp.open("w", encoding="utf-8") as fout:
                for line in fin:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        fout.write(line)
                        continue
                    if row.get("kb_match") is None and \
                            len(row.get("instances") or []) == 1:
                        key = (row["sha256"], row["instances"][0])
                        ann = res.get(key)
                        if ann:
                            row.update({k: ann[k] for k in
                                        ("kb_match", "richness", "caption",
                                         "identity", "focus", "quality")})
                            n_merged += 1
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(tmp, manifest)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return n_merged


def build_kb() -> dict:
    """打标知识块查表 {概念名: {desc, aliases}}：concepts.json 行 + docs 层草稿现场构建。"""
    doc = json.loads((META_DIR / "concepts.json").read_text(encoding="utf-8"))
    docs = {}
    if DOCS_DRAFT.exists():
        with DOCS_DRAFT.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                docs[r["name"]] = r.get("body") or ""
    return {c["name"]: {"desc": docs.get(c["name"], ""),
                        "aliases": c.get("aliases") or []}
            for c in doc.get("concepts", [])}


async def run(args) -> None:
    mini = json.loads(args.instances.read_text(encoding="utf-8"))
    focus = {it["name"] for it in (mini.get("concepts") or mini.get("instances") or [])}
    pending = scan_pending(focus, args.state)
    if args.limit:
        pending = pending[: args.limit]
    print(f"待补标 {len(pending)} 行（state={args.state}）", flush=True)

    kb = build_kb()
    sem = asyncio.Semaphore(args.concurrency)
    t0, n_ok, n_fail = time.time(), 0, 0
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.open("a", encoding="utf-8") as fout:
        async with httpx.AsyncClient() as client:
            tasks = [annotate_one(client, sem, kb, rec, args.endpoint)
                     for rec in pending]
            for i, fut in enumerate(asyncio.as_completed(tasks), 1):
                out = await fut
                fout.write(json.dumps(out, ensure_ascii=False) + "\n")
                fout.flush()
                if out.get("kb_match") is not None:
                    n_ok += 1
                else:
                    n_fail += 1
                if i % 100 == 0 or i == len(tasks):
                    rate = i / max(time.time() - t0, 1e-6)
                    eta = (len(tasks) - i) / max(rate, 1e-6) / 60
                    print(f"[{i}/{len(tasks)}] {rate:.2f} img/s, "
                          f"ok {n_ok}, fail {n_fail}, ETA {eta:.0f} min",
                          flush=True)
    print(f"打标完成：ok {n_ok}, fail {n_fail}（fail 行重跑自动重试）", flush=True)

    if not args.no_merge:
        n = merge_back(args.state)
        print(f"合并回写 {n} 行 -> instance_images.jsonl", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instances", type=Path, default=DEFAULT_INSTANCES)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-merge", action="store_true",
                    help="只打标不回写（结果留在 state 文件）")
    ap.add_argument("--merge-only", action="store_true",
                    help="跳过打标，直接合并 state 里已有结果")
    args = ap.parse_args()
    if args.merge_only:
        n = merge_back(args.state)
        print(f"合并回写 {n} 行 -> instance_images.jsonl")
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
