#!/usr/bin/env python3
"""Build and summarize the reproducible complexity-audit double-check sample."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "archive"    # 复核轮历史产物已归档
DEFAULT_SOURCE = HERE / "complexity_audit_synth.jsonl"    # 活素材仍在 edit 根
DEFAULT_SAMPLE = ARCHIVE / "audit_doublecheck_sample.jsonl"
DEFAULT_RESULTS = ARCHIVE / "audit_doublecheck_results.jsonl"
DEFAULT_REPORT = ARCHIVE / "audit_doublecheck.report.json"
SEED = 20260902


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def has_subject_group(row: dict) -> bool:
    return any(
        group.get("role") == "is_subject" and int(group.get("count", 0)) >= 3
        for group in row.get("same_class_groups") or []
    )


def matched_strata(row: dict) -> list[str]:
    count = int(row["scene_count"])
    labels = []
    if 11 <= count <= 12:
        labels.append("scene_11_12")
    if 8 <= count <= 9:
        labels.append("scene_8_9")
    if 6 <= count <= 7:
        labels.append("scene_6_7")
    if row.get("generator") == "gpt-image-2" and len(row.get("consequence_carriers") or []) >= 2:
        labels.append("gpt_carriers_ge2")
    if has_subject_group(row):
        labels.append("subject_group_ge3")
    return labels


def build_sample(source: Path, output: Path, seed: int) -> None:
    rows = [row for row in read_jsonl(source) if not row.get("error")]
    by_sha = {row["sha256"]: row for row in rows}
    if len(by_sha) != len(rows):
        raise SystemExit("source contains duplicate sha256 rows")

    rng = random.Random(seed)
    selected: list[tuple[str, dict]] = []
    used: set[str] = set()

    # Reserve the mandatory seven first, then sample the three scene strata
    # around them so the advertised 30+10 means 40 unique images.
    mandatory = sorted(
        (
            row
            for row in rows
            if row.get("generator") == "gpt-image-2"
            and len(row.get("consequence_carriers") or []) >= 2
        ),
        key=lambda row: row["sha256"],
    )
    if len(mandatory) != 7:
        raise SystemExit(f"expected 7 mandatory GPT carrier rows, found {len(mandatory)}")
    for row in mandatory:
        used.add(row["sha256"])

    for label, lo, hi in (
        ("scene_11_12", 11, 12),
        ("scene_8_9", 8, 9),
        ("scene_6_7", 6, 7),
    ):
        candidates = [row for row in rows if lo <= int(row["scene_count"]) <= hi and row["sha256"] not in used]
        chosen = rng.sample(candidates, 10)
        selected.extend((label, row) for row in chosen)
        used.update(row["sha256"] for row in chosen)

    selected.extend(("gpt_carriers_ge2", row) for row in mandatory)

    candidates = [row for row in rows if has_subject_group(row) and row["sha256"] not in used]
    chosen = rng.sample(candidates, 3)
    selected.extend(("subject_group_ge3", row) for row in chosen)

    if len(selected) != 40 or len({row["sha256"] for _, row in selected}) != 40:
        raise SystemExit("sampling did not produce 40 unique rows")

    output_rows = []
    for index, (primary, row) in enumerate(selected, 1):
        output_rows.append(
            {
                "sample_id": f"DC{index:02d}",
                "primary_stratum": primary,
                "matched_strata": matched_strata(row),
                "seed": seed,
                "audit": row,
            }
        )
    write_jsonl(output, output_rows)
    print(f"wrote {len(output_rows)} unique samples to {output}")
    print("primary strata:", dict(Counter(row["primary_stratum"] for row in output_rows)))


def build_report(sample_path: Path, results_path: Path, output: Path) -> None:
    samples = read_jsonl(sample_path)
    results = read_jsonl(results_path)
    sample_by_sha = {row["audit"]["sha256"]: row for row in samples}
    result_by_sha = {row["sha256"]: row for row in results}
    if len(sample_by_sha) != 40:
        raise SystemExit(f"expected 40 unique sample rows, found {len(sample_by_sha)}")
    if set(result_by_sha) != set(sample_by_sha):
        missing = sorted(set(sample_by_sha) - set(result_by_sha))
        extra = sorted(set(result_by_sha) - set(sample_by_sha))
        raise SystemExit(f"result/sample mismatch; missing={missing}, extra={extra}")

    required = {
        "sha256", "instance", "scene_inflation", "scene_missed", "groups_wrong",
        "groups_missed", "referents_wrong", "carriers_wrong", "carriers_missed",
        "suitability_wrong", "overall", "notes",
    }
    for result in results:
        if set(result) != required:
            raise SystemExit(f"bad result schema for {result.get('sha256')}: {sorted(set(result) ^ required)}")
        if result["instance"] != sample_by_sha[result["sha256"]]["audit"]["instance"]:
            raise SystemExit(f"instance mismatch for {result['sha256']}")
        audit = sample_by_sha[result["sha256"]]["audit"]
        claimed_carriers_for_row = set(audit.get("consequence_carriers") or [])
        if not set(result["carriers_wrong"]).issubset(claimed_carriers_for_row):
            raise SystemExit(f"carrier wrong item was not claimed for {result['sha256']}")
        if set(result["carriers_missed"]) & claimed_carriers_for_row:
            raise SystemExit(f"carrier missed item was already claimed for {result['sha256']}")
        claimed_group_classes = {group["class"] for group in audit.get("same_class_groups") or []}
        if not {item["class"] for item in result["groups_wrong"]}.issubset(claimed_group_classes):
            raise SystemExit(f"group wrong item was not claimed for {result['sha256']}")
        if not set(result["referents_wrong"]).issubset(set(audit.get("referents") or [])):
            raise SystemExit(f"referent wrong item was not claimed for {result['sha256']}")
        if result["overall"] not in {"可信", "偏乐观", "不可信"}:
            raise SystemExit(f"bad overall value for {result['sha256']}")

    untrusted = {
        result["sha256"]
        for result in results
        if len(result["scene_inflation"]) >= 3 or len(result["groups_wrong"]) >= 2
    }
    for result in results:
        if result["sha256"] in untrusted and result["overall"] != "不可信":
            raise SystemExit(f"hard-rule untrusted row lacks matching overall for {result['sha256']}")
    claimed_carriers = sum(
        len(sample_by_sha[result["sha256"]]["audit"].get("consequence_carriers") or [])
        for result in results
    )
    wrong_carriers = sum(len(result["carriers_wrong"]) for result in results)
    claimed_carrier_rows = sum(
        bool(sample_by_sha[result["sha256"]]["audit"].get("consequence_carriers"))
        for result in results
    )
    wrong_carrier_rows = sum(bool(result["carriers_wrong"]) for result in results)

    score_pattern = re.compile(r"审计(\d)分实撑(\d)分")
    missed_pattern = re.compile(r"实撑(\d)分而审计为(\d)分")
    original_scene_counts = []
    corrected_scene_counts = []
    original_scene_strong = []
    corrected_scene_strong = []
    for result in results:
        audit = sample_by_sha[result["sha256"]]["audit"]
        scores = dict(audit["scene_sources"])
        original_scene_counts.append(sum(score >= 1 for score in scores.values()))
        original_scene_strong.append(sum(score >= 2 for score in scores.values()))
        for item in result["scene_inflation"]:
            match = score_pattern.search(item)
            if not match:
                raise SystemExit(f"cannot parse scene inflation: {item}")
            scores[item.split("：", 1)[0]] = int(match.group(2))
        for item in result["scene_missed"]:
            match = missed_pattern.search(item)
            if not match:
                raise SystemExit(f"cannot parse scene miss: {item}")
            scores[item.split("：", 1)[0]] = int(match.group(1))
        corrected_scene_counts.append(sum(score >= 1 for score in scores.values()))
        corrected_scene_strong.append(sum(score >= 2 for score in scores.values()))

    corrected_double = 0
    original_double = 0
    gpt_original_double = 0
    gpt_corrected_double = 0
    for result in results:
        audit = sample_by_sha[result["sha256"]]["audit"]
        claimed = list(audit.get("consequence_carriers") or [])
        corrected = (set(claimed) - set(result["carriers_wrong"])) | set(result["carriers_missed"])
        is_double = len(claimed) >= 2
        is_corrected_double = len(corrected) >= 2
        original_double += is_double
        corrected_double += is_corrected_double
        if audit.get("generator") == "gpt-image-2":
            gpt_original_double += is_double
            gpt_corrected_double += is_corrected_double

    by_primary = {}
    for label in sorted({row["primary_stratum"] for row in samples}):
        shas = {row["audit"]["sha256"] for row in samples if row["primary_stratum"] == label}
        subset = [result for result in results if result["sha256"] in shas]
        by_primary[label] = {
            "n": len(subset),
            "untrusted": sum(result["sha256"] in untrusted for result in subset),
            "untrusted_rate": sum(result["sha256"] in untrusted for result in subset) / len(subset),
            "scene_inflation_items": sum(len(result["scene_inflation"]) for result in subset),
            "groups_wrong_items": sum(len(result["groups_wrong"]) for result in subset),
            "carrier_wrong_items": sum(len(result["carriers_wrong"]) for result in subset),
        }

    by_generator = {}
    for label, predicate in (
        ("qwen-image", lambda audit: audit.get("generator") is None),
        ("gpt-image-2", lambda audit: audit.get("generator") == "gpt-image-2"),
    ):
        shas = {
            row["audit"]["sha256"]
            for row in samples
            if predicate(row["audit"])
        }
        subset = [result for result in results if result["sha256"] in shas]
        by_generator[label] = {
            "n": len(subset),
            "untrusted": sum(result["sha256"] in untrusted for result in subset),
            "untrusted_rate": sum(result["sha256"] in untrusted for result in subset) / len(subset),
            "carrier_claimed_items": sum(
                len(sample_by_sha[result["sha256"]]["audit"].get("consequence_carriers") or [])
                for result in subset
            ),
            "carrier_wrong_items": sum(len(result["carriers_wrong"]) for result in subset),
        }

    scene_inflation_dims = Counter(
        item.split("：", 1)[0]
        for result in results
        for item in result["scene_inflation"]
    )
    report = {
        "source": str(DEFAULT_SOURCE),
        "sample": str(sample_path),
        "results": str(results_path),
        "seed": samples[0]["seed"],
        "sample_size": len(results),
        "decision_rule": "scene_inflation>=3 or groups_wrong>=2",
        "untrusted_count": len(untrusted),
        "untrusted_rate": len(untrusted) / len(results),
        "overall_counts": dict(Counter(result["overall"] for result in results)),
        "scene_inflation_items": sum(len(result["scene_inflation"]) for result in results),
        "scene_missed_items": sum(len(result["scene_missed"]) for result in results),
        "scene_inflation_dims": dict(scene_inflation_dims.most_common()),
        "scene_counts": {
            "original_mean": sum(original_scene_counts) / len(original_scene_counts),
            "corrected_mean": sum(corrected_scene_counts) / len(corrected_scene_counts),
            "corrected_to_original_ratio": sum(corrected_scene_counts) / sum(original_scene_counts),
            "original_strong_mean": sum(original_scene_strong) / len(original_scene_strong),
            "corrected_strong_mean": sum(corrected_scene_strong) / len(corrected_scene_strong),
            "corrected_strong_to_original_ratio": sum(corrected_scene_strong) / sum(original_scene_strong),
            "temporary_selection_penalty_from_prompt": 0.8,
        },
        "groups_wrong_items": sum(len(result["groups_wrong"]) for result in results),
        "groups_missed_items": sum(len(result["groups_missed"]) for result in results),
        "referents_wrong_items": sum(len(result["referents_wrong"]) for result in results),
        "carriers": {
            "claimed_items": claimed_carriers,
            "wrong_items": wrong_carriers,
            "item_false_positive_rate": wrong_carriers / claimed_carriers if claimed_carriers else None,
            "claimed_rows": claimed_carrier_rows,
            "wrong_rows": wrong_carrier_rows,
            "row_false_positive_rate": wrong_carrier_rows / claimed_carrier_rows if claimed_carrier_rows else None,
            "sample_original_double_rows": original_double,
            "sample_corrected_double_rows": corrected_double,
            "sample_double_row_survival_rate": corrected_double / original_double if original_double else None,
            "gpt_original_double_rows": gpt_original_double,
            "gpt_corrected_double_rows": gpt_corrected_double,
            "gpt_double_row_survival_rate": (
                gpt_corrected_double / gpt_original_double if gpt_original_double else None
            ),
            "primary_false_positive_definition": "wrong carrier claims / claimed carrier items",
            "row_rate_is_diagnostic_only": True,
        },
        "suitability_wrong_items": sum(len(result["suitability_wrong"]) for result in results),
        "by_primary_stratum": by_primary,
        "by_generator": by_generator,
        "thresholds": {
            "audit_optimistic_if_untrusted_rate_gt": 0.20,
            "carrier_supply_reestimate_if_false_positive_rate_gt": 0.15,
        },
        "decisions": {
            "audit_is_optimistic": len(untrusted) / len(results) > 0.20,
            "carrier_supply_requires_reestimate_by_item_rate": (
                wrong_carriers / claimed_carriers > 0.15 if claimed_carriers else False
            ),
            "carrier_rows_need_review_by_row_rate": (
                wrong_carrier_rows / claimed_carrier_rows > 0.15 if claimed_carrier_rows else False
            ),
        },
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote report to {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sample = subparsers.add_parser("sample")
    sample.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    sample.add_argument("--output", type=Path, default=DEFAULT_SAMPLE)
    sample.add_argument("--seed", type=int, default=SEED)
    report = subparsers.add_parser("report")
    report.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    report.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    report.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if args.command == "sample":
        build_sample(args.source, args.output, args.seed)
    else:
        build_report(args.sample, args.results, args.output)


if __name__ == "__main__":
    main()
