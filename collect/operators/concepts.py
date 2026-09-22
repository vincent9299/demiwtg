"""Expand canonical master concepts into collection queries, without a model call."""
import re
from demiflow.data.plan import StreamStage
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PAIRS_ASSUME = 56


class ConceptSeedStage(StreamStage):
    """概念行 → 种子行集（name + aliases 机械展开，零 LLM）。

    top_n_hint 配额驱动：ceil(min_images × 1.6 / 56)，下限 1。
    """

    label = "seed"
    concurrency = 16
    queue_depth = 64

    async def __call__(self, concept: dict):
        hint = concept.get('top_n') or max(1, (concept["min_images"] * 8 + 5 * _PAIRS_ASSUME - 1)
                   // (5 * _PAIRS_ASSUME))
        seeds = [{"name": concept["name"], "query": concept["name"],
                  "lang": "zh" if _CJK_RE.search(concept["name"]) else "latin",
                  "top_n_hint": hint,
                  "taxonomy": concept.get("taxonomy") or []}]
        for a in concept["aliases"]:
            a = str(a).strip()
            if not a or a == concept["name"]:
                continue
            seeds.append({"name": concept["name"], "query": a,
                          "lang": "zh" if _CJK_RE.search(a) else "latin",
                          "top_n_hint": hint,
                          "taxonomy": concept.get("taxonomy") or []})
        return seeds
