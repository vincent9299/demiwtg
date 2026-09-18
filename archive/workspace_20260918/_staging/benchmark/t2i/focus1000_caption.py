#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focus1000 detail caption（任务2）：对 focus1000 池下载图片批量补 300-500 字细节描述。

- 输入：metadata.jsonl 中 focus1000 实例的行，全局按 sha256 去重，剔除短边 <200px；
- VLM：qianwen1 直连 qwen3.8-max（视觉输入，enable_thinking=false 提速）；
- 锚定：prompt 带实例名 + instances.json desc 节选，只描述画面可见内容；
- 产物：data/focus1000/detail_captions.jsonl（sha256 键控断点续跑，不入 git）。

用法：
  python3 focus1000_caption.py [--limit N] [--conc 20] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import re
import sys
import time
from pathlib import Path

import aiohttp

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data" / "focus1000"
OUT_F = DATA_DIR / "detail_captions.jsonl"
META_DIR = REPO_ROOT / "datasets" / "demiwtg" / "meta"
METADATA_F = META_DIR / "metadata.jsonl"
FOCUS_F = REPO_ROOT / "state" / "collect" / "focus1000_instances.json"
BLOBS = REPO_ROOT / "datasets" / "demiwtg" / "blobs"
ENV_F = REPO_ROOT / "modelhub" / ".env"

TEXT_MODEL = "qwen3.8-max"
MIN_SHORT_SIDE = 200

SYSTEM_PROMPT = """你是图像细节描述专家。对给定图片写一段 300-500 字的中文全景细节描述（detail caption），一段连贯文字，不分点。

必须覆盖：
1. 画面主体：是什么、处于什么状态/阶段，主体关键部件/形态/姿态的细粒度刻画（形状、结构、数目、层级、朝向）；
2. 实体特征核对：结合给出的「实体知识」核对画面主体的辨识特征是否呈现、呈现成什么样（不臆造画面中不存在的知识细节）；
3. 色彩与光影：主色调、光源方向与性质、高光阴影、反光/透明/哑光等光效；
4. 构图与视角：机位（平视/俯仰/广角特写）、主体位置、前后景层次与遮挡关系；
5. 纹理与材质：可见的表面质感（金属/织物/植被/水等）；
6. 环境与伴随元素：场景、背景、其他对象及其与主体的关系；
7. 画面中的任何文字/徽标/水印：原样转写并注明位置；
8. 媒介与风格：照片/插画/3D渲染/示意图等。

纪律：只描述画面可见内容；不确定的不写；不以猜测补充实体身份；与「实体知识」冲突时以画面为准并客观描述差异。只输出描述正文，不要前后缀。"""


def load_env():
    env = {}
    for line in ENV_F.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
BASE = ENV["QIANWEN1_API_BASE"]
KEY = ENV["QIANWEN1_API_KEY"]


def load_targets():
    focus = {i["name"]: i for i in json.load(open(FOCUS_F))["instances"]}
    rows = {}
    with open(METADATA_F, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            insts = r.get("instances") or []
            hit = [i for i in insts if i in focus]
            if not hit:
                continue
            if r["sha256"] in rows:
                continue
            w, h = r.get("width") or 0, r.get("height") or 0
            if min(w, h) < MIN_SHORT_SIDE:
                continue
            rows[r["sha256"]] = {"sha256": r["sha256"], "ext": r.get("ext") or "jpg",
                                 "instances": hit, "width": w, "height": h}
    return list(rows.values())


def data_url(sha: str, ext: str) -> str:
    p = BLOBS / sha[:2] / f"{sha}.{ext}"
    b = p.read_bytes()
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(b).decode()}", len(b)


class QuotaExhausted(Exception):
    pass


