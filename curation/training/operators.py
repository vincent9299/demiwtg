"""Training pipeline operators."""
from pathlib import Path

from curation.preparation.materials import asset_for_item, duplicate, model_content
from curation.preparation.records import digest

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


class BindTrainingInputs:
    """Validate and assemble the references fixed during task design; no retrieval."""
    def __init__(self, guard):
        self.guard = guard

    def __call__(self, row):
        if row['status'] != 'accepted_task':
            return row
        from curation.preparation.materials import reference_options
        try:
            if row['branch'] != 'training':
                raise ValueError('Bound training inputs require a training task')
            selected = row['materials']
            binding = row['training_input_binding']
            ids = [m['item_id'] for m in selected]
            if ids != binding['item_ids'] or digest(selected) != binding['materials_sha256']:
                raise ValueError('Selected training materials changed after task design')
            required = {key for criterion in row['criteria'] for key in criterion['knowledge_ids']}
            if not selected or not required <= set(ids):
                raise ValueError('Selected training materials do not cover criterion evidence')
            excluded = list(row.get('design_targets', []))
            if row.get('edit_source'):
                excluded.append(row['edit_source'])
            if len(reference_options(selected, excluded)['evidence']) != len(selected):
                raise ValueError('Bound references conflict with target/source or required figure')
            if not all(self.guard.allows_item(m, True) for m in selected):
                raise ValueError('Bound training materials violate formal-test reservation')
            content, mapping = model_content(row['draft']['instruction'], selected, row.get('edit_source'))
            return {**row, 'answer_materials': selected,
                    'answer_input': {'messages': [{'role': 'user', 'content': content}], 'image_roles': mapping}}
        except (ValueError, TypeError, KeyError, OSError) as error:
            return fail(row, 'invalid_training_inputs', str(error))






def accept_target(row):
    if row["status"] != "target_reviewed":
        return row
    if not review_pass(row["review_target"], TARGET_CHECKS):
        return fail(row, "rejected_target", row["review_target"].get("reason", "Target review failed"))
    if any(m['kind'] == 'image' and duplicate(row['target'], m['asset'])
           for m in row.get('materials', []) + row.get('answer_materials', [])):
        return fail(row, 'rejected_target', 'Target/near duplicate leaked into references')
    return {**row, "status": "training_ready"}


def export_record(row):
    ready = row["status"] == 'training_ready'
    result = {**row, "export_ready": ready}
    if not ready:
        return result
    sequence = [{"type": "text", "role": "instruction", "text": row["draft"]["instruction"], "loss": False}]
    for item in row.get("answer_materials", []):
        if item["kind"] == "text":
            sequence.append({"type": "text", "role": "knowledge", "text": item["text"], "loss": False})
        else:
            sequence.append({"type": "image", **asset_for_item(item), "loss": False})
    if row.get("edit_source"):
        sequence.append({"type": "image", **row["edit_source"], "loss": False})
    sequence.append({"type": "image", **row["target"], "loss": True})
    result["training_sample"] = {"schema": "v4-knowledge-conditioned-sequence/1", "pipeline_version":"V2", "task_id": row["task_id"],
                                 "sample_id": 'sample_' + digest([row['task_id'], row['target']['sha256']])[:24],
                                 "training_input_binding": row.get('training_input_binding'),
                                 "knowledge_version":row.get('knowledge_version'),
                                 "task_sha256":row.get('task_sha256'),
                                 "task_image_roles":{"target_sha256":row['target']['sha256'],
                                     "reference_sha256":[m['asset']['sha256'] for m in row.get('answer_materials',[]) if m['kind']=='image'],
                                     "target_design_binding":row.get('target_design_binding')},
                                 "sequence": sequence, "loader_status": "neutral_contract_not_BAGEL_adapter"}
    return result
