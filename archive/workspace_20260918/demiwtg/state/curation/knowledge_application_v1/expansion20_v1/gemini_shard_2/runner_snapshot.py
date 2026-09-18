"""Bounded, immutable multimodal Gemini adapter for the new unified jobs.

Response parsing follows curation/probe_gemini.py; no old module is imported.
One attempt per job, at most 48 per invocation and 60 in this output directory.
Interrupted attempts remain spent; no automatic HTTP or paid generation retries.
"""
import argparse
import base64
import fcntl
import io
import json
from pathlib import Path
import time
import traceback

try:
    from .bagel_runner import validate_jobs, publish, encoded, sha, image_role_text
except ImportError:
    from bagel_runner import validate_jobs, publish, encoded, sha, image_role_text

MODEL = "openrouter/google/gemini-3.1-flash-image"
ENDPOINT = "http://127.0.0.1:4001/v1/chat/completions"


def bounded_limit(value):
    value = int(value)
    if not 1 <= value <= 48:
        raise argparse.ArgumentTypeError("--limit must be 1..48")
    return value


def make_payload(job):
    from PIL import Image
    content = []
    for index, entry in enumerate(job["images"], 1):
        data = Path(entry["path"]).read_bytes()
        if sha(data) != entry["sha256"]:
            raise ValueError("Input image changed after jobs validation")
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            image.verify()
        mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(fmt)
        if not mime:
            raise ValueError(f"Unsupported input format {fmt}; freeze a supported image in jobs")
        content.extend([
            {"type": "text", "text": image_role_text(index, entry["role"])},
            {"type": "image_url", "image_url": {
                "url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")}},
        ])
    content.append({"type": "text", "text": job["prompt"]})
    # Gemini does not guarantee BAGEL-equivalent seeded noise; record the job
    # seed as provenance, without claiming the provider honors this RNG.
    return {"model": MODEL, "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"], "image_config": {"aspect_ratio": "1:1"}}


def parse_image(obj, reference_hashes):
    from PIL import Image
    choices = obj.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    parts = list(message.get("images") or [])
    if isinstance(message.get("content"), list):
        parts.extend(message["content"])
    urls = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        field = part.get("image_url")
        url = field.get("url") if isinstance(field, dict) else field
        if isinstance(url, str) and url not in urls:
            urls.append(url)
    inline = [u for u in urls if u.startswith("data:image/") and ";base64," in u]
    if len(inline) != 1:
        raise ValueError(f"Expected one inline generated image, got {len(inline)}; URLs retained, never fetched")
    data = base64.b64decode(inline[0].split(",", 1)[1], validate=True)
    if sha(data) in reference_hashes:
        raise ValueError("Response image is byte-identical to an input; not counted as generation")
    with Image.open(io.BytesIO(data)) as image:
        width, height, fmt = image.width, image.height, image.format
        image.verify()
    ext = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(fmt)
    if not ext:
        raise ValueError(f"Unsupported generated format {fmt}")
    return data, dict(extension=ext, width=width, height=height, format=fmt,
                      output_sha256=sha(data), returned_image_count=len(urls),
                      square_output=width == height)