async def caption_one(session, sem, item, focus, out_f, lock, stats):
    inst_names = item["instances"]
    primary = inst_names[0]
    desc = (focus[primary].get("desc") or "").strip()
    kb = f"实体名：{primary}" + (f"（画面可能与这些实例相关：{'、'.join(inst_names[:4])}）" if len(inst_names) > 1 else "")
    if desc:
        kb += f"\n实体知识（供核对，画面为准）：{desc[:300]}"
    payload = {"model": TEXT_MODEL, "enable_thinking": False, "temperature": 0.3, "max_tokens": 1500,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": [
                                {"type": "image_url", "image_url": {"url": item["_url"]}},
                                {"type": "text", "text": kb}]}]}
    last_err = "unknown"
    async with sem:
        for attempt in range(3):
            try:
                to = aiohttp.ClientTimeout(total=300)
                async with session.post(f"{BASE}/chat/completions", json=payload, timeout=to,
                                        headers={"Authorization": f"Bearer {KEY}"}) as resp:
                    body = await resp.json()
                    if resp.status == 200 and "choices" in body:
                        cap = (body["choices"][0]["message"].get("content") or "").strip()
                        if len(cap) >= 120:
                            rec = {"sha256": item["sha256"], "instances": inst_names,
                                   "width": item["width"], "height": item["height"],
                                   "caption_len": len(cap), "detail_caption": cap, "ts": int(time.time())}
                            async with lock:
                                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                                out_f.flush()
                                stats["ok"] += 1
                                if stats["ok"] % 200 == 0:
                                    print(f"[caption] 进度 ok={stats['ok']}/{stats['total']} fail={stats['fail']}", flush=True)
                            return
                        last_err = f"short_caption:{len(cap)}"
                    elif "insufficient_quota" in json.dumps(body):
                        raise QuotaExhausted(body.get("error", {}).get("message", "quota"))
                    else:
                        last_err = str(body)[:200]
            except QuotaExhausted:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {str(e)[:150]}"
            await asyncio.sleep(5 * (attempt + 1))
    stats["fail"] += 1
    print(f"[caption] FAIL {item['sha256'][:12]}: {last_err}", flush=True)


async def main_run(limit, conc):
    focus = {i["name"]: i for i in json.load(open(FOCUS_F))["instances"]}
    targets = load_targets()
    done = set()
    if OUT_F.exists():
        for line in OUT_F.read_text().splitlines():
            try:
                done.add(json.loads(line)["sha256"])
            except Exception:  # noqa: BLE001
                pass
    jobs = [t for t in targets if t["sha256"] not in done]
    if limit:
        jobs = jobs[:limit]
    print(f"[caption] 待处理 {len(jobs)}（已完成 {len(done)} / 目标池 {len(targets)}）", flush=True)
    # 预取 data URL 在 job 内做（避免 18k 张同时驻留内存）
    sem = asyncio.Semaphore(conc)
    lock = asyncio.Lock()
    stats = {"ok": 0, "fail": 0, "total": len(jobs)}
    out_f = OUT_F.open("a", encoding="utf-8")

    async def wrapped(item):
        try:
            url, nbytes = data_url(item["sha256"], item["ext"])
        except FileNotFoundError:
            stats["fail"] += 1
            print(f"[caption] MISS blob {item['sha256'][:12]}", flush=True)
            return
        if nbytes > 12 * 1024 * 1024:
            stats["fail"] += 1
            print(f"[caption] SKIP big {item['sha256'][:12]} {nbytes}", flush=True)
            return
        item["_url"] = url
        await caption_one(session, sem, item, focus, out_f, lock, stats)

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            try:
                # 分批 500，控制 base64 内存占用
                for i in range(0, len(jobs), 500):
                    await asyncio.gather(*(wrapped(t) for t in jobs[i:i + 500]))
            except QuotaExhausted as e:
                print(f"[caption] 配额耗尽，熔断：{e}", flush=True)
    finally:
        out_f.close()
    print(f"[caption] 完成 ok={stats['ok']} fail={stats['fail']}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--conc", type=int, default=20)
    args = ap.parse_args()
    asyncio.run(main_run(args.limit, min(args.conc, 32)))


if __name__ == "__main__":
    main()
