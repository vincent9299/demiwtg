#!/usr/bin/env python3
"""Offline registration and validation for hard-plus built-in imagegen files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import time
import zlib
from pathlib import Path
from typing import Iterable


HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "focus200" / "quality_regen_v1"
FOCUS = HERE / "data" / "focus200"
MANIFEST = FOCUS / "manifest.jsonl"
PLAN = DATA / "plan.jsonl"
MERGED = DATA / "merged_results.jsonl"
ACCEPTED = DATA / "accepted_results.jsonl"
SUMMARY = DATA / "review_summary.json"
SHARDS = ("a", "b", "c")


class ValidationError(RuntimeError):
    pass


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise ValidationError(f"missing file: {path}")
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValidationError(f"{path}:{lineno}: expected a JSON object")
        rows.append(row)
    return rows


def plan_rows() -> list[dict]:
    rows = read_jsonl(PLAN)
    seen: set[str] = set()
    for lineno, row in enumerate(rows, 1):
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValidationError(f"{PLAN}:{lineno}: missing candidate_id")
        if candidate_id in seen:
            raise ValidationError(f"{PLAN}:{lineno}: duplicate candidate_id {candidate_id!r}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate_id):
            raise ValidationError(f"{PLAN}:{lineno}: unsafe candidate_id {candidate_id!r}")
        match = re.fullmatch(r"qrv1-([abc])-\d{4}", candidate_id)
        if not match:
            raise ValidationError(f"{PLAN}:{lineno}: candidate_id does not encode a valid shard")
        row["shard"] = match.group(1)
        seen.add(candidate_id)
    return rows


def shard_dir(shard: str) -> Path:
    return DATA / f"agent_{shard}"


def results_path(shard: str) -> Path:
    return shard_dir(shard) / "results.jsonl"


def atomic_write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def inspect_png(path: Path) -> tuple[int, int, str]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValidationError(f"not a PNG: {path}")
    offset = 8
    width = height = None
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValidationError(f"truncated PNG chunk: {path}")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValidationError(f"truncated PNG payload: {path}")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise ValidationError(f"PNG CRC mismatch: {path}")
        if chunk_type == b"IHDR":
            if length != 13 or width is not None:
                raise ValidationError(f"invalid PNG IHDR: {path}")
            width, height = struct.unpack(">II", payload[:8])
            if width <= 0 or height <= 0:
                raise ValidationError(f"invalid PNG dimensions: {path}")
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise ValidationError(f"invalid PNG IEND/trailing data: {path}")
            saw_iend = True
            break
        offset = end
    if width is None or height is None or not saw_iend:
        raise ValidationError(f"incomplete PNG: {path}")
    return width, height, hashlib.sha256(data).hexdigest()


def image_for(candidate_id: str, images_dir: Path) -> Path | None:
    matches = sorted(
        path for path in images_dir.glob(f"{candidate_id}*.png")
        if path.name == f"{candidate_id}.png" or path.name.startswith(f"{candidate_id}_")
    )
    if len(matches) > 1:
        raise ValidationError(f"multiple PNGs for {candidate_id}: {[p.name for p in matches]}")
    return matches[0] if matches else None


def register(shard: str) -> None:
    rows = [row for row in plan_rows() if row["shard"] == shard]
    images_dir = shard_dir(shard) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    registered = []
    for plan in rows:
        candidate_id = plan["candidate_id"]
        image = image_for(candidate_id, images_dir)
        if image is None:
            continue
        width, height, digest = inspect_png(image)
        registered.append({
            **plan,
            "file": image.relative_to(DATA).as_posix(),
            "sha256": digest,
            "width": width,
            "height": height,
            "generator": "codex-imagegen",
        })
    atomic_write_jsonl(results_path(shard), registered)
    print(f"shard={shard} planned={len(rows)} registered={len(registered)} "
          f"pending={len(rows) - len(registered)}")


def load_and_verify_result(shard: str, plan_by_id: dict[str, dict]) -> list[dict]:
    path = results_path(shard)
    rows = read_jsonl(path)
    seen: set[str] = set()
    for lineno, row in enumerate(rows, 1):
        candidate_id = row.get("candidate_id")
        if candidate_id in seen:
            raise ValidationError(f"{path}:{lineno}: duplicate candidate_id {candidate_id!r}")
        seen.add(candidate_id)
        plan = plan_by_id.get(candidate_id)
        if plan is None or plan["shard"] != shard:
            raise ValidationError(f"{path}:{lineno}: candidate absent from shard plan: {candidate_id!r}")
        rel = row.get("file")
        if not isinstance(rel, str):
            raise ValidationError(f"{path}:{lineno}: missing file")
        image = (DATA / rel).resolve()
        expected_dir = (shard_dir(shard) / "images").resolve()
        try:
            image.relative_to(expected_dir)
        except ValueError as exc:
            raise ValidationError(f"{path}:{lineno}: file escapes shard image directory") from exc
        if not image.is_file():
            raise ValidationError(f"{path}:{lineno}: missing image {image}")
        width, height, digest = inspect_png(image)
        if (row.get("sha256"), row.get("width"), row.get("height")) != (digest, width, height):
            raise ValidationError(f"{path}:{lineno}: image metadata mismatch for {candidate_id}")
    return rows


def status() -> None:
    rows = plan_rows()
    plan_by_id = {row["candidate_id"]: row for row in rows}
    print(f"plan={len(rows)} expected=100")
    total = 0
    for shard in SHARDS:
        planned = sum(row["shard"] == shard for row in rows)
        path = results_path(shard)
        registered = len(load_and_verify_result(shard, plan_by_id)) if path.exists() else 0
        total += registered
        print(f"shard={shard} planned={planned} registered={registered} pending={planned - registered}")
    print(f"registered={total} pending={len(rows) - total} merged={'yes' if MERGED.exists() else 'no'}")


def merge() -> None:
    plans = plan_rows()
    if len(plans) != 100:
        raise ValidationError(f"expected exactly 100 plan rows, found {len(plans)}")
    plan_by_id = {row["candidate_id"]: row for row in plans}
    results = []
    for shard in SHARDS:
        results.extend(load_and_verify_result(shard, plan_by_id))
    result_by_id = {row["candidate_id"]: row for row in results}
    if len(result_by_id) != len(results):
        raise ValidationError("duplicate candidate_id across shard results")
    missing = sorted(set(plan_by_id) - set(result_by_id))
    extra = sorted(set(result_by_id) - set(plan_by_id))
    if missing or extra:
        raise ValidationError(f"result coverage mismatch: missing={missing} extra={extra}")
    hashes: dict[str, str] = {}
    for row in results:
        prior = hashes.setdefault(row["sha256"], row["candidate_id"])
        if prior != row["candidate_id"]:
            raise ValidationError(
                f"duplicate image content: {prior} and {row['candidate_id']} ({row['sha256']})"
            )
    merged = [result_by_id[row["candidate_id"]] for row in plans]
    atomic_write_jsonl(MERGED, merged)
    print(f"merged={len(merged)} -> {MERGED}")


def finalize() -> None:
    plans = plan_rows()
    if len(plans) != 100:
        raise ValidationError(f"expected exactly 100 plan rows, found {len(plans)}")
    plan_by_id = {row["candidate_id"]: row for row in plans}
    results = []
    reviews = []
    for shard in SHARDS:
        results.extend(load_and_verify_result(shard, plan_by_id))
        review_path = shard_dir(shard) / "review_r2.jsonl"
        shard_reviews = read_jsonl(review_path)
        expected = {row["candidate_id"] for row in plans if row["shard"] == shard}
        actual = {row.get("candidate_id") for row in shard_reviews}
        if len(actual) != len(shard_reviews) or actual != expected:
            raise ValidationError(f"{review_path}: review coverage mismatch")
        for row in shard_reviews:
            if not isinstance(row.get("accepted"), bool):
                raise ValidationError(f"{review_path}: invalid accepted for {row.get('candidate_id')}")
            reasons = row.get("reject_reasons")
            if not isinstance(reasons, list) or row["accepted"] == bool(reasons):
                raise ValidationError(f"{review_path}: inconsistent decision for {row.get('candidate_id')}")
        reviews.extend(shard_reviews)
    result_by_id = {row["candidate_id"]: row for row in results}
    accepted_ids = {row["candidate_id"] for row in reviews if row["accepted"]}
    accepted = [result_by_id[row["candidate_id"]] for row in plans if row["candidate_id"] in accepted_ids]
    atomic_write_jsonl(ACCEPTED, accepted)
    reason_counts: dict[str, int] = {}
    for row in reviews:
        for reason in row["reject_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    summary = {
        "schema_version": "1.0.0",
        "planned": len(plans),
        "accepted": len(accepted),
        "rejected": len(plans) - len(accepted),
        "acceptance_rate": len(accepted) / len(plans),
        "by_shard": {
            shard: {
                "planned": sum(row["shard"] == shard for row in plans),
                "accepted": sum(
                    row["accepted"] and row["candidate_id"].split("-")[1] == shard
                    for row in reviews
                ),
            }
            for shard in SHARDS
        },
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "review_files": [f"agent_{shard}/review_r2.jsonl" for shard in SHARDS],
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"accepted={len(accepted)} rejected={len(plans) - len(accepted)} -> {ACCEPTED}")


def promote() -> None:
    accepted = read_jsonl(ACCEPTED)
    manifest = read_jsonl(MANIFEST)
    known = {row.get("sha256") for row in manifest}
    images_dir = FOCUS / "images"
    promoted, skipped = [], []
    for index, row in enumerate(accepted):
        digest = row["sha256"]
        if digest in known:
            skipped.append(row["candidate_id"])
            continue
        src = DATA / row["file"]
        width, height, actual = inspect_png(src)
        if actual != digest:
            raise ValidationError(f"{src}: content hash mismatch for {row['candidate_id']}")
        safe_instance = re.sub(r"[/\\]", "_", row["instance"])
        dest = images_dir / f"{70100 + index}_{row['candidate_id']}_{safe_instance}.png"
        if dest.exists():
            raise ValidationError(f"destination already exists: {dest}")
        shutil.copy2(src, dest)
        if inspect_png(dest)[2] != digest:
            dest.unlink()
            raise ValidationError(f"{dest}: copied file hash mismatch")
        manifest.append({
            "prompt_id": row["logical_key"],
            "instance": row["instance"],
            "generator": row["generator"],
            "file": dest.relative_to(FOCUS).as_posix(),
            "sha256": digest,
            "width": width,
            "height": height,
            "ts": int(time.time()),
            "batch": "quality_regen_v1",
        })
        known.add(digest)
        promoted.append(row["candidate_id"])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{MANIFEST.name}.", suffix=".tmp", dir=MANIFEST.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        for row in manifest:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp_name, MANIFEST)
    listed = {row["file"] for row in manifest}
    on_disk = {f"images/{path.name}" for path in images_dir.glob("*.png")}
    orphaned = sorted(on_disk - listed)
    absent = sorted(listed - on_disk)
    if absent:
        raise ValidationError(f"manifest lists missing files: {absent[:5]}")
    print(f"promoted={len(promoted)} skipped={len(skipped)} manifest_rows={len(manifest)} "
          f"images_on_disk={len(on_disk)} orphans={len(orphaned)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register and validate quality_regen_v1 built-in imagegen outputs offline."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="validate and summarize plan/shard progress")
    register_parser = subparsers.add_parser("register", help="register one shard's PNG files")
    register_parser.add_argument("--shard", required=True, choices=SHARDS)
    subparsers.add_parser("merge", help="validate and merge exactly 100 completed candidates")
    subparsers.add_parser("finalize", help="merge final reviews into the accepted result set")
    subparsers.add_parser("promote", help="promote accepted images into focus200 manifest (idempotent)")
    args = parser.parse_args()
    try:
        if args.command == "status":
            status()
        elif args.command == "register":
            register(args.shard)
        elif args.command == "merge":
            merge()
        elif args.command == "promote":
            promote()
        else:
            finalize()
    except ValidationError as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
