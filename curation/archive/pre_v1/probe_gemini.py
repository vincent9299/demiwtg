"""Small, append-only Gemini T2I development probe through the local modelhub.

Example (paid generation; requires session authorization):
  python -m curation.probe_gemini --questions state/curation/probe/questions.jsonl \
      --out-dir state/curation/probe/gemini --limit 6

Each selected qid gets at most one HTTP attempt in this output directory. An
interrupted attempt is retained and skipped on resume, since it may be billed.
Only inline image responses are materialized; remote URLs remain in raw JSON.
"""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))

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

MODEL = "openrouter/google/gemini-3.1-flash-image"
ENDPOINT = "http://127.0.0.1:4001/v1/chat/completions"
SCHEMA = "curation-gemini-t2i-probe-v1"
QID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        import os
        os.fsync(handle.fileno())


def bounded_int(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 24:
        raise argparse.ArgumentTypeError("--limit must be between 1 and 24")
    return number


def image_candidates(message: dict) -> list[str]:
    parts = list(message.get("images") or [])
    if isinstance(message.get("content"), list):
        parts.extend(message["content"])
    urls = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        field = part.get("image_url")
        url = field.get("url") if isinstance(field, dict) else field
        if isinstance(url, str):
            urls.append(url)
    return urls


def parse_image(obj: dict) -> tuple[bytes, dict]:
    choices = obj.get("choices") or []
    message = choices[0].get("message") if choices else {}
    urls = image_candidates(message or {})
    inline = [url for url in urls if url.startswith("data:image/") and ";base64," in url]
    if not inline:
        raise ValueError("No inline image; remote URLs are retained in response.json but not fetched")
    raw = base64.b64decode(inline[0].split(",", 1)[1], validate=True)
    with Image.open(io.BytesIO(raw)) as im:
        info = {"width": im.width, "height": im.height, "format": im.format}
        im.verify()
    extensions = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
    if info["format"] not in extensions:
        raise ValueError("Unsupported generated image format")
    info.update(extension=extensions[info["format"]], output_sha256=digest(raw),
                returned_image_count=len(urls))
    return raw, info


def prepare(questions: Path, limit: int) -> tuple[dict, list[dict]]:
    raw = questions.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    qids = []
    for row in rows:
        qid = row.get("qid")
        if not isinstance(qid, str) or not QID.fullmatch(qid):
            raise ValueError("Invalid or missing qid")
        if row.get("task", "t2i") != "t2i":
            raise ValueError("Only t2i questions are supported")
        if not isinstance(row.get("gen_prompt"), str) or not row["gen_prompt"].strip():
            raise ValueError(f"Missing gen_prompt: {qid}")
        qids.append(qid)
    if not rows or len(qids) != len(set(qids)):
        raise ValueError("Question bank is empty or has duplicate qids")
    manifest = {"schema": SCHEMA, "questions_path": str(questions.resolve()),
                "questions_sha256": digest(raw), "model": MODEL, "endpoint": ENDPOINT,
                "image_config": {"aspect_ratio": "1:1"}, "limit": limit,
                "selected_qids": qids[:limit], "automatic_retries": 0}
    return manifest, rows[:limit]


def run(questions: Path, out_dir: Path, limit: int, timeout: int = 240) -> dict:
    if not 1 <= limit <= 24 or not 1 <= timeout <= 240:
        raise ValueError("limit must be 1..24; timeout must be 1..240 seconds")
    manifest, rows = prepare(questions, limit)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / ".runner.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest_path = out_dir / "manifest.json"
        if manifest_path.exists():
            if json.loads(manifest_path.read_text()) != manifest:
                raise ValueError("Output directory belongs to a different frozen request batch")
        else:
            write_new(manifest_path, encoded(manifest))
        with requests.Session() as session:
            session.trust_env = False
            for row in rows:
                qid = row["qid"]
                item = out_dir / qid
                if item.exists():
                    print(json.dumps({"qid": qid, "status": "existing_attempt_skipped"}), flush=True)
                    continue
                item.mkdir()
                payload = {"model": MODEL, "messages": [{"role": "user", "content": [
                    {"type": "text", "text": row["gen_prompt"]}]}],
                    "modalities": ["image", "text"], "image_config": {"aspect_ratio": "1:1"}}
                request_raw = encoded(payload)
                write_new(item / "question.json", encoded(row))
                write_new(item / "request.json", request_raw)
                rec = {"schema": SCHEMA, "qid": qid, "task": "t2i", "ok": False,
                       "questions_sha256": manifest["questions_sha256"],
                       "question_sha256": digest(encoded(row)),
                       "prompt_sha256": digest(row["gen_prompt"].encode()),
                       "request_sha256": digest(request_raw), "request_model": MODEL,
                       "endpoint": ENDPOINT, "started_at": time.time(), "usage": {}}
                # Durable marker precedes HTTP; an ambiguous crashed attempt never retries.
                write_new(item / "attempt.json", encoded(rec))
                started = time.monotonic()
                try:
                    response = session.post(ENDPOINT, data=request_raw,
                                            headers={"Content-Type": "application/json"},
                                            timeout=timeout, allow_redirects=False)
                    write_new(item / "response.raw", response.content)
                    rec["http_status"] = response.status_code
                    obj = response.json()
                    write_new(item / "response.json", encoded(obj))
                    rec["usage"] = obj.get("usage") or {}
                    rec["response_model"] = obj.get("model")
                    rec["response_id"] = obj.get("id")
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(f"HTTP {response.status_code}; see retained raw response")
                    raw, info = parse_image(obj)
                    filename = "image" + info.pop("extension")
                    write_new(item / filename, raw)
                    rec.update(info, image=f"{qid}/{filename}", ok=True)
                except Exception as exc:
                    rec["error"] = f"{type(exc).__name__}: {exc}"
                rec["seconds"] = round(time.monotonic() - started, 3)
                write_new(item / "result.json", encoded(rec))
                print(json.dumps({k: rec.get(k) for k in ("qid", "ok", "seconds", "usage", "error")}), flush=True)
    records = [json.loads(p.read_text()) for p in out_dir.glob("*/result.json")]
    costs = [r.get("usage", {}).get("cost") for r in records]
    summary = {"selected": len(rows), "results": len(records),
               "successful": sum(r["ok"] for r in records),
               "reported_cost_usd": sum(c for c in costs if isinstance(c, (int, float))),
               "results_without_reported_cost": sum(not isinstance(c, (int, float)) for c in costs)}
    print(json.dumps(summary), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--limit", type=bounded_int, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    run(args.questions, args.out_dir, args.limit, args.timeout)


if __name__ == "__main__":
    main()
