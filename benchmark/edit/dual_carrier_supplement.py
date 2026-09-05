#!/usr/bin/env python3
"""Generate dual-carrier source-image supplements for the focus200 edit set.

Stages are resumable by prompt_id:
  needed  - derive instances without an audited >=2-carrier image
  prompts - generate and mechanically validate one prompt per needed instance
  images  - generate images, append result records and manifest records
  all     - run all three stages
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import struct
import time
from pathlib import Path

import aiohttp


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE    # edit 无 data/ 层：focus200 与活素材落子模块根
FOCUS = DATA / "focus200"
IMAGES = FOCUS / "images"
NEEDED = FOCUS / "supplement_needed.json"
PROMPTS = FOCUS / "supplement_prompts.jsonl"
RESULTS = FOCUS / "supplement_results.jsonl"
MANIFEST = FOCUS / "manifest.jsonl"
AUDIT = DATA / "complexity_audit_synth.jsonl"
BENCH = ROOT / "state" / "collect" / "focus_bench_v1.json"
INSTANCES = ROOT / "datasets" / "demiwtg" / "meta" / "instances.json"
ENV_FILE = ROOT / "modelhub" / ".env"

TEXT_MODEL = "qwen3.8-max"
IMAGE_MODEL = "qwen-image-3.0-pro"
CARRIERS = {"水面倒影", "镜面玻璃反光", "影子投影", "接触叠放", "液体容器", "仪表指示", "可动机构"}
RATIOS = {
    "1:1": ("1328*1328", "正方形 1:1 画幅"),
    "3:4": ("1104*1472", "竖幅 3:4（高大于宽）"),
    "4:3": ("1472*1104", "横幅 4:3（宽大于高）"),
    "9:16": ("864*1536", "竖幅 9:16（明显高瘦）"),
    "16:9": ("1536*864", "横幅 16:9（明显宽扁）"),
}

SYSTEM_PROMPT = """你是图像编辑评测集的源图设计师。根据给定实体及其知识介绍，输出一段中文、具象写实、可直接交给文生图模型的完整连续作画提示词。目标是让源图天然支持高难度局部编辑。

硬性要求：
1. 从以下封闭枚举中选择至少两类、且与实体自然适配的后果载体：水面倒影、镜面玻璃反光、影子投影、接触叠放、液体容器、仪表指示、可动机构。不自洽就换组合，禁止硬塞。每个所选载体必须在 gen_prompt 中逐字点名，并明确其当前在画内清楚可辨；倒影须有可辨映像，反光须有可辨映像，影子须方向与归属清楚，液体容器须容器和液面均可见，仪表须表盘或屏幕指示可辨，可动机构须关节或活动部件可见。
2. 主体实体画出同类 2至4 个，并有肉眼可见的状态、阶段或变体差异，便于指定其中一个。
3. 另设 2至3 个与主体不同类、显著独立、位置清楚的参照物。
4. 至少满足一项保持矩阵：画内有3个以上可辨对象，或明确前景、中景、背景三层纵深。
5. 点名全部设计元素及其相对位置，知识事实准确，组合有真实视觉交汇，结尾明确“画面中不出现文字、字幕、标识或水印”。不要让模型绘制解释性标签。