def run(jobs_path, out, limit, job_ids=None, validate_only=False):
    if not 1 <= limit <= 48:
        raise ValueError("limit must be 1..48")
    jobs, raw = validate_jobs(jobs_path)
    if job_ids is not None:
        unknown = set(job_ids) - {j["job_id"] for j in jobs}
        if unknown:
            raise ValueError(f"Unknown job IDs: {sorted(unknown)}")
        jobs = [j for j in jobs if j["job_id"] in job_ids]
    if validate_only:
        for job in jobs[:limit]:
            make_payload(job)
        return {"eligible": len(jobs), "validated": min(limit, len(jobs)), "http_attempts": 0}
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ".runner.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        publish(out / "jobs.jsonl", raw)
        publish(out / "runner_snapshot.py", Path(__file__).read_bytes())
        helper = Path(__file__).with_name("bagel_runner.py")
        publish(out / "bagel_helper_snapshot.py", helper.read_bytes())
        common = dict(model=MODEL, endpoint=ENDPOINT, jobs_sha256=sha(raw),
                      runner_sha256=sha(Path(__file__).read_bytes()),
                      helper_sha256=sha(helper.read_bytes()), max_attempts=60,
                      automatic_retries=0, timeout_seconds=240,
                      image_config={"aspect_ratio": "1:1"}, seed_supported=False)
        publish(out / "run_config.json", encoded(common))
        # request.json itself reserves budget before any ambiguous network call.
        spent = len(list(out.glob("jobs/*/request.json")))
        pending = [j for j in jobs if not (out / "jobs" / j["job_id"] / "request.json").exists()][:limit]
        if spent + len(pending) > 60:
            raise ValueError(f"Output budget exceeded: {spent} reserved + {len(pending)} new > 60")
        import requests
        with requests.Session() as session:
            session.trust_env = False
            session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
            for job in pending:
                d = out / "jobs" / job["job_id"]
                payload = make_payload(job)
                request_raw = encoded(payload)
                publish(d / "job.json", encoded(job))
                # Durable request commits this job's single attempt. Never resend
                # even if the process dies between this write and session.post.
                publish(d / "request.json", request_raw)
                rec = dict(job_id=job["job_id"], question_id=job["question_id"],
                           task=job["task"], condition=job["condition"], seed=job["seed"],
                           seed_supported=False, request_sha256=sha(request_raw),
                           prompt_sha256=sha(job["prompt"].encode()),
                           job_sha256=sha(encoded(job)), jobs_sha256=sha(raw),
                           request_model=MODEL, endpoint=ENDPOINT, ok=False,
                           started_unix=time.time(), usage={})
                publish(d / "attempt.json", encoded(rec))
                started = time.monotonic()
                try:
                    response = session.post(ENDPOINT, data=request_raw,
                                            headers={"Content-Type": "application/json"},
                                            timeout=240, allow_redirects=False)
                    publish(d / "response.raw", response.content)
                    rec["http_status"] = response.status_code
                    # Preserve provider/gateway cost and request identifiers,
                    # excluding cookie/authentication headers.
                    rec["response_metadata"] = {k: v for k, v in response.headers.items()
                                                if any(x in k.lower() for x in ("cost", "usage", "request-id"))}
                    obj = response.json()
                    publish(d / "response.json", encoded(obj))
                    rec["usage"] = obj.get("usage") or {}
                    rec["response_model"] = obj.get("model")
                    rec["response_id"] = obj.get("id")
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(f"HTTP {response.status_code}; raw response retained")
                    data, info = parse_image(obj, {x["sha256"] for x in job["images"]})
                    name = "image" + info.pop("extension")
                    publish(d / name, data)
                    rec.update(info, image=str(d / name), ok=True)
                except Exception:
                    rec["error"] = traceback.format_exc()
                rec["seconds"] = round(time.monotonic() - started, 3)
                publish(d / "result.json", encoded(rec))
                print(json.dumps({k: rec.get(k) for k in ("job_id", "ok", "usage", "seconds", "error")}), flush=True)
        results = [json.loads(p.read_text()) for p in out.glob("jobs/*/result.json")]
        costs = [r.get("usage", {}).get("cost") for r in results]
        return dict(attempts_reserved=len(list(out.glob("jobs/*/request.json"))),
                    completed_records=len(results), successful=sum(r["ok"] for r in results),
                    reported_cost_usd=sum(c for c in costs if isinstance(c, (int, float))),
                    results_without_reported_cost=sum(not isinstance(c, (int, float)) for c in costs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", required=True, type=bounded_limit)
    parser.add_argument("--job-ids", help="Comma-separated job IDs; preserves jobs file order")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    ids = set(args.job_ids.split(",")) if args.job_ids else None
    print(json.dumps(run(args.jobs, args.out, args.limit, ids, args.validate_only)))


if __name__ == "__main__":
    main()
