#!/usr/bin/env python3
"""通过 OpenAI-compatible chat 图像接口批量执行 edit 题。

模型只接收原图字节与 ``edit_instruction``；不会泄漏 reasoning、evidence、level、
caption 或实体知识。默认复用项目此前 T2I 跑通的 Gemini 强模型路由。

示例：
    .venv/bin/python benchmark/edit/eval_edit_gen.py \
      --questions benchmark/edit/synth_v61_pilot/questions.jsonl \
      --out-dir benchmark/edit/synth_v61_pilot/gemini
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import io
import json
import re
import time
from pathlib import Path

import requests
from PIL import Image

SUB_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "openrouter/google/gemini-3.1-flash-image"
DEFAULT_ENDPOINT = "http://127.0.0.1:4001/v1/chat/completions"
ASPECTS = {"1:1": 1.0, "3:4": 3 / 4, "4:3": 4 / 3, "9:16": 9 / 16,
           "16:9": 16 / 9, "2:3": 2 / 3, "3:2": 3 / 2}
ALLOWED_SOURCE_ROOT = (SUB_DIR / "data" / "focus200").resolve()
RUNNER_SCHEMA = "edit-gen-v1"
QID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: JSONL 损坏：{exc}") from exc
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def instruction_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_source(q: dict, questions: Path) -> Path:
    rel = q.get("_sample_image")
    if not rel and q.get("_file"):
        rel = f"focus200/{q['_file']}"
    if not rel:
        raise ValueError("题目缺 _sample_image/_file")
    src = Path(rel)
    candidates = [src] if src.is_absolute() else [
        questions.parent / src, questions.parent.parent / src, SUB_DIR / "data" / src
    ]
    for cand in candidates:
        if not cand.exists():
            continue
        resolved = cand.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(ALLOWED_SOURCE_ROOT):
            raise ValueError(f"源图必须是 {ALLOWED_SOURCE_ROOT} 内的普通文件：{rel}")
        return resolved
    raise FileNotFoundError(f"源图不存在：{rel}")


def closest_aspect(path: Path) -> str:
    with Image.open(path) as im:
        ratio = im.width / im.height
    return min(ASPECTS, key=lambda key: abs(ASPECTS[key] - ratio))


def data_url(path: Path) -> tuple[str, bytes]:
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        image_format = str(im.format or "").upper()
        im.verify()
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
        image_format)
    if not mime:
        raise ValueError(f"不支持的源图格式：{image_format or 'unknown'}")
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), raw


def image_urls(message: dict) -> list[str]:
    urls = []
    for image in message.get("images") or []:
        field = image.get("image_url") if isinstance(image, dict) else None
        url = field.get("url") if isinstance(field, dict) else field
        if url:
            urls.append(url)
    content = message.get("content")
    for part in content if isinstance(content, list) else []:
        field = part.get("image_url") if isinstance(part, dict) else None
        url = field.get("url") if isinstance(field, dict) else field
        if url:
            urls.append(url)
    return urls


def decode_image(url: str) -> bytes:
    """只接受内联图片，避免跟随模型返回的任意 URL 访问内网或其他主机。"""
    if not url.startswith("data:image/") or ";base64," not in url:
        raise ValueError("回包图片必须是 data:image/...;base64 内联数据")
    return base64.b64decode(url.split(",", 1)[1], validate=True)


def prepare_request(args, src: Path, instruction: str) -> tuple[dict, bytes, str]:
    encoded, source_raw = data_url(src)
    aspect = closest_aspect(src)
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": encoded}},
            {"type": "text", "text": instruction},
        ]}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": aspect},
    }
    fingerprint_payload = {
        "runner_schema": RUNNER_SCHEMA,
        "endpoint": args.endpoint,
        "model": args.model,
        "source_sha256": sha256_bytes(source_raw),
        "instruction_sha256": instruction_sha(instruction),
        "content_order": ["image_url", "text"],
        "modalities": payload["modalities"],
        "image_config": payload["image_config"],
    }
    fingerprint = sha256_bytes(json.dumps(
        fingerprint_payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))
    return payload, source_raw, fingerprint


def call_one(session: requests.Session, args, payload: dict, source_raw: bytes,
             instruction: str, fingerprint: str) -> tuple[bytes, dict]:
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    last_error = ""
    for attempt in range(args.retries):
        try:
            resp = session.post(args.endpoint, json=payload, headers=headers,
                                timeout=args.timeout)
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            obj = resp.json()
            msg = (obj.get("choices") or [{}])[0].get("message") or {}
            urls = image_urls(msg)
            if not urls:
                raise RuntimeError(f"回包无 image：{resp.text[:500]}")
            data = decode_image(urls[0])
            with Image.open(io.BytesIO(data)) as im:
                width, height, image_format = im.width, im.height, str(im.format or "").upper()
                im.verify()
            if image_format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError(f"不支持的结果图格式：{image_format or 'unknown'}")
            meta = {
                "runner_schema": RUNNER_SCHEMA,
                "request_fingerprint": fingerprint,
                "request_model": args.model,
                "response_model": obj.get("model"),
                "source_sha256": sha256_bytes(source_raw),
                "instruction_sha256": instruction_sha(instruction),
                "aspect_ratio_request": payload["image_config"]["aspect_ratio"],
                "width": width, "height": height, "format": image_format,
                "usage": obj.get("usage") or {},
            }
            return data, meta
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < args.retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(last_error)


def safe_model_name(model: str) -> str:
    tail = re.sub(r"[^A-Za-z0-9._-]+", "_", model.split("/")[-1])
    return f"{tail}_{sha256_bytes(model.encode('utf-8'))[:8]}"


def write_jsonl_atomic(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text("".join(json.dumps(rows[k], ensure_ascii=False) + "\n"
                           for k in sorted(rows)), encoding="utf-8")
    tmp.replace(path)


def persist_manifests(lock_path: Path, responses_path: Path, errors_path: Path,
                      done: dict[str, dict], errors: dict[str, dict]) -> None:
    """跨进程合并后原子写，避免并行分片互相覆盖断点。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        disk_done = ({r["qid"]: r for r in read_jsonl(responses_path)}
                     if responses_path.exists() else {})
        disk_errors = ({r["qid"]: r for r in read_jsonl(errors_path)}
                       if errors_path.exists() else {})
        disk_done.update(done)
        disk_errors.update(errors)
        for qid in disk_done:
            disk_errors.pop(qid, None)
        write_jsonl_atomic(responses_path, disk_done)
        write_jsonl_atomic(errors_path, disk_errors)
        done.clear()
        done.update(disk_done)
        errors.clear()
        errors.update(disk_errors)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--qids", default="",
                    help="可选逗号分隔 qid 子集；先按题库顺序过滤，再应用 --limit")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    rows = [q for q in read_jsonl(args.questions)
            if q.get("task") == "edit" and q.get("status", "constructed") == "constructed"]
    if args.qids:
        wanted = {x.strip() for x in args.qids.split(",") if x.strip()}
        present = {str(q.get("qid") or "") for q in rows}
        unknown = sorted(wanted - present)
        if unknown:
            raise SystemExit(f"--qids 不在题库中：{unknown}")
        rows = [q for q in rows if str(q.get("qid") or "") in wanted]
    if args.limit:
        rows = rows[:args.limit]
    qids = [str(q.get("qid") or "") for q in rows]
    if (not rows or any(not QID_RE.fullmatch(qid) for qid in qids)
            or len(qids) != len(set(qids))):
        raise SystemExit("题库为空、qid 不安全/缺失或 qid 重复")

    model_name = safe_model_name(args.model)
    img_dir = args.out_dir / "imgs" / model_name
    img_dir.mkdir(parents=True, exist_ok=True)
    responses_path = args.out_dir / f"responses_{model_name}.jsonl"
    errors_path = args.out_dir / f"errors_{model_name}.jsonl"
    manifest_lock = args.out_dir / f".{model_name}.manifest.lock"
    done = {r["qid"]: r for r in read_jsonl(responses_path)} if responses_path.exists() else {}
    errors = {r["qid"]: r for r in read_jsonl(errors_path)} if errors_path.exists() else {}
    session = requests.Session()
    print(f"[edit-gen] n={len(rows)} model={args.model} endpoint={args.endpoint}")

    for index, q in enumerate(rows, 1):
        qid = q["qid"]
        t0 = time.time()
        try:
            src = resolve_source(q, args.questions)
            instruction = str(q.get("edit_instruction") or "").strip()
            if not instruction:
                raise ValueError("edit_instruction 为空")
            payload, source_raw, fingerprint = prepare_request(args, src, instruction)
            prior = done.get(qid)
            if prior and prior.get("ok") and prior.get("request_fingerprint") == fingerprint:
                prior_path = (args.out_dir / str(prior.get("image") or "")).resolve()
                if (prior_path.is_file()
                        and prior_path.is_relative_to(args.out_dir.resolve())):
                    errors.pop(qid, None)
                    persist_manifests(manifest_lock, responses_path, errors_path,
                                      done, errors)
                    print(f"[{index}/{len(rows)}] {qid} skip（fingerprint 一致）")
                    continue
            data, meta = call_one(session, args, payload, source_raw, instruction, fingerprint)
            width, height, image_format = meta["width"], meta["height"], meta["format"]
            # 按原生字节真实格式保存；绝不转码。
            suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image_format]
            out = img_dir / f"{qid}{suffix}"
            tmp = out.with_suffix(out.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(out)
            row = {
                "qid": qid, "task": "edit", "ok": True,
                "image": out.relative_to(args.out_dir).as_posix(),
                "input_role": "edit_target", "model": args.model,
                "seconds": round(time.time() - t0, 1),
                "output_sha256": sha256_bytes(data), **meta,
            }
            done[qid] = row
            errors.pop(qid, None)
            print(f"[{index}/{len(rows)}] {qid} ok {width}x{height} "
                  f"{len(data) // 1024}KiB {row['seconds']}s")
        except Exception as exc:  # noqa: BLE001
            errors[qid] = {"qid": qid, "task": "edit", "ok": False,
                           "model": args.model, "seconds": round(time.time() - t0, 1),
                           "error": f"{type(exc).__name__}: {exc}"}
            print(f"[{index}/{len(rows)}] {qid} FAIL {errors[qid]['error'][:300]}")
        persist_manifests(manifest_lock, responses_path, errors_path, done, errors)

    print(f"responses -> {responses_path}\nerrors -> {errors_path}")


if __name__ == "__main__":
    main()
