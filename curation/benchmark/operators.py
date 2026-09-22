"""Benchmark pipeline operators."""
from pathlib import Path

from curation.preparation.materials import model_content, pixels
from curation.preparation.records import digest
from curation.benchmark.retrieval import retrieve_public

PROMPTS = Path(__file__).parent / "prompts"
TASK_CHECKS = ("grounding", "knowledge_necessary", "observable", "nonleaking", "reasonable_preservation")
TARGET_CHECKS = ("instruction_satisfied", "all_criteria_satisfied", "image_quality_usable", "edit_correspondence", "preservation")
FORBIDDEN = {"combo_type", "premise_types", "premise_count", "hop_count", "weakness_product"}


def fail(row, status, reason):
    return {**row, "status": status, "issues": row.get("issues", []) + [reason]}






def contains_forbidden(value):
    if isinstance(value, dict):
        return bool(FORBIDDEN.intersection(value)) or any(contains_forbidden(v) for v in value.values())
    return isinstance(value, list) and any(contains_forbidden(v) for v in value)


class ValidateTask:
    def __init__(self, guard):
        self.guard = guard

    def __call__(self, row):
        if row["status"] != "constructed":
            return row
        draft = row["draft"]
        try:
            if draft.get("status") == "insufficient":
                return fail(row, "needs_materials", draft.get("reason", "Unspecified material gap"))
            if draft.get("status") != "ok" or contains_forbidden(draft):
                raise ValueError("Invalid V4 response or removed complexity fields")
            for key in ("instruction", "condition", "knowledge_application"):
                if not isinstance(draft.get(key), str) or not draft[key].strip():
                    raise ValueError("Missing " + key)
            self.guard.check_instruction(draft["instruction"], row["branch"] == "training")
            criteria = draft.get("criteria")
            if not isinstance(criteria, list) or not criteria:
                raise ValueError("Observable grounded criteria required")
            bound = []
            for criterion in criteria:
                if not all(isinstance(criterion.get(k), str) and criterion[k].strip()
                           for k in ("requirement", "observable_region", "allowed_variation")):
                    raise ValueError("Criterion must specify observation and legal variation")
                numbers = criterion.get("evidence")
                if not isinstance(numbers, list) or not numbers or any(type(n) is not int or not 1 <= n <= len(row["materials"]) for n in numbers):
                    raise ValueError("Invalid evidence number")
                bound.append({**criterion, "knowledge_ids": [row["materials"][n-1]["item_id"] for n in numbers]})
            if row["plan"]["task_type"] == "edit":
                if draft.get("edit_type") not in {"adjust", "replace", "add", "action", "remove", "background", "style"}:
                    raise ValueError("Unsupported V4 edit type")
                if (not isinstance(draft.get("anchor"), str) or not draft["anchor"].strip() or
                        not isinstance(draft.get("preserve"), list) or not draft["preserve"] or
                        any(not isinstance(x, str) or not x.strip() for x in draft["preserve"])):
                    raise ValueError("Edit requires a visible anchor and preservation requirements")
            elif draft.get("edit_type") is not None or draft.get("preserve"):
                raise ValueError("T2I cannot carry edit obligations")
            return {**row, "status": "valid_task", "criteria": bound, "task_sha256": digest(draft)}
        except (ValueError, TypeError, KeyError) as error:
            return fail(row, "invalid_task", str(error))


def review_pass(result, names):
    return (isinstance(result, dict) and bool(result.get("reason")) and
            all(result.get("checks", {}).get(name) is True for name in names))


def accept_task(row):
    if row["status"] != "task_reviewed":
        return row
    if not review_pass(row["review_task"], TASK_CHECKS):
        return fail(row, "rejected_task", row["review_task"].get("reason", "Task review failed"))
    if row["plan"]["split"] == "test" and row["review_task_provenance"]["reviewer_kind"] not in {"human", "independent"}:
        return fail(row, "needs_independent_review", "Formal tests require an independent review")
    return {**row, "status": "accepted_task", "certification": "reviewed_candidate_not_golden"}




class RetrieveForAnswer:
    def __init__(self, items, guard):
        self.items, self.guard = items, guard

    def __call__(self, row):
        if row["status"] != "accepted_task":
            return row
        if row['branch'] == 'training':
            raise ValueError('Training uses BindTrainingInputs; do not replace selected references by retrieval')
        excluded = list(row.get('design_targets', []))
        for key in ("target", "edit_source"):
            if row["plan"].get(key):
                asset = row["plan"][key]
                _, _, dhash = pixels(asset)
                excluded.append({**asset, "dhash": dhash})
        # The ONLY query is the public task. No criteria, answer, target caption,
        # construction intent or hidden knowledge IDs participate in ranking.
        query = row["draft"]["instruction"]
        selected, trace = retrieve_public(self.items, query, self.guard, excluded=excluded,
                                   training=row["branch"] == "training")
        trace.update(phase="answer", actual_retrieval=True, query_fields=["instruction"])
        required = {key for criterion in row["criteria"] for key in criterion["knowledge_ids"]}
        trace["missing_construction_evidence_ids"] = sorted(required - {item["item_id"] for item in selected})
        content, mapping = model_content(query, selected, row.get("edit_source"))
        return {**row, "answer_materials": selected, "answer_retrieval": trace,
                "answer_input": {"messages": [{"role": "user", "content": content}], "image_roles": mapping},
                "retrieval_gap": not bool(selected) or bool(trace["missing_construction_evidence_ids"])}






def export_record(row):
    ready = row["status"] == 'accepted_task'
    result = {**row, "export_ready": ready}
    if not ready:
        return result
    result["benchmark_question"] = {"schema": "v4-benchmark-question/1", "pipeline_version": "V2", "task_id": row["task_id"],
        "task_type": row["plan"]["task_type"], "split": row["plan"]["split"],
        "instruction": row["draft"]["instruction"], "criteria": row["criteria"],
        "edit_type": row["draft"].get("edit_type"), "edit_source": row.get("edit_source"),
        "knowledge_diagnostics": row["draft"]["knowledge_application"],
        "scoring": "existing main scoring unchanged; knowledge diagnostics separate"}
    return result
