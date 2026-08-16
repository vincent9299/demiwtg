#!/usr/bin/env python3
"""
filter_vlm.py —— 基于本地 vLLM（Qwen3.8-27B 多模态）的图片质量过滤 pipeline。

职责：对 data/dataset/meta/images.jsonl 中的图片做内容级过滤
（模糊/水印/大面积文字遮挡/截图拼图/与实例名不符等），输出逐图裁决 JSONL。

约定（AGENTS.md）：
- 输入只读 data/dataset/meta/images.jsonl（唯一权威清单）与 data/dataset/blobs/（只读，不改动）
- 结果写顶层 state/filter_vlm/（运行时产物，不进 meta/、不进 git）
- 本脚本的 --report 子命令即结果消费者

用法：
    # 跑过滤（默认并发 16，图片缩到最长边 1280 再送模型）
    python3 curation/filter_vlm.py run

    # 只看标签为"人鱼"的图、先跑 20 张试试
    python3 curation/filter_vlm.py run --instance 人鱼 --limit 20

    # 只过滤高分辨率图（最短边 >= 1024 的才送 VLM，小图直接 keep）
    python3 curation/filter_vlm.py run --min-side 1024

    # 汇总结果
    python3 curation/filter_vlm.py report
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

from PIL import Image

try:
    import httpx
except ImportError:
    sys.exit("缺少 httpx：python3 -m pip install httpx")

DEFAULT_ENDPOINT = "http://localhost:8000/v1/chat/completions"
DEFAULT_MODEL = "qwen3.8-27b"

SYSTEM_PROMPT = (
    "你是图片数据集质检员。对每张图给出裁决，严格按 JSON 输出，不要输出其他内容。\n"
    '格式：{"verdict":"keep|drop","score":0-10的整数,"issues":[],"reason":"一句话"}\n'
    "issues 从以下枚举选择（可多选，无问题则空数组）：\n"
    "blur(模糊) watermark(水印/logo) text_overlay(大面积文字遮挡) "
    "collage(拼图/截图/多图拼接) low_info(纯色/极简无信息) "
    "off_topic(与实例名不符) human_real(真人照片冒充IP形象) other(其他缺陷)\n"
    "score 表示图片作为该 IP 训练/检索素材的可用度。verdict=drop 当且仅当存在明显缺陷。"
)

USER_PROMPT_TPL = "该图应描绘实体：{tags}。请质检这张图。"

# 从模型回复中提取 JSON 对象（容忍 thinking 前缀/```json 包裹）
_JSON_RE = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}", re.S)


def repo_root_from_meta(meta: Path) -> Path:
    """AGENTS.md 约定：仓库根由 --meta（默认 data/dataset/meta）向上三级推导。"""
    return meta.resolve().parent.parent.parent


def load_manifest(meta_dir: Path) -> list[dict]:
    rows = []
    with open(meta_dir / "images.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_done(out_path: Path) -> set[str]:
    done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["sha256"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def encode_image(ds_root: Path, row: dict, max_edge: int) -> tuple[str, int, int] | None:
    """读 blob → 缩放到最长边 max_edge → JPEG base64。失败返回 None。"""
    path = ds_root / row["path"]
    if not path.exists():
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = max_edge / max(w, h)
            if scale < 1.0:
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode(), w, h
    except Exception:
        return None


def parse_verdict(text: str) -> dict:
    m = _JSON_RE.search(text)
    if not m:
        return {"verdict": "uncertain", "score": None, "issues": [],
                "reason": text[:200], "parse_error": True}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"verdict": "uncertain", "score": None, "issues": [],
                "reason": m.group(0)[:200], "parse_error": True}
    d["verdict"] = str(d.get("verdict", "uncertain")).lower()
    if d["verdict"] not in ("keep", "drop"):
        d["verdict"] = "uncertain"
    d.setdefault("issues", [])
    return d


async def call_vlm(client: httpx.AsyncClient, endpoint: str, model: str,
                   b64: str, tags: list[str], max_tokens: int,
                   retries: int) -> tuple[dict, int]:
    """调 vLLM，返回 (解析后的裁决, 尝试次数)。"""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        # Qwen3.8 默认开 thinking，会把 token 预算耗在推理链上；批量质检关掉
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text",
                 "text": USER_PROMPT_TPL.format(tags="、".join(tags) or "未知")},
            ]},
        ],
    }
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            r = await client.post(endpoint, json=payload, timeout=120)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return parse_verdict(content), attempt
        except Exception as e:  # noqa: BLE001 - 网络/服务端错误统一退避重试
            last_err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(min(2 ** attempt, 15))
    return {"verdict": "uncertain", "score": None, "issues": [],
            "reason": "", "ok": False, "error": last_err}, retries


async def worker(name: str, queue: asyncio.Queue, client: httpx.AsyncClient,
                 args, out_f, counter: dict, lock: asyncio.Lock):
    while True:
        row = await queue.get()
        try:
            if row is None:
                return
            t0 = time.time()
            rec = {"sha256": row["sha256"], "path": row["path"],
                   "instances": row.get("instances", []), "width": row.get("width"),
                   "height": row.get("height")}
            encoded = encode_image(args.dataset, row, args.max_edge)
            if encoded is None:
                rec.update({"verdict": "uncertain", "ok": False,
                            "error": "image_unreadable"})
            else:
                b64, _, _ = encoded
                verdict, attempts = await call_vlm(
                    client, args.endpoint, args.model, b64,
                    row.get("instances", []), args.max_tokens, args.retries)
                rec.update(verdict)
                rec["ok"] = verdict.get("ok", True)
                rec["attempts"] = attempts
                rec["elapsed_ms"] = int((time.time() - t0) * 1000)
            async with lock:
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out_f.flush()
                counter["done"] += 1
                counter[rec.get("verdict", "uncertain")] = \
                    counter.get(rec.get("verdict", "uncertain"), 0) + 1
                if counter["done"] % args.log_every == 0:
                    rate = counter["done"] / max(time.time() - counter["t0"], 1e-6)
                    eta = (counter["total"] - counter["done"]) / max(rate, 1e-6)
                    print(f"[{counter['done']}/{counter['total']}] "
                          f"keep={counter.get('keep', 0)} "
                          f"drop={counter.get('drop', 0)} "
                          f"uncertain={counter.get('uncertain', 0)} "
                          f"{rate:.1f} img/s ETA {eta/60:.1f} min", flush=True)
        finally:
            queue.task_done()


async def run(args):
    rows = load_manifest(args.meta)
    # 候选过滤：标签/分辨率预筛/数量上限
    if args.instance:
        rows = [r for r in rows if args.instance in (r.get("instances") or [])]
    total_before = len(rows)
    ds_root = args.dataset

    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out_path)
    pending = [r for r in rows if r["sha256"] not in done]
    print(f"清单 {total_before} 张，已完成 {len(done)}，本次待处理 {len(pending)}")

    # 小图直保（不满足 --min-side 的不送 VLM，省算力）
    small, to_vlm = [], []
    for r in pending:
        if args.min_side > 0 and min(r.get("width") or 0, r.get("height") or 0) < args.min_side:
            small.append(r)
        else:
            to_vlm.append(r)
    if args.limit > 0:
        to_vlm = to_vlm[: args.limit]

    with open(out_path, "a", encoding="utf-8") as out_f:
        for r in small:
            out_f.write(json.dumps({
                "sha256": r["sha256"], "path": r["path"], "instances": r.get("instances", []),
                "width": r.get("width"), "height": r.get("height"),
                "verdict": "keep", "score": None, "issues": [],
                "reason": f"skip: 最短边<{args.min_side}，不送VLM", "ok": True,
            }, ensure_ascii=False) + "\n")
        if small:
            print(f"小图直保 {len(small)} 张（最短边 < {args.min_side}）")
        if not to_vlm:
            print("没有需要送 VLM 的图片")
            return

        counter = {"done": 0, "total": len(to_vlm), "t0": time.time()}
        lock = asyncio.Lock()
        queue: asyncio.Queue = asyncio.Queue(maxsize=args.concurrency * 4)

        limits = httpx.Limits(max_connections=args.concurrency + 4,
                              max_keepalive_connections=args.concurrency)
        async with httpx.AsyncClient(limits=limits) as client:
            workers = [asyncio.create_task(
                worker(f"w{i}", queue, client, args, out_f, counter, lock))
                for i in range(args.concurrency)]
            for r in to_vlm:
                await queue.put(r)
            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers)

    print(f"完成，结果写入 {out_path}")
    print(f"汇总：keep={counter.get('keep', 0)} drop={counter.get('drop', 0)} "
          f"uncertain={counter.get('uncertain', 0)}")


def report(args):
    if not args.out.exists():
        sys.exit(f"结果文件不存在：{args.out}")
    verdicts: dict[str, int] = {}
    issues: dict[str, int] = {}
    errors = 0
    total = 0
    drops: list[dict] = []
    with open(args.out, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            v = d.get("verdict", "uncertain")
            verdicts[v] = verdicts.get(v, 0) + 1
            if not d.get("ok", True):
                errors += 1
            for iss in d.get("issues", []):
                issues[iss] = issues.get(iss, 0) + 1
            if v == "drop":
                drops.append(d)

    print(f"总计 {total} 张（含历史累计），失败/异常 {errors}")
    print("裁决分布：")
    for v, n in sorted(verdicts.items(), key=lambda x: -x[1]):
        print(f"  {v:<10} {n:>6}  {n/total:6.1%}")
    if issues:
        print("缺陷分布（drop/uncertain 图，可叠加）：")
        for k, n in sorted(issues.items(), key=lambda x: -x[1]):
            print(f"  {k:<14} {n:>6}")
    if drops:
        print(f"\ndrop 样例（前 {min(5, len(drops))} 条）：")
        for d in drops[:5]:
            print(f"  {d['sha256'][:16]} instances={d.get('instances')} "
                  f"issues={d.get('issues')} reason={str(d.get('reason', ''))[:60]}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--meta", type=Path, default=Path("data/dataset/meta"),
                        help="meta 目录（默认 data/dataset/meta）")
        default_root = repo_root_from_meta(Path("data/dataset/meta"))
        sp.add_argument("--out", type=Path,
                        default=default_root / "state" / "filter_vlm" / "results.jsonl",
                        help="结果 JSONL（默认 state/filter_vlm/results.jsonl）")

    pr = sub.add_parser("run", help="执行过滤")
    add_common(pr)
    pr.add_argument("--dataset", type=Path, default=Path("data/dataset"),
                    help="数据湖根目录（默认 data/dataset）")
    pr.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="vLLM chat completions 地址")
    pr.add_argument("--model", default=DEFAULT_MODEL)
    pr.add_argument("--concurrency", type=int, default=16)
    pr.add_argument("--max-edge", type=int, default=1280, help="送模型前最长边缩放阈值")
    pr.add_argument("--max-tokens", type=int, default=400)
    pr.add_argument("--retries", type=int, default=3)
    pr.add_argument("--instance", default=None, help="只处理含该实例名的图")
    pr.add_argument("--min-side", type=int, default=0,
                    help="只把最短边>=该值的图送 VLM，更小的直接 keep（0=全部送）")
    pr.add_argument("--limit", type=int, default=0, help="本次最多送 VLM 的张数（0=不限）")
    pr.add_argument("--log-every", type=int, default=50)

    pp = sub.add_parser("report", help="汇总结果")
    add_common(pp)

    args = p.parse_args()
    if args.cmd == "run":
        args.meta = args.meta.resolve()
        args.dataset = args.dataset.resolve()
        asyncio.run(run(args))
    else:
        report(args)


if __name__ == "__main__":
    main()
