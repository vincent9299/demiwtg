"""Prepare anonymous Codex edit judging inputs and validate/aggregate scores.

The blind manifest intentionally contains only BEFORE, AFTER, instruction and
edit type.  Model identity and synthesis metadata live in a separate file that
must not be shown to the judge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path


EDIT_DIMS = {
    "replace": ["Prompt Compliance", "Visual Naturalness", "Physical & Detail Integrity"],
    "add": ["Prompt Compliance", "Visual Naturalness", "Physical & Detail Coherence"],
    "adjust": ["Prompt Compliance", "Visual Seamlessness", "Physical & Detail Fidelity"],
    "remove": ["Prompt Compliance", "Visual Naturalness", "Physical & Detail Integrity"],
    "style": ["Style Fidelity", "Content Preservation", "Rendering Quality"],
    "action": ["Action Fidelity", "Identity Preservation", "Visual & Anatomical Coherence"],
    "extract": ["Object Identity", "Mask Precision", "Visual Quality"],
    "background": ["Instruction Compliance", "Visual Seamlessness", "Physical Consistency"],
    "compose": ["Instruction Compliance", "Visual Naturalness", "Physical Consistency & Fine Detail"],
}
VALIDITY = {"ok", "model_failure", "judge_unscorable", "invalid_question"}
PHI = {0: 0, 1: 60, 2: 100}


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_source(question: dict, questions_path: Path) -> Path:
    source = Path(question["_sample_image"])
    if source.is_absolute() and source.is_file():
        return source
    for base in (questions_path.parent, questions_path.parent.parent):
        candidate = base / source
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"source image not found: {source}")


def prepare(args: argparse.Namespace) -> None:
    questions = load_jsonl(args.questions)
    response_paths = (sorted(args.responses.glob("responses_shard*.jsonl"))
                      if args.responses.is_dir() else [args.responses])
    if not response_paths:
        raise FileNotFoundError(f"no response JSONL found: {args.responses}")
    responses: dict[str, tuple[dict, Path]] = {}
    for response_path in response_paths:
        for response in load_jsonl(response_path):
            qid = response["qid"]
            if qid in responses:
                raise ValueError(f"duplicate response qid: {qid}")
            responses[qid] = (response, response_path.parent)
    before_dir = args.out_dir / "inputs" / "before"
    after_dir = args.out_dir / "inputs" / "after"
    before_dir.mkdir(parents=True, exist_ok=True)
    after_dir.mkdir(parents=True, exist_ok=True)
    blind, identity = [], []
    for index, question in enumerate(questions, 1):
        qid = question["qid"]
        response_entry = responses.get(qid)
        if not response_entry or not response_entry[0].get("ok", True):
            raise ValueError(f"missing successful response: {qid}")
        response, response_base = response_entry
        source = resolve_source(question, args.questions)
        result = Path(response["image"])
        if not result.is_absolute():
            result = response_base / result
        if not result.is_file():
            raise FileNotFoundError(f"result image not found: {result}")
        source_hash = sha256(source)
        result_hash = sha256(result)
        instruction_hash = hashlib.sha256(
            question["edit_instruction"].encode("utf-8")
        ).hexdigest()
        for field, actual in (("source_sha256", source_hash),
                              ("output_sha256", result_hash),
                              ("instruction_sha256", instruction_hash)):
            recorded = response.get(field)
            if recorded and recorded != actual:
                raise ValueError(f"{qid}: response {field} mismatch")
        candidate_id = f"c{index:03d}"
        before = before_dir / f"{candidate_id}{source.suffix.lower()}"
        after = after_dir / f"{candidate_id}{result.suffix.lower()}"
        shutil.copyfile(source, before)
        shutil.copyfile(result, after)
        blind.append({
            "schema": "edit-codex-blind-input-v1",
            "qid": qid,
            "candidate_id": candidate_id,
            "edit_type": question["edit_type"],
            "edit_instruction": question["edit_instruction"],
            "before": str(before.resolve()),
            "after": str(after.resolve()),
            "inputs": {
                "source_sha256": source_hash,
                "output_sha256": result_hash,
                "instruction_sha256": instruction_hash,
            },
        })
        identity.append({
            "qid": qid,
            "candidate_id": candidate_id,
            "response_model": response.get("response_model", response.get("model", "")),
            "original_result": str(result.resolve()),
        })
    atomic_jsonl(args.out_dir / "blind_manifest.jsonl", blind)
    atomic_jsonl(args.out_dir / "identity_private.jsonl", identity)
    print(f"blind inputs: {len(blind)} -> {args.out_dir / 'blind_manifest.jsonl'}")


def mean(rows: list[dict], key: str = "official_total") -> float | None:
    return round(sum(float(row[key]) for row in rows) / len(rows), 3) if rows else None


def aggregate(args: argparse.Namespace) -> None:
    manifest = {r["qid"]: r for r in load_jsonl(args.manifest)}
    score_files = sorted(args.scores_dir.glob("part_*.jsonl"))
    scores = [r for path in score_files for r in load_jsonl(path)]
    by_qid: dict[str, dict] = {}
    for row in scores:
        qid = row.get("qid")
        if qid not in manifest or qid in by_qid:
            raise ValueError(f"unknown or duplicate score qid: {qid}")
        expected = manifest[qid]
        if row.get("candidate_id") != expected["candidate_id"]:
            raise ValueError(f"candidate mismatch: {qid}")
        edit_type = expected["edit_type"]
        if row.get("edit_type") != edit_type:
            raise ValueError(f"edit_type mismatch: {qid}")
        if row.get("validity", {}).get("status") not in VALIDITY:
            raise ValueError(f"bad validity: {qid}")
        dims = row.get("raw_dimensions", [])
        if len(dims) != 3:
            raise ValueError(f"need three dimensions: {qid}")
        raw = []
        qib_v2 = row.get("schema") == "edit-codex-v2-qib"
        for i, (dim, label) in enumerate(zip(dims, EDIT_DIMS[edit_type]), 1):
            if dim.get("key") != f"d{i}" or dim.get("label") != label:
                raise ValueError(f"dimension contract mismatch: {qid}/d{i}")
            if qib_v2:
                tier = dim.get("tier")
                if not isinstance(tier, int) or tier not in PHI:
                    raise ValueError(f"invalid QIB tier: {qid}/d{i}")
                if dim.get("mapped") != PHI[tier]:
                    raise ValueError(f"bad QIB mapping: {qid}/d{i}")
                raw.append(tier)
            else:
                score = dim.get("score")
                if not isinstance(score, int) or not 1 <= score <= 5:
                    raise ValueError(f"invalid score: {qid}/d{i}")
                raw.append(score)
        official = [raw[0], min(raw[1], raw[0]), min(raw[2], raw[0])]
        if qib_v2:
            mapped = [PHI[tier] for tier in raw]
            official_mapped = [PHI[tier] for tier in official]
            row["raw_tier_mean"] = round(sum(raw) / 3, 3)
            row["mapped_dimensions"] = dict(zip(("d1", "d2", "d3"), mapped))
            row["official_tiers"] = dict(zip(("d1", "d2", "d3"), official))
            row["official_dimensions"] = dict(
                zip(("d1", "d2", "d3"), official_mapped)
            )
            row["official_total"] = round(sum(official_mapped) / 3, 3)
        else:
            row["raw_total"] = round(sum(raw) / 3, 3)
            row["official_dimensions"] = dict(zip(("d1", "d2", "d3"), official))
            row["official_total"] = round(sum(official) / 3, 3)
        row["inputs"] = expected["inputs"]
        by_qid[qid] = row
    missing = sorted(set(manifest) - set(by_qid))
    if missing:
        raise ValueError(f"missing scores: {', '.join(missing)}")

    ordered = [by_qid[qid] for qid in manifest]
    questions = {q["qid"]: q for q in load_jsonl(args.questions)}
    valid = [r for r in ordered if r["validity"]["status"] == "ok"]
    invalid = [r for r in ordered if r["validity"]["status"] != "ok"]
    schemes = sorted({r.get("schema", "") for r in ordered})
    if len(schemes) != 1:
        raise ValueError(f"mixed score schemas: {schemes}")
    report: dict = {
        "schema": "edit-codex-score-report-v2" if schemes == ["edit-codex-v2-qib"]
        else "edit-codex-score-report-v1",
        "score_schema": schemes[0],
        "n": len(ordered),
        "n_valid": len(valid),
        "n_invalid": len(invalid),
        "overall": mean(valid),
    }
    for field, report_key in (
        ("level", "by_level"),
        ("edit_type", "by_edit_type"),
        ("suite", "by_suite"),
        ("_batch", "by_source_batch"),
    ):
        groups: dict[str, list[dict]] = defaultdict(list)
        for score in valid:
            groups[str(questions[score["qid"]].get(field, "unknown"))].append(score)
        report[report_key] = {
            group: {"n": len(rows), "official_total": mean(rows)}
            for group, rows in sorted(groups.items())
        }
    report["invalid_qids"] = [r["qid"] for r in invalid]
    atomic_jsonl(args.out_dir / "scores.jsonl", ordered)
    report_path = args.out_dir / "report.json"
    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def paired_summary(rows: list[dict], left_name: str, right_name: str) -> dict:
    if not rows:
        return {"n": 0, left_name: None, right_name: None,
                "delta_left_minus_right": None, "wins_ties_losses": {"win": 0, "tie": 0, "loss": 0}}
    left_mean = round(sum(r["left_total"] for r in rows) / len(rows), 3)
    right_mean = round(sum(r["right_total"] for r in rows) / len(rows), 3)
    return {
        "n": len(rows),
        left_name: left_mean,
        right_name: right_mean,
        "delta_left_minus_right": round(left_mean - right_mean, 3),
        "wins_ties_losses": {
            "win": sum(r["winner"] == "left" for r in rows),
            "tie": sum(r["winner"] == "tie" for r in rows),
            "loss": sum(r["winner"] == "right" for r in rows),
        },
    }


def compare(args: argparse.Namespace) -> None:
    questions = {q["qid"]: q for q in load_jsonl(args.questions)}
    left = {r["qid"]: r for r in load_jsonl(args.left_scores)}
    right = {r["qid"]: r for r in load_jsonl(args.right_scores)}
    if set(left) != set(right):
        raise ValueError("paired score qids differ")
    rows = []
    excluded = []
    for qid in questions:
        if qid not in left:
            continue
        lrow, rrow = left[qid], right[qid]
        lstatus = lrow.get("validity", {}).get("status")
        rstatus = rrow.get("validity", {}).get("status")
        if lstatus != "ok" or rstatus != "ok":
            excluded.append({"qid": qid, "left_status": lstatus, "right_status": rstatus})
            continue
        ltotal, rtotal = float(lrow["official_total"]), float(rrow["official_total"])
        winner = "left" if ltotal > rtotal else "right" if rtotal > ltotal else "tie"
        q = questions[qid]
        rows.append({
            "qid": qid,
            "level": q.get("level"),
            "edit_type": q.get("edit_type"),
            "suite": q.get("suite"),
            "source_batch": q.get("_batch"),
            "left": args.left_name,
            "right": args.right_name,
            "left_total": ltotal,
            "right_total": rtotal,
            "delta_left_minus_right": round(ltotal - rtotal, 3),
            "winner": winner,
        })
    report = {
        "schema": "edit-codex-paired-qib-v1",
        "left": args.left_name,
        "right": args.right_name,
        "overall": paired_summary(rows, args.left_name, args.right_name),
        "excluded": excluded,
    }
    for field, key in (("level", "by_level"), ("edit_type", "by_edit_type"),
                       ("suite", "by_suite"), ("source_batch", "by_source_batch")):
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(field, "unknown"))].append(row)
        report[key] = {group: paired_summary(group_rows, args.left_name, args.right_name)
                       for group, group_rows in sorted(groups.items())}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    atomic_jsonl(args.out_dir / "paired_scores.jsonl", rows)
    report_path = args.out_dir / "report.json"
    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--questions", type=Path, required=True)
    p_prepare.add_argument("--responses", type=Path, required=True)
    p_prepare.add_argument("--out-dir", type=Path, required=True)
    p_prepare.set_defaults(func=prepare)
    p_aggregate = sub.add_parser("aggregate")
    p_aggregate.add_argument("--questions", type=Path, required=True)
    p_aggregate.add_argument("--manifest", type=Path, required=True)
    p_aggregate.add_argument("--scores-dir", type=Path, required=True)
    p_aggregate.add_argument("--out-dir", type=Path, required=True)
    p_aggregate.set_defaults(func=aggregate)
    p_compare = sub.add_parser("compare")
    p_compare.add_argument("--questions", type=Path, required=True)
    p_compare.add_argument("--left-scores", type=Path, required=True)
    p_compare.add_argument("--right-scores", type=Path, required=True)
    p_compare.add_argument("--left-name", required=True)
    p_compare.add_argument("--right-name", required=True)
    p_compare.add_argument("--out-dir", type=Path, required=True)
    p_compare.set_defaults(func=compare)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
