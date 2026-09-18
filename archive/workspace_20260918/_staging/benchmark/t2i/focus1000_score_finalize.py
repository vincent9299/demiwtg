#!/usr/bin/env python3
"""Validate/merge manual V2 judge shards and build focus1000 score reports.

This script does not judge images.  It only validates the exact v6.0-V2 output
shape, applies eval_score.py's phi mapping, appends new qids, and summarizes the
result.  Judge workers write ``data/focus1000/judge_raw.*.jsonl`` shards.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


BASE = Path(__file__).resolve().parent / "data" / "focus1000"
PROMPTS = BASE / "gen_prompts.jsonl"
SCORES = BASE / "gen_scores.jsonl"
REPORT = BASE / "gen_scores.report.json"
SUMMARY = BASE / "gen_scores.summary.md"

ALIGNMENT = [
    "subject_presence", "form_structure", "color_material", "quantity_scale",
    "spatial_relation", "text_symbol", "action_interaction", "state_context",
    "scene_environment", "style",
]
QUALITY = [
    "physical_logic", "material_texture", "detail_richness", "artifacts",
    "resolution", "edge_clarity", "naturalness", "anatomical_fidelity",
]
AESTHETICS = [
    "composition", "color_harmony", "lighting_atmosphere",
    "emotional_expression",
]
DIMS = {"alignment": ALIGNMENT, "quality": QUALITY, "aesthetics": AESTHETICS}
PHI = {0: 0.0, 1: 60.0, 2: 100.0}


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{lineno}: {exc}") from exc


def normalize_score(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ValueError(f"boolean score {value!r}")
    if isinstance(value, int) and value in PHI:
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.upper() in {"N/A", "NA"}:
            return "N/A"
        if value in {"0", "1", "2"}:
            return int(value)
    raise ValueError(f"invalid score {value!r}")


def validate_judge(judge: Any, origin: str) -> dict[str, Any]:
    if not isinstance(judge, dict):
        raise ValueError(f"{origin}: judge is not an object")
    normalized: dict[str, Any] = {}
    for dim, keys in DIMS.items():
        scores = judge.get(dim)
        reasons = judge.get(f"{dim}_reasons")
        if not isinstance(scores, dict) or set(scores) != set(keys):
            raise ValueError(
                f"{origin}: {dim} keys differ; expected {keys}, got "
                f"{sorted(scores) if isinstance(scores, dict) else type(scores).__name__}"
            )
        if not isinstance(reasons, dict) or set(reasons) != set(keys):
            raise ValueError(f"{origin}: {dim}_reasons keys differ")
        normalized[dim] = {key: normalize_score(scores[key]) for key in keys}
        normalized[f"{dim}_reasons"] = {}
        for key in keys:
            reason = reasons[key]
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{origin}: empty reason for {dim}.{key}")
            normalized[f"{dim}_reasons"][key] = reason.strip()
    return normalized


def line_score(scores: dict[str, int | str]) -> float | None:
    values = [PHI[value] for value in scores.values() if value in PHI]
    return round(sum(values) / len(values), 2) if values else None


def convert(raw: dict[str, Any], prompts: dict[str, dict[str, Any]], origin: str) -> dict[str, Any]:
    qid = raw.get("qid")
    if not isinstance(qid, str) or qid not in prompts:
        raise ValueError(f"{origin}: unknown qid {qid!r}")
    image = raw.get("image")
    if not isinstance(image, str) or not image:
        raise ValueError(f"{origin}: missing image for {qid}")
    judge = validate_judge(raw.get("judge"), origin)
    lines = {dim: line_score(judge[dim]) for dim in DIMS}
    valid_lines = [value for value in lines.values() if value is not None]
    generic = round(sum(valid_lines) / len(valid_lines), 2) if valid_lines else 0.0

    def with_reasons(dim: str) -> dict[str, dict[str, Any]]:
        return {
            key: {"score": judge[dim][key], "reason": judge[f"{dim}_reasons"][key]}
            for key in DIMS[dim]
        }

    prompt = prompts[qid]
    return {
        "qid": qid,
        "instance": prompt.get("instance"),
        "gen_prompt": prompt.get("gen_prompt"),
        "image": image,
        "alignment": with_reasons("alignment"),
        "quality": with_reasons("quality"),
        "aesthetics": with_reasons("aesthetics"),
        "alignment_score": lines["alignment"],
        "quality_score": lines["quality"],
        "aesthetic_score": lines["aesthetics"],
        "generic_score": generic,
        "judged_by": "codex-subagent-v2",
    }


def append_new_rows(rows: list[dict[str, Any]]) -> int:
    done = {row.get("qid") for row in read_jsonl(SCORES)}
    new_rows = [row for row in rows if row["qid"] not in done]
    if not new_rows:
        return 0
    with SCORES.open("a", encoding="utf-8") as fh:
        for row in new_rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return len(new_rows)


def mean(values: Iterable[float | int | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    return round(sum(valid) / len(valid), 2) if valid else None


def bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "alignment_score": mean(row.get("alignment_score") for row in rows),
        "quality_score": mean(row.get("quality_score") for row in rows),
        "aesthetic_score": mean(row.get("aesthetic_score") for row in rows),
        "generic_score": mean(row.get("generic_score") for row in rows),
    }


def build_report(prompts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = list(read_jsonl(SCORES))
    by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_combo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    distributions: dict[str, Counter[str]] = defaultdict(Counter)
    zeros: Counter[str] = Counter()
    na_count: Counter[str] = Counter()
    total_count: Counter[str] = Counter()

    for row in rows:
        prompt = prompts.get(row.get("qid"), {})
        by_level[str(prompt.get("level") or "unknown")].append(row)
        by_combo[str(prompt.get("combo_type") or "unknown")].append(row)
        for dim, keys in DIMS.items():
            values = row.get(dim) or {}
            for key in keys:
                item = values.get(key) or {}
                value = item.get("score")
                full_key = f"{dim}.{key}"
                distributions[full_key][str(value)] += 1
                total_count[full_key] += 1
                if value == 0:
                    zeros[full_key] += 1
                if value == "N/A":
                    na_count[full_key] += 1

    axis_means: dict[str, float | None] = {}
    for dim, keys in DIMS.items():
        for key in keys:
            values = []
            for row in rows:
                value = ((row.get(dim) or {}).get(key) or {}).get("score")
                if value in PHI:
                    values.append(PHI[value])
            axis_means[f"{dim}.{key}"] = mean(values)

    worst = sorted(rows, key=lambda row: (row.get("generic_score", 101), row.get("qid", "")))[:12]

    def zero_evidence(row: dict[str, Any]) -> list[dict[str, str]]:
        evidence = []
        for dim, keys in DIMS.items():
            for key in keys:
                item = ((row.get(dim) or {}).get(key) or {})
                if item.get("score") == 0:
                    evidence.append({"axis": f"{dim}.{key}", "reason": str(item.get("reason") or "")})
        return evidence
    report = {
        "schema": "focus1000-v6.0-V2-codex-subagent",
        "n": len(rows),
        "overall": bucket(rows),
        "axis_phi_means": axis_means,
        "score_distributions": {key: dict(value) for key, value in sorted(distributions.items())},
        "by_level": {key: bucket(value) for key, value in sorted(by_level.items())},
        "by_combo_type": {key: bucket(value) for key, value in sorted(by_combo.items())},
        "na_rate": {
            key: round(na_count[key] / total_count[key], 4) if total_count[key] else None
            for key in sorted(total_count)
        },
        "zero_axes_top": [
            {"axis": key, "count": count, "rate": round(count / total_count[key], 4)}
            for key, count in zeros.most_common()
        ],
        "representative_low_qids": [
            {"qid": row.get("qid"), "generic_score": row.get("generic_score"),
             "zero_evidence": zero_evidence(row)}
            for row in worst
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    overall = report["overall"]
    zero_lines = report["zero_axes_top"][:10]
    low_lines = report["representative_low_qids"]
    md = [
        "# focus1000 V2 生图判分汇总",
        "",
        f"已判 **{report['n']}** 题。总体 generic 均分 **{overall['generic_score']}**；"
        f"对齐 **{overall['alignment_score']}**、质量 **{overall['quality_score']}**、"
        f"美感 **{overall['aesthetic_score']}**。所有分数均按 φ（0→0、1→60、2→100，"
        "N/A 剔除）计算，generic 为三条维度线等权均值。",
        "",
        "## 0 分轴 Top",
        "",
    ]
    md.extend(
        f"- `{item['axis']}`：{item['count']} 题（{item['rate']:.1%}）"
        for item in zero_lines
    )
    md.extend(["", "## 代表性低分题", ""])
    for item in low_lines:
        evidence = item["zero_evidence"]
        detail = (f"；`{evidence[0]['axis']}`=0：{evidence[0]['reason']}"
                  if evidence else "；无 0 分轴，因多项 Pass 档进入低分样本")
        md.append(f"- `{item['qid']}`：generic {item['generic_score']}{detail}")
    md.extend([
        "",
        "详细逐项 reason 与打分在 `gen_scores.jsonl` 同一行；level/combo_type 分桶、"
        "各轴均值、N/A 率及完整分布见 `gen_scores.report.json`。",
        "",
    ])
    SUMMARY.write_text("\n".join(md), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate shards without writing")
    args = parser.parse_args()

    prompts = {row["prompt_id"]: row for row in read_jsonl(PROMPTS)}
    converted: list[dict[str, Any]] = []
    seen: set[str] = set()
    shards = sorted(BASE.glob("judge_raw.*.jsonl"))
    for shard in shards:
        for lineno, raw in enumerate(read_jsonl(shard), 1):
            row = convert(raw, prompts, f"{shard.name}:{lineno}")
            if row["qid"] in seen:
                raise ValueError(f"duplicate qid across shards: {row['qid']}")
            seen.add(row["qid"])
            converted.append(row)
    if args.check:
        print(json.dumps({"shards": len(shards), "valid_rows": len(converted)}, ensure_ascii=False))
        return
    added = append_new_rows(converted)
    report = build_report(prompts)
    print(json.dumps({"shards": len(shards), "valid_rows": len(converted), "added": added,
                      "score_rows": report["n"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
