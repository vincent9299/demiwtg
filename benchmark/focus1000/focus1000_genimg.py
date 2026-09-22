#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focus1000 复杂图片生成（任务3）：V6.0 出题协议改编 → qwen-image-3.0-pro 出图。

两段式：
  1) prompts 子命令：对 focus1000 每实例生成 2 条复杂作画提示词（设计维度
     随机采样自 V6.0 菜单：场景复杂度来源/前提/组合/跳/知识类别，L2~L3 复杂度），
     LLM=qianwen1 qwen3.8-max；
  2) images 子命令：逐条调 qwen-image-3.0-pro（qianwen1 直连 chat 生图），
     随机画幅（1:1/3:4/4:3/9:16/16:9），下载 OSS URL 落盘 + 记录 sha256/尺寸。

产物（均不入 git）：
  data/focus1000/gen_prompts.jsonl   # {prompt_id, instance, variant, dims, gen_prompt, ...}
  data/focus1000/gen_results.jsonl   # {prompt_id, instance, ratio, file, sha256, width, height, ts}
  data/focus1000/gen_imgs/*.png

断点续跑：两阶段均按 prompt_id 跳过已完成行；配额耗尽(insufficient_quota)
自动熔断退出。用法：
  python3 focus1000_genimg.py prompts [--limit N]
  python3 focus1000_genimg.py images [--limit N] [--conc 8]
  python3 focus1000_genimg.py all
"""
from __future__ import annotations

import argparse
import asyncio
import aiohttp
import hashlib
import io
import json
import os
import random
import re
import struct
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"
IMGS_DIR = DATA_DIR / "gen_imgs"
PROMPTS_F = DATA_DIR / "gen_prompts.jsonl"
RESULTS_F = DATA_DIR / "gen_results.jsonl"
FOCUS_F = REPO_ROOT / "collect" / "records" / "focus1000_instances.json"
DOCS_DRAFT = REPO_ROOT / "collect" / "records" / "concepts_docs_draft.jsonl"
TAXONOMY_F = REPO_ROOT / "datasets" / "demiwtg" / "meta" / "taxonomy.json"
V60_PROMPT_F = Path("/yzp/zhaozy/yangzepeng/0905/demiwtg/benchmark/t2i/synthesize_prompt_gen_v6.0.md")
ENV_F = REPO_ROOT / "modelhub" / ".env"

TEXT_MODEL = "qwen3.8-max"
IMG_MODEL = "qwen-image-3.0-pro"
VARIANTS_PER_INSTANCE = 2

# 画幅网格（qwen-image-3.0-pro 原生 2752/1536 档；size 参数作提示、画幅以题面文字为准）
RATIOS = {
    "1:1": ("1328*1328", "正方形 1:1 画幅"),
    "3:4": ("1104*1472", "竖幅 3:4（高大于宽）"),
    "4:3": ("1472*1104", "横幅 4:3（宽大于高）"),
    "9:16": ("864*1536", "竖幅 9:16（明显高瘦）"),
    "16:9": ("1536*864", "横幅 16:9（明显宽扁）"),
}
RATIO_KEYS = list(RATIOS)

SCENE_TYPES = ["实体密度", "细节密度", "交互链", "过程时刻", "环境作用", "视点剖示",
               "规约场景", "多实例对比", "纵深层次", "光照时段", "动态要素", "多人物编排"]
PREMISE_TYPES = ["阶段/时刻", "年龄/生长阶段", "变体/子类型", "使用状态", "数量/编组",
                 "环境场所", "视角/暴露", "事件/典故语境", "时间/历史语境", "反事实假设"]
COMBO_TYPES = ["光学媒介", "接触交互", "容纳承载", "尺度并置", "因果", "多实例",
               "文化语境", "光影投影", "装配咬合", "运动瞬间", "光学传播", "生态互动"]
HOP_TYPES = ["过程-因果", "规约-标准", "发育-生长", "功能-结构", "物理规律", "文化-规制",
             "关系（组合）", "分类-辨识", "量-守恒", "序-时序", "原理-推演", "化学-反应", "生态-互动"]
KNOW_CATS = ["概念自身结构", "状态与阶段", "环境交互", "可组合关系", "规约与典故",
             "物理规律", "化学规律", "生物规律", "地理气象", "天文对应"]


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


# ---------- 通用 HTTP ----------

async def _post_json(session, url, payload, timeout):
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    to = aiohttp.ClientTimeout(total=timeout)
    async with session.post(url, json=payload, headers=headers, timeout=to) as resp:
        text = await resp.text()
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"_status": resp.status, "_text": text[:300]}
        if resp.status == 429 and "insufficient_quota" in text:
            raise QuotaExhausted("token-plan quota exhausted (429)")
        return resp.status, body


class QuotaExhausted(Exception):
    pass


# ---------- 阶段一：gen_prompt 合成 ----------

def v60_menu_section() -> str:
    text = V60_PROMPT_F.read_text(encoding="utf-8")
    m = re.search(r"(## 二、设计维度菜单.*?)\n## 三、", text, re.S)
    return m.group(1).strip() if m else ""


SYSTEM_PROMPT = """你是文生图评测的复杂场景出题人（改编自 t2i v6.0 出题协议——此处不产出评测题，只产出一段发给文生图模型的作画提示词）。 \
任务：基于给定概念与随机指派的设计维度，写一段**知识密集、约束复杂**的中文作画提示词（gen_prompt）。

要求：
1. gen_prompt 是一段语法语义通顺、容易理解的连续中文（不压词、不堆砌名词、不分点），像给画师的完整 Auftrag。
2. 画面必须以概念为视觉主体之一（概念蕴含的世界知识必须画对），并自然融入所指派维度引出的其他元素/关系/状态——所有组合必须有真实视觉交汇，不得为凑难度硬凑；确实无法支撑某指派维度时，可自然替换为同表其他维度并在 notes 说明。
3. 蕴含至少 3 条多跳视觉结论（概念知识点+前提+组合=画面上可核验的视觉事实），把最终视觉事实直接写进题面（读者无需推理即可知道该画成什么样）。
4. 复杂度对标 L2~L3：前提密度 4+ 个原子前提、多主体多层约束；但题面保持流畅可读。
5. 不出现渲染长文字/水印的要求；不出现违背物理或不可能画的内容；不使用"镜面反射"类光学媒介维度除非被显式指派。
6. 画风默认具象写实，除非概念自然属于插画/标志/艺术风格类。

只输出一个 JSON 对象（不要 markdown 代码块）：
{"gen_prompt": "...", "level": "L2或L3", "combo_type": "...", "scene_types": [...], "premise_types": [...], "hop_types": [...], "knowledge_categories": [...], "key_visual_conclusions": ["...", "..."], "notes": "维度替换或舍弃说明，无则空串"}"""


def sample_dims(rng: random.Random) -> dict:
    return {
        "combo_type": rng.choice(COMBO_TYPES),
        "scene_types": rng.sample(SCENE_TYPES, k=rng.choice([2, 3, 3, 4])),
        "premise_types": rng.sample(PREMISE_TYPES, k=rng.choice([1, 2, 2, 3])),
        "hop_types": rng.sample(HOP_TYPES, k=rng.choice([2, 3, 3])),
        "knowledge_categories": rng.sample(KNOW_CATS, k=rng.choice([2, 3, 3])),
    }


def parse_json_obj(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


async def gen_one_prompt(session, sem, inst, mount_paths, variant, rng):
    dims = sample_dims(rng)
    dim_str = json.dumps(dims, ensure_ascii=False)
    ctx = [f"概念名：{inst['name']}"]
    al = inst.get("aliases") or []
    if isinstance(al, str):
        try:
            al = json.loads(al)
        except json.JSONDecodeError:
            try:
                import ast
                al = ast.literal_eval(al)
            except Exception:  # noqa: BLE001
                al = []
    if al:
        ctx.append(f"别名：{'、'.join([str(a) for a in al[:6]])}")
    ctx.append(f"概念介绍：{inst.get('_docs') or '（无）'}")
    if mount_paths:
        ctx.append(f"分类路径（首段为主概念域）：{mount_paths[0]}" +
                   (f"（另有 {len(mount_paths)-1} 条挂载路径）" if len(mount_paths) > 1 else ""))
    ctx.append(f"本画指派设计维度（随机采样，供你围绕设计）：{dim_str}")
    user = "\n".join(ctx)
    payload = {"model": TEXT_MODEL, "temperature": 0.9, "max_tokens": 4096,
               "enable_thinking": False,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user}]}
    async with sem:
        for attempt in range(3):
            try:
                status, body = await _post_json(session, f"{BASE}/chat/completions", payload, 240)
                if status == 200 and "choices" in body:
                    out = parse_json_obj(body["choices"][0]["message"]["content"] or "")
                    if out and out.get("gen_prompt"):
                        out.update({"prompt_id": f"{inst['name']}#{variant}", "instance": inst["name"],
                                    "variant": variant, "dims_assigned": dims})
                        return out
                    last_err = "parse_fail"
                elif "insufficient_quota" in json.dumps(body):
                    raise QuotaExhausted(body.get("error", {}).get("message", "quota"))
                else:
                    last_err = str(body)[:200]
            except QuotaExhausted:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
            await asyncio.sleep(5 * (attempt + 1))
    return {"prompt_id": f"{inst['name']}#{variant}", "instance": inst["name"], "variant": variant,
            "error": last_err}


async def run_prompts(limit, conc):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    doc = json.load(open(FOCUS_F))
    instances = doc.get("concepts") or doc.get("instances") or []
    docs = {}
    if DOCS_DRAFT.exists():
        with DOCS_DRAFT.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    docs[r["name"]] = r.get("body") or ""
    for i in instances:
        i["_docs"] = docs.get(i["name"], "")
    mounts = {item["name"]: item.get("taxonomy", []) for item in instances}
    done = set()
    if PROMPTS_F.exists():
        for line in PROMPTS_F.read_text().splitlines():
            try:
                done.add(json.loads(line)["prompt_id"])
            except Exception:  # noqa: BLE001
                pass
    jobs = [(i, mounts.get(i["name"], []), v) for i in instances for v in range(VARIANTS_PER_INSTANCE)
            if f"{i['name']}#{v}" not in done]
    if limit:
        jobs = jobs[:limit]
    print(f"[prompts] 待生成 {len(jobs)}（已完成 {len(done)}）", flush=True)
    sem = asyncio.Semaphore(conc)
    ok = fail = 0
    import aiohttp  # 延迟 import，prompts 阶段才需要
    out = PROMPTS_F.open("a", encoding="utf-8")
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:

            async def wrapped(inst, mp, v):
                nonlocal ok, fail
                rng = random.Random(f"{inst['name']}#{v}#20260902")
                try:
                    r = await gen_one_prompt(session, sem, inst, mp, v, rng)
                except QuotaExhausted as e:
                    print(f"[prompts] 配额耗尽，熔断：{e}", flush=True)
                    raise
                if r.get("error"):
                    fail += 1
                    print(f"[prompts] FAIL {r['prompt_id']}: {r['error']}", flush=True)
                else:
                    ok += 1
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    out.flush()
                    if ok % 50 == 0:
                        print(f"[prompts] 进度 ok={ok} fail={fail}", flush=True)

            try:
                await asyncio.gather(*(wrapped(*j) for j in jobs))
            except QuotaExhausted:
                return
    finally:
        out.close()
    print(f"[prompts] 完成 ok={ok} fail={fail}", flush=True)


# ---------- 阶段二：生图 ----------

def png_dims(data: bytes):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    return None, None


def safe_name(name: str) -> str:
    return re.sub(r"[/\\:*?\"<>|\s]+", "_", name)[:60]


async def download(session, url, timeout=180):
    to = aiohttp.ClientTimeout(total=timeout)
    async with session.get(url, timeout=to) as resp:
        resp.raise_for_status()
        return await resp.read()


async def gen_one_image(session, sem, row, seq, rng, results_lock, out_f):
    pid = row["prompt_id"]
    ratio = rng.choice(RATIO_KEYS)
    size_param, ratio_desc = RATIOS[ratio]
    prompt_text = row["gen_prompt"].rstrip("。") + f"。画面为{ratio_desc}。"
    payload = {"model": IMG_MODEL, "size": size_param,
               "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}]}]}
    last_err = "unknown"
    async with sem:
        for attempt in range(3):
            try:
                status, body = await _post_json(session, f"{BASE}/chat/completions", payload, 600)
                blob = json.dumps(body, ensure_ascii=False)
                if status == 200:
                    ch = (body.get("output") or {}).get("choices") or []
                    content = ch[0]["message"]["content"] if ch else None
                    url = next((c["image"] for c in content
                                if isinstance(c, dict) and c.get("image")), None) if content else None
                    if url:
                        data = await download(session, url)
                        w, h = png_dims(data)
                        sha = hashlib.sha256(data).hexdigest()
                        fname = f"{seq:05d}_{safe_name(row['instance'])}_v{row['variant']}_{ratio.replace(':', 'x')}.png"
                        fpath = IMGS_DIR / fname
                        fpath.write_bytes(data)
                        rec = {"prompt_id": pid, "instance": row["instance"], "variant": row["variant"],
                               "ratio": ratio, "size_param": size_param, "file": str(fpath.relative_to(DATA_DIR)),
                               "sha256": sha, "width": w, "height": h, "bytes": len(data), "ts": int(time.time())}
                        async with results_lock:
                            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                            out_f.flush()
                        return True
                    last_err = f"no_image:{blob[:150]}"
                elif "insufficient_quota" in blob:
                    raise QuotaExhausted(body.get("error", {}).get("message", "quota"))
                else:
                    last_err = blob[:200]
            except QuotaExhausted:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {str(e)[:150]}"
            await asyncio.sleep(10 * (attempt + 1))
    print(f"[images] FAIL {pid}: {last_err}", flush=True)
    async with results_lock:
        out_f.write(json.dumps({"prompt_id": pid, "instance": row["instance"], "variant": row["variant"],
                                "error": last_err, "ts": int(time.time())}, ensure_ascii=False) + "\n")
        out_f.flush()
    return False


async def run_images(limit, conc):
    IMGS_DIR.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in PROMPTS_F.read_text().splitlines()] if PROMPTS_F.exists() else []
    done_ok, done_all = set(), set()
    if RESULTS_F.exists():
        for line in RESULTS_F.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            done_all.add(r["prompt_id"])
            if not r.get("error"):
                done_ok.add(r["prompt_id"])
    # 重试历史失败行（保留成功行跳过）
    jobs = [r for r in rows if r.get("gen_prompt") and r["prompt_id"] not in done_ok]
    if limit:
        jobs = jobs[:limit]
    print(f"[images] 待生图 {len(jobs)}（成功 {len(done_ok)} / prompts {len(rows)}）", flush=True)
    sem = asyncio.Semaphore(conc)
    results_lock = asyncio.Lock()
    ok = 0
    import aiohttp
    out_f = RESULTS_F.open("a", encoding="utf-8")
    counters = {}

    async def wrapped(row):
        nonlocal ok
        i = counters.get(row["prompt_id"], len(counters))
        counters[row["prompt_id"]] = i
        rng = random.Random(f"{row['prompt_id']}#img#20260902")
        if await gen_one_image(session, sem, row, i, rng, results_lock, out_f):
            ok += 1
            if ok % 25 == 0:
                print(f"[images] 进度 ok={ok}/{len(jobs)}", flush=True)

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            try:
                await asyncio.gather(*(wrapped(r) for r in jobs))
            except QuotaExhausted as e:
                print(f"[images] 配额耗尽，熔断：{e}", flush=True)
                return
    finally:
        out_f.close()
    print(f"[images] 完成 ok={ok}/{len(jobs)}", flush=True)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prompts", "images", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--conc", type=int, default=8)
    args = ap.parse_args()
    if args.cmd in ("prompts", "all"):
        asyncio.run(run_prompts(args.limit, min(args.conc, 12)))
    if args.cmd in ("images", "all"):
        asyncio.run(run_images(args.limit, min(args.conc, 10)))


if __name__ == "__main__":
    main()
