"""Explicit concept source conversion; reads historical concept exports."""
import json
from pathlib import Path
def normalize_carriers(value):
    if isinstance(value, list) and all(x in {'image', 'text'} for x in value):
        return list(dict.fromkeys(value))
    text = ''.join(value) if isinstance(value, list) else value
    if text not in {'image', 'text', 'image+text'}:
        raise ValueError('Unknown concept carriers: ' + repr(value))
    return text.split('+')

def iter_concept_rows(dataset_tree: Path, migrated_at_us: int, stats: dict):
    source = dataset_tree / "meta" / "concepts.json"
    meta = json.loads(source.read_text(encoding="utf-8"))
    concepts = meta.get("concepts") or []
    stats["declared"] = len(concepts)
    for index, concept in enumerate(concepts):
        name = concept.get("name")
        if not isinstance(name, str) or not name:
            stats["nameless"] += 1
            continue
        stats["rows"] += 1
        yield {
            "name": name,
            "aliases": concept.get("aliases"),
            "carriers": normalize_carriers(concept.get("carriers")),
            "taxonomy": (
                json.dumps(concept.get("taxonomy"), ensure_ascii=False, sort_keys=True)
                if concept.get("taxonomy") is not None else None
            ),
            "source_file": "meta/concepts.json",
            "source_row": index,
            "raw_payload": json.dumps(concept, ensure_ascii=False, sort_keys=True),
            "migrated_at_us": migrated_at_us,
        }