只输出 JSON 对象，不要代码围栏：
{"gen_prompt":"...","carriers_required":["枚举值1","枚举值2"],"checklist":{"same_class_count":2,"visible_variants":["..."],"referents":["...","..."],"entity_density":true,"three_depth_layers":false,"all_elements_named":true,"no_text_watermark":true},"notes":"适配理由"}"""


class QuotaExhausted(Exception):
    pass


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_env() -> tuple[str, str]:
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env["QIANWEN1_API_BASE"].rstrip("/"), env["QIANWEN1_API_KEY"]


def derive_needed() -> list[str]:
    names = json.loads(BENCH.read_text(encoding="utf-8"))
    audited = [row for row in read_jsonl(AUDIT) if not row.get("error")]
    covered = {row["instance"] for row in audited if len(row.get("consequence_carriers") or []) >= 2}
    missing = [name for name in names if name not in covered]
    payload = {"n": len(missing), "instances": missing, "basis": "audit@20260902"}
    FOCUS.mkdir(parents=True, exist_ok=True)
    NEEDED.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[needed] covered={len(names) - len(missing)}/{len(names)} needed={len(missing)} -> {NEEDED}")
    return missing


def parse_object(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    left, right = text.find("{"), text.rfind("}")
    if left < 0 or right <= left:
        return None
    try:
        value = json.loads(text[left:right + 1])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def validate_prompt(obj: dict) -> list[str]:
    errors = []
    prompt = obj.get("gen_prompt")
    carriers = obj.get("carriers_required")
    checklist = obj.get("checklist")
    if not isinstance(prompt, str) or len(prompt) < 180:
        errors.append("gen_prompt过短或缺失")
    if not isinstance(carriers, list) or len(set(carriers)) < 2 or not set(carriers or []).issubset(CARRIERS):
        errors.append("载体须为封闭枚举中至少两个不同值")
    elif any(carrier not in prompt for carrier in set(carriers)):
        errors.append("gen_prompt未逐字点名全部载体")
    if not isinstance(checklist, dict):
        return errors + ["checklist缺失"]
    count = checklist.get("same_class_count")
    if not isinstance(count, int) or not 2 <= count <= 4:
        errors.append("同类主体数量不在2至4")
    if not isinstance(checklist.get("visible_variants"), list) or not checklist["visible_variants"]:
        errors.append("缺少同类可见差异")
    refs = checklist.get("referents")
    if not isinstance(refs, list) or not 2 <= len(refs) <= 3:
        errors.append("参照物须为2至3个")
    if not (checklist.get("entity_density") is True or checklist.get("three_depth_layers") is True):
        errors.append("保持矩阵未满足")
    if checklist.get("all_elements_named") is not True:
        errors.append("设计元素未确认点名")
    if checklist.get("no_text_watermark") is not True or not all(x in prompt for x in ("文字", "水印")):
        errors.append("缺少文字水印负面约束")
    return errors


async def post_json(session, base: str, key: str, payload: dict, timeout: int) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with session.post(f"{base}/chat/completions", json=payload, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        text = await response.text()
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"_text": text[:500]}
        if response.status == 429 and "insufficient_quota" in text:
            raise QuotaExhausted("token-plan quota exhausted")
        return response.status, body


async def make_prompt(session, sem, base, key, name, desc) -> dict:
    user = f"实体名：{name}\n实体知识介绍：{desc or '暂无详细介绍；仅使用可靠常识，不虚构专名事实。'}"
    last_error = "unknown"
    async with sem:
        for attempt in range(5):
            suffix = "" if attempt == 0 else f"\n上次输出机检失败：{last_error}。请完整重写并修正。"
            payload = {"model": TEXT_MODEL, "temperature": 0.8, "max_tokens": 4096,
                       "enable_thinking": False, "messages": [
                           {"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": user + suffix},
                       ]}
            try:
                status, body = await post_json(session, base, key, payload, 240)
                if status == 200 and body.get("choices"):
                    obj = parse_object(body["choices"][0]["message"].get("content") or "")
                    errors = validate_prompt(obj or {})
                    if obj and not errors:
                        return {"prompt_id": f"{name}#s", "instance": name,
                                "gen_prompt": obj["gen_prompt"],
                                "carriers_required": obj["carriers_required"],
                                "checklist": obj["checklist"]}
                    last_error = "；".join(errors) or "JSON解析失败"
                else:
                    last_error = json.dumps(body, ensure_ascii=False)[:300]
            except QuotaExhausted:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(3 * (attempt + 1))
    return {"prompt_id": f"{name}#s", "instance": name, "error": last_error}


async def run_prompts(limit: int, concurrency: int) -> None:
    needed = json.loads(NEEDED.read_text(encoding="utf-8"))["instances"] if NEEDED.exists() else derive_needed()
    instance_rows = json.loads(INSTANCES.read_text(encoding="utf-8"))["instances"]
    descriptions = {row["name"]: row.get("desc", "") for row in instance_rows}
    done = {row["prompt_id"] for row in read_jsonl(PROMPTS) if not row.get("error")}
    jobs = [name for name in needed if f"{name}#s" not in done]
    if limit:
        jobs = jobs[:limit]
    print(f"[prompts] pending={len(jobs)} done={len(done)}", flush=True)
    base, key = load_env()
    sem, write_lock = asyncio.Semaphore(concurrency), asyncio.Lock()
    ok = failed = 0
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        with PROMPTS.open("a", encoding="utf-8") as output:
            async def one(name: str) -> None:
                nonlocal ok, failed
                result = await make_prompt(session, sem, base, key, name, descriptions.get(name, ""))
                async with write_lock:
                    if result.get("error"):
                        failed += 1
                        print(f"[prompts] FAIL {name}: {result['error']}", flush=True)
                    else:
                        output.write(json.dumps(result, ensure_ascii=False) + "\n")
                        output.flush()
                        ok += 1
                        print(f"[prompts] {ok}/{len(jobs)} {name}", flush=True)
            try:
                await asyncio.gather(*(one(name) for name in jobs))
            except QuotaExhausted as exc:
                print(f"[prompts] quota breaker: {exc}", flush=True)
    print(f"[prompts] complete ok={ok} failed={failed}", flush=True)


def png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    return struct.unpack(">II", data[16:24]) if data[:8] == b"\x89PNG\r\n\x1a\n" else (None, None)


def safe_name(name: str) -> str:
    return re.sub(r"[/\\:*?\"<>|\s]+", "_", name)[:70]


def sequence_map(rows: list[dict]) -> dict[str, int]:
    existing = {}
    used = set()
    for row in read_jsonl(RESULTS):
        if not row.get("error"):
            match = re.match(r"images/(\d{5})_", row.get("file", ""))
            if match:
                existing[row["prompt_id"]] = int(match.group(1))
                used.add(int(match.group(1)))
    for path in IMAGES.glob("*.png"):
        match = re.match(r"(\d{5})_", path.name)
        if match:
            used.add(int(match.group(1)))
    next_seq = 60100
    for row in rows:
        if row["prompt_id"] not in existing:
            while next_seq in used:
                next_seq += 1
            existing[row["prompt_id"]] = next_seq
            used.add(next_seq)
            next_seq += 1
    return existing


def register_builtin_images() -> None:
    """Idempotently register built-in imagegen files already copied into focus200."""
    done_results = {row["prompt_id"] for row in read_jsonl(RESULTS) if not row.get("error")}
    done_manifest = {row["prompt_id"] for row in read_jsonl(MANIFEST)}
    added_results = added_manifest = 0
    with RESULTS.open("a", encoding="utf-8") as results_out, MANIFEST.open("a", encoding="utf-8") as manifest_out:
        for path in sorted(IMAGES.glob("[0-9][0-9][0-9][0-9][0-9]_*_supp.png")):
            match = re.match(r"\d{5}_(.+)_supp\.png$", path.name)
            if not match:
                continue
            instance = match.group(1).replace("_", " ")
            # Prefer the exact focus200 spelling when safe_name introduced underscores.
            names = json.loads(BENCH.read_text(encoding="utf-8"))
            instance = next((name for name in names if safe_name(name) == match.group(1)), instance)
            prompt_id = f"{instance}#s"
            data = path.read_bytes()
            width, height = png_dimensions(data)
            row = {"prompt_id": prompt_id, "instance": instance, "generator": "codex-imagegen",
                   "file": f"images/{path.name}", "sha256": hashlib.sha256(data).hexdigest(),
                   "width": width, "height": height, "ts": int(path.stat().st_mtime)}
            if prompt_id not in done_results:
                results_out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                done_results.add(prompt_id)
                added_results += 1
            if prompt_id not in done_manifest:
                manifest_out.write(json.dumps({**row, "batch": "dual_carrier_supplement"},
                                              ensure_ascii=False, separators=(",", ":")) + "\n")
                done_manifest.add(prompt_id)
                added_manifest += 1
    print(f"[register] results+={added_results} manifest+={added_manifest}")


async def make_image(session, sem, base, key, row, seq) -> tuple[dict | None, str | None]:
    rng = random.Random(f"{row['prompt_id']}#image#20260902")
    ratio = rng.choice(list(RATIOS))
    size, ratio_text = RATIOS[ratio]
    prompt = row["gen_prompt"].rstrip("。") + f"。画面为{ratio_text}。"
    payload = {"model": IMAGE_MODEL, "size": size,
               "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
    last_error = "unknown"
    async with sem:
        for attempt in range(3):
            try:
                status, body = await post_json(session, base, key, payload, 600)
                blob = json.dumps(body, ensure_ascii=False)
                if status == 200:
                    choices = (body.get("output") or {}).get("choices") or []
                    content = choices[0]["message"].get("content") if choices else []
                    url = next((item.get("image") for item in content
                                if isinstance(item, dict) and item.get("image")), None)
                    if url:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as response:
                            response.raise_for_status()
                            data = await response.read()
                        width, height = png_dimensions(data)
                        filename = f"{seq:05d}_{safe_name(row['instance'])}_supp.png"
                        path = IMAGES / filename
                        path.write_bytes(data)
                        return ({"prompt_id": row["prompt_id"], "instance": row["instance"],
                                 "generator": IMAGE_MODEL, "file": f"images/{filename}",
                                 "sha256": hashlib.sha256(data).hexdigest(), "width": width,
                                 "height": height, "ts": int(time.time())}, None)
                    last_error = f"no_image:{blob[:200]}"
                elif "insufficient_quota" in blob:
                    raise QuotaExhausted("image quota exhausted")
                else:
                    last_error = blob[:300]
            except QuotaExhausted:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            await asyncio.sleep(10 * (attempt + 1))
    return None, last_error


async def run_images(limit: int, concurrency: int) -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    rows = [row for row in read_jsonl(PROMPTS) if row.get("gen_prompt")]
    done = {row["prompt_id"] for row in read_jsonl(RESULTS) if not row.get("error")}
    manifested = {row["prompt_id"] for row in read_jsonl(MANIFEST)}
    # Repair a prior interruption between results append and manifest append.
    missing_manifest = [row for row in read_jsonl(RESULTS)
                        if not row.get("error") and row["prompt_id"] not in manifested]
    if missing_manifest:
        with MANIFEST.open("a", encoding="utf-8") as output:
            for row in missing_manifest:
                output.write(json.dumps({**row, "batch": "dual_carrier_supplement"}, ensure_ascii=False) + "\n")
                manifested.add(row["prompt_id"])
    jobs = [row for row in rows if row["prompt_id"] not in done]
    if limit:
        jobs = jobs[:limit]
    seqs = sequence_map(rows)
    print(f"[images] pending={len(jobs)} done={len(done)}", flush=True)
    base, key = load_env()
    sem, write_lock = asyncio.Semaphore(concurrency), asyncio.Lock()
    ok = failed = 0
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        with RESULTS.open("a", encoding="utf-8") as results_out, MANIFEST.open("a", encoding="utf-8") as manifest_out:
            async def one(row: dict) -> None:
                nonlocal ok, failed
                result, error = await make_image(session, sem, base, key, row, seqs[row["prompt_id"]])
                async with write_lock:
                    if result:
                        results_out.write(json.dumps(result, ensure_ascii=False) + "\n")
                        results_out.flush()
                        manifest_out.write(json.dumps({**result, "batch": "dual_carrier_supplement"}, ensure_ascii=False) + "\n")
                        manifest_out.flush()
                        ok += 1
                        print(f"[images] {ok}/{len(jobs)} {row['instance']}", flush=True)
                    else:
                        failed += 1
                        print(f"[images] FAIL {row['instance']}: {error}", flush=True)
            try:
                await asyncio.gather(*(one(row) for row in jobs))
            except QuotaExhausted as exc:
                print(f"[images] quota breaker: {exc}", flush=True)
    print(f"[images] complete ok={ok} failed={failed}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["needed", "prompts", "images", "register", "all"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--conc", type=int, default=8)
    args = parser.parse_args()
    if args.cmd in {"needed", "all"}:
        derive_needed()
    if args.cmd in {"prompts", "all"}:
        asyncio.run(run_prompts(args.limit, min(args.conc, 12)))
    if args.cmd in {"images", "all"}:
        asyncio.run(run_images(args.limit, min(args.conc, 10)))
    if args.cmd == "register":
        register_builtin_images()


if __name__ == "__main__":
    main()
