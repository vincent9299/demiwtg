#!/usr/bin/env python3
"""Portable single-file reference for Safe-Gated AEPGS.

Purpose
=======
This file is a readable, self-contained transfer artifact for moving the
Safe-Gated Adaptive Expected Pruning-Gain Search design into another project.
It does not replace the verified modular implementation in ``src/wlo_pipeline``.

Sections
========
CURRENT_VERIFIED
    Relation state, safe gates, closure, transitive reduction, closure gain,
    versioned priority queue, frozen waves, snapshots, and SQLite task state.

VNEXT_REFERENCE
    Exact GraphDelta for reference-sized graphs, PendingCandidateIndex,
    inferred-before-model pruning, QueuePruneGain, and hybrid scoring.

PROJECT_ADAPTERS
    Probability-provider interface, the current 16 deployable features,
    evidence-only fallback, optional sklearn model loading/training, and an
    abstract async model adapter.

SELF_TESTS
    Deterministic tests for closure expansion, component merging, candidate
    cancellation, reduction, snapshots, queue versioning, and SQLite recovery.

Scalability boundary
====================
The exact GraphDelta implementation compares all original Label pairs and is
intentionally a correctness oracle for tests and small/medium pilots. A
million-Label deployment must replace it with component deltas, dynamic
reachability, and a persistent reverse candidate index, as described in:

    docs/safe_gated_aepgs_priority_system_complete_20260809.md
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import hashlib
import heapq
import itertools
import json
import math
import os
import pickle
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


PORTABLE_SCHEMA_VERSION = "safe-gated-aepgs-portable-v1"
OUTCOME_SCHEMA_VERSION = "safe-gated-aepgs-outcomes-v1"
FEATURE_SCHEMA_VERSION = "safe-gated-aepgs-features-v1"


# =============================================================================
# CURRENT_VERIFIED: contracts and graph primitives
# =============================================================================


class Relation(str, Enum):
    EQUIVALENT_TO = "EQUIVALENT_TO"
    BROADER_THAN = "BROADER_THAN"
    NONE = "NONE"
    NONE_DISJOINT_SUBTREES = "NONE_DISJOINT_SUBTREES"
    UNCERTAIN = "UNCERTAIN"


class Outcome(str, Enum):
    EQUIVALENT_TO = "EQUIVALENT_TO"
    A_BROADER_THAN_B = "A_BROADER_THAN_B"
    B_BROADER_THAN_A = "B_BROADER_THAN_A"
    NONE_PAIR_ONLY = "NONE_PAIR_ONLY"
    NONE_DISJOINT_SUBTREES = "NONE_DISJOINT_SUBTREES"
    UNCERTAIN = "UNCERTAIN"


PREDICTED_OUTCOMES = tuple(Outcome)
OUTCOME_VALUES = tuple(value.value for value in PREDICTED_OUTCOMES)
HIGH_RISK_RELATIONS = {
    Relation.EQUIVALENT_TO,
    Relation.NONE_DISJOINT_SUBTREES,
}


@dataclass(frozen=True)
class PairDecision:
    pair_id: str
    label_a_id: str
    label_b_id: str
    first_label_id: str
    relation: Relation
    second_label_id: str
    model: str = ""

    def validate(self) -> None:
        if not self.pair_id:
            raise ValueError("pair_id must be non-empty")
        if self.label_a_id == self.label_b_id:
            raise ValueError("decision pair must have two endpoints")
        if {self.first_label_id, self.second_label_id} != {
            self.label_a_id,
            self.label_b_id,
        }:
            raise ValueError("decision changed pair endpoints")
        if self.relation != Relation.BROADER_THAN and (
            self.first_label_id,
            self.second_label_id,
        ) != (self.label_a_id, self.label_b_id):
            raise ValueError("non-directional decision reversed endpoints")


@dataclass(frozen=True)
class CandidateProbabilities:
    equivalent: float
    a_broader_b: float
    b_broader_a: float
    none: float
    disjoint: float
    uncertain: float

    def values(self) -> tuple[float, ...]:
        return (
            self.equivalent,
            self.a_broader_b,
            self.b_broader_a,
            self.none,
            self.disjoint,
            self.uncertain,
        )

    def validate(self) -> None:
        values = self.values()
        if any(not math.isfinite(value) for value in values):
            raise ValueError("probabilities must be finite")
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("probabilities must be in [0,1]")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-8):
            raise ValueError("probabilities must sum to one")

    def expected_model_cost(self) -> float:
        """Reference cost: one Generator plus expected high-risk Reviewer."""
        self.validate()
        return 1.0 + self.equivalent + self.disjoint


@dataclass(frozen=True)
class CandidateTask:
    pair_id: str
    label_a_id: str
    label_b_id: str
    probabilities: CandidateProbabilities
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.pair_id:
            raise ValueError("candidate pair_id must be non-empty")
        if self.label_a_id == self.label_b_id:
            raise ValueError("candidate endpoints must differ")
        self.probabilities.validate()


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    expected_gain: float
    expected_cost: float
    gains: Mapping[str, float]


@dataclass
class WaveCommitReport:
    graph_version_before: int
    graph_version_after: int
    accepted: list[str] = field(default_factory=list)
    inferred: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    review_required: list[str] = field(default_factory=list)
    gated_relations: dict[str, str] = field(default_factory=dict)
    reduced_edge_count: int = 0
    accepted_broader_assertion_count: int = 0


@dataclass(frozen=True)
class InferenceProof:
    pair_id: str
    relation: Relation
    broader_direction: tuple[str, str] | None
    graph_version: int
    source: str = "SAFE_RELATION_ENGINE"

    def as_json(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "relation": self.relation.value,
            "broader_direction": (
                list(self.broader_direction) if self.broader_direction else None
            ),
            "graph_version": self.graph_version,
            "source": self.source,
        }


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    if left == right:
        raise ValueError("self-pair is not canonical")
    return (left, right) if left < right else (right, left)


def stable_pair_id(left: str, right: str, prefix: str = "P") -> str:
    a, b = canonical_pair(left, right)
    digest = hashlib.sha256(f"{a}\0{b}".encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def dag_closure(
    nodes: Iterable[str], edges: Iterable[tuple[str, str]]
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[str]]:
    node_values = sorted(set(nodes))
    children: dict[str, set[str]] = {node: set() for node in node_values}
    parents: dict[str, set[str]] = {node: set() for node in node_values}
    for parent, child in set(edges):
        if parent == child:
            raise ValueError("BROADER_THAN self-loop")
        if parent not in children or child not in children:
            raise ValueError("BROADER_THAN endpoint is not a current component")
        children[parent].add(child)
        parents[child].add(parent)
    indegree = {node: len(parents[node]) for node in node_values}
    ready = deque(sorted(node for node in node_values if indegree[node] == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(order) != len(node_values):
        raise ValueError("BROADER_THAN graph contains a cycle")
    ancestors = {node: set() for node in node_values}
    for node in order:
        for parent in parents[node]:
            ancestors[node].add(parent)
            ancestors[node].update(ancestors[parent])
    descendants = {node: set() for node in node_values}
    for node in reversed(order):
        for child in children[node]:
            descendants[node].add(child)
            descendants[node].update(descendants[child])
    return ancestors, descendants, order


def transitive_reduction(
    nodes: Iterable[str], edges: Iterable[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Return the unique transitive reduction of a DAG."""
    node_values = set(nodes)
    edge_values = set(edges)
    dag_closure(node_values, edge_values)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for parent, child in edge_values:
        adjacency[parent].add(child)
    reduced = set(edge_values)
    for parent, child in sorted(edge_values):
        seen = {parent}
        stack = [node for node in adjacency[parent] if node != child]
        reachable = False
        while stack and not reachable:
            node = stack.pop()
            if node == child:
                reachable = True
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adjacency.get(node, ()))
        if reachable:
            reduced.discard((parent, child))
    return reduced


class SafeRelationEngine:
    """Reference safe graph state for original Labels and equivalent components."""

    def __init__(self, label_ids: Iterable[str]) -> None:
        labels = tuple(sorted(set(label_ids)))
        if not labels:
            raise ValueError("relation engine requires labels")
        self.labels = labels
        self.uf_parent = {label: label for label in labels}
        self.component_size = {label: 1 for label in labels}
        self.members = {label: {label} for label in labels}
        self.broader_assertions: set[tuple[str, str]] = set()
        self.none_assertions: set[tuple[str, str]] = set()
        self.disjoint_assertions: set[tuple[str, str]] = set()
        self._mapped_none_cache: set[tuple[str, str]] | None = None
        self._mapped_disjoint_cache: set[tuple[str, str]] | None = None
        self.reduced_edges: set[tuple[str, str]] = set()
        self.ancestors: dict[str, set[str]] = {label: set() for label in labels}
        self.descendants: dict[str, set[str]] = {label: set() for label in labels}
        self.version = 0
        self.raw_decision_ledger: list[dict[str, Any]] = []

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(sorted(self.component_size))

    def find(self, label_id: str) -> str:
        if label_id not in self.uf_parent:
            raise KeyError(f"unknown Label: {label_id}")
        root = label_id
        while self.uf_parent[root] != root:
            root = self.uf_parent[root]
        while self.uf_parent[label_id] != label_id:
            parent = self.uf_parent[label_id]
            self.uf_parent[label_id] = root
            label_id = parent
        return root

    def infer(self, left: str, right: str) -> Relation | None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return Relation.EQUIVALENT_TO
        if b in self.descendants[a] or a in self.descendants[b]:
            return Relation.BROADER_THAN
        if self._covered_by_disjoint(a, b):
            return Relation.NONE
        if canonical_pair(a, b) in self._mapped_none_pairs():
            return Relation.NONE
        return None

    def broader_direction(self, left: str, right: str) -> tuple[str, str] | None:
        a, b = self.find(left), self.find(right)
        if b in self.descendants[a]:
            return a, b
        if a in self.descendants[b]:
            return b, a
        return None

    def inference_proof(self, pair_id: str, left: str, right: str) -> InferenceProof | None:
        relation = self.infer(left, right)
        if relation is None:
            return None
        return InferenceProof(
            pair_id=pair_id,
            relation=relation,
            broader_direction=self.broader_direction(left, right),
            graph_version=self.version,
        )

    def _mapped_edges(self, extra: tuple[str, str] | None = None) -> set[tuple[str, str]]:
        values = {
            (self.find(parent), self.find(child))
            for parent, child in self.broader_assertions
            if self.find(parent) != self.find(child)
        }
        if extra is not None:
            parent, child = self.find(extra[0]), self.find(extra[1])
            if parent != child:
                values.add((parent, child))
        return values

    def _mapped_none_pairs(self) -> set[tuple[str, str]]:
        if self._mapped_none_cache is not None:
            return self._mapped_none_cache
        result = set()
        for left, right in self.none_assertions:
            a, b = self.find(left), self.find(right)
            if a != b:
                result.add(canonical_pair(a, b))
        self._mapped_none_cache = result
        return result

    def _mapped_disjoint_pairs(self) -> set[tuple[str, str]]:
        if self._mapped_disjoint_cache is not None:
            return self._mapped_disjoint_cache
        result = set()
        for left, right in self.disjoint_assertions:
            a, b = self.find(left), self.find(right)
            if a != b:
                result.add(canonical_pair(a, b))
        self._mapped_disjoint_cache = result
        return result

    def _invalidate_negative_caches(self) -> None:
        self._mapped_none_cache = None
        self._mapped_disjoint_cache = None

    def _covered_by_disjoint(self, a: str, b: str) -> bool:
        disjoint = self._mapped_disjoint_pairs()
        return any(
            canonical_pair(x, y) in disjoint
            for x in self.ancestors[a] | {a}
            for y in self.ancestors[b] | {b}
            if x != y
        )

    def _validate_state(self, extra_edge: tuple[str, str] | None = None) -> str | None:
        edges = self._mapped_edges(extra_edge)
        try:
            _, descendants, _ = dag_closure(self.roots, edges)
        except ValueError as exc:
            return str(exc)
        for pair in self._mapped_none_pairs():
            left, right = pair
            if right in descendants[left] or left in descendants[right]:
                return f"BROADER_THAN conflicts with NONE {pair}"
        for left, right in self._mapped_disjoint_pairs():
            if right in descendants[left] or left in descendants[right]:
                return f"BROADER_THAN conflicts with DISJOINT {(left, right)}"
            if (descendants[left] | {left}) & (descendants[right] | {right}):
                return f"DISJOINT scopes overlap {(left, right)}"
        return None

    def _rebuild(self) -> None:
        mapped = self._mapped_edges()
        error = self._validate_state()
        if error:
            raise ValueError(error)
        self.reduced_edges = transitive_reduction(self.roots, mapped)
        self.ancestors, self.descendants, _ = dag_closure(
            self.roots, self.reduced_edges
        )

    def _can_union(
        self, left: str, right: str, wave_forbidden: set[tuple[str, str]]
    ) -> str | None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return None
        for x in self.members[a]:
            for y in self.members[b]:
                if canonical_pair(x, y) in wave_forbidden:
                    return "same-wave non-equivalent evidence"
        if b in self.descendants[a] or a in self.descendants[b]:
            return "existing BROADER_THAN relation"
        if canonical_pair(a, b) in self._mapped_none_pairs():
            return "existing NONE relation"
        if canonical_pair(a, b) in self._mapped_disjoint_pairs():
            return "existing DISJOINT relation"

        # Simulate the merge and restore every touched Union-Find parent.
        keep, drop = sorted((a, b))
        drop_members = set(self.members[drop])
        old_drop_parents = {label: self.uf_parent[label] for label in drop_members}
        old_size = self.component_size[keep]
        old_members = set(self.members[keep])
        self._invalidate_negative_caches()
        self.uf_parent[drop] = keep
        self.component_size[keep] += self.component_size.pop(drop)
        self.members[keep].update(self.members.pop(drop))
        error = self._validate_state()
        self.members[drop] = self.members[keep] - old_members
        self.members[keep] = old_members
        self.component_size[drop] = self.component_size[keep] - old_size
        self.component_size[keep] = old_size
        for label, parent in old_drop_parents.items():
            self.uf_parent[label] = parent
        self._invalidate_negative_caches()
        return error

    def _union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        keep, drop = sorted((a, b))
        self._invalidate_negative_caches()
        self.uf_parent[drop] = keep
        self.component_size[keep] += self.component_size.pop(drop)
        self.members[keep].update(self.members.pop(drop))
        self._rebuild()

    def _add_none(self, left: str, right: str) -> str | None:
        if self.infer(left, right) in {Relation.EQUIVALENT_TO, Relation.BROADER_THAN}:
            return "NONE conflicts with an existing positive relation"
        self.none_assertions.add(canonical_pair(left, right))
        self._invalidate_negative_caches()
        return None

    def _add_disjoint(self, left: str, right: str) -> str | None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return "DISJOINT conflicts with EQUIVALENT_TO"
        if b in self.descendants[a] or a in self.descendants[b]:
            return "DISJOINT conflicts with BROADER_THAN"
        if (self.descendants[a] | {a}) & (self.descendants[b] | {b}):
            return "DISJOINT scopes already overlap"
        self.disjoint_assertions.add(canonical_pair(left, right))
        self._invalidate_negative_caches()
        return None

    def _add_broader(self, parent: str, child: str) -> tuple[str, bool]:
        a, b = self.find(parent), self.find(child)
        if a == b:
            return "BROADER_THAN conflicts with EQUIVALENT_TO", False
        if b in self.descendants[a]:
            self.broader_assertions.add((parent, child))
            return "", True
        error = self._validate_state((parent, child))
        if error:
            return error, False
        self.broader_assertions.add((parent, child))
        self._rebuild()
        return "", False

    @staticmethod
    def _gate(
        generator: PairDecision, reviewer: PairDecision | None
    ) -> tuple[Relation | None, str]:
        relation = generator.relation
        if relation == Relation.EQUIVALENT_TO:
            if reviewer is None:
                return None, "REVIEW_REQUIRED"
            if reviewer.relation == Relation.EQUIVALENT_TO:
                return Relation.EQUIVALENT_TO, ""
            return Relation.UNCERTAIN, "REVIEWER_DISAGREED_EQUIVALENT"
        if relation == Relation.NONE_DISJOINT_SUBTREES:
            if reviewer is None:
                return None, "REVIEW_REQUIRED"
            if reviewer.relation == Relation.NONE_DISJOINT_SUBTREES:
                return Relation.NONE_DISJOINT_SUBTREES, ""
            if reviewer.relation == Relation.NONE:
                return Relation.NONE, "REVIEWER_DOWNGRADED_TO_NONE"
            return Relation.UNCERTAIN, "REVIEWER_DISAGREED_DISJOINT"
        return relation, ""

    def apply_wave(
        self,
        generators: Sequence[PairDecision],
        reviewers: Mapping[str, PairDecision] | None = None,
    ) -> WaveCommitReport:
        reviewers = reviewers or {}
        report = WaveCommitReport(self.version, self.version)
        generated: dict[str, PairDecision] = {}
        effective: dict[str, Relation] = {}
        gate_notes: dict[str, str] = {}

        for decision in generators:
            decision.validate()
            if decision.pair_id in generated:
                raise ValueError(f"duplicate generator decision {decision.pair_id}")
            generated[decision.pair_id] = decision
            reviewer = reviewers.get(decision.pair_id)
            if reviewer is not None:
                reviewer.validate()
                if {reviewer.label_a_id, reviewer.label_b_id} != {
                    decision.label_a_id,
                    decision.label_b_id,
                }:
                    raise ValueError("reviewer changed pair identity")
            relation, note = self._gate(decision, reviewer)
            self.raw_decision_ledger.append(
                {
                    "graph_version": self.version,
                    "pair_id": decision.pair_id,
                    "generator_relation": decision.relation.value,
                    "reviewer_relation": reviewer.relation.value if reviewer else None,
                    "gate_note": note,
                }
            )
            if relation is None:
                report.review_required.append(decision.pair_id)
                continue
            effective[decision.pair_id] = relation
            gate_notes[decision.pair_id] = note
            report.gated_relations[decision.pair_id] = relation.value

        wave_forbidden = {
            canonical_pair(
                generated[pair_id].label_a_id,
                generated[pair_id].label_b_id,
            )
            for pair_id, relation in effective.items()
            if relation
            in {
                Relation.BROADER_THAN,
                Relation.NONE,
                Relation.NONE_DISJOINT_SUBTREES,
            }
        }

        # Equality first, constrained by every non-equivalent result in the wave.
        for pair_id in sorted(effective):
            if effective[pair_id] != Relation.EQUIVALENT_TO:
                continue
            decision = generated[pair_id]
            reason = self._can_union(
                decision.label_a_id, decision.label_b_id, wave_forbidden
            )
            if reason:
                report.rejected[pair_id] = reason
                continue
            already = self.find(decision.label_a_id) == self.find(decision.label_b_id)
            self._union(decision.label_a_id, decision.label_b_id)
            (report.inferred if already else report.accepted).append(pair_id)

        for wanted in (Relation.NONE, Relation.NONE_DISJOINT_SUBTREES):
            for pair_id in sorted(effective):
                if effective[pair_id] != wanted:
                    continue
                decision = generated[pair_id]
                reason = (
                    self._add_none(decision.label_a_id, decision.label_b_id)
                    if wanted == Relation.NONE
                    else self._add_disjoint(
                        decision.label_a_id, decision.label_b_id
                    )
                )
                if reason:
                    report.rejected[pair_id] = reason
                else:
                    report.accepted.append(pair_id)

        for pair_id in sorted(effective):
            if effective[pair_id] != Relation.BROADER_THAN:
                continue
            decision = generated[pair_id]
            reason, redundant = self._add_broader(
                decision.first_label_id, decision.second_label_id
            )
            if reason:
                report.rejected[pair_id] = reason
            elif redundant:
                report.inferred.append(pair_id)
            else:
                report.accepted.append(pair_id)

        for pair_id, relation in effective.items():
            if relation == Relation.UNCERTAIN:
                report.uncertain.append(pair_id)
            elif gate_notes.get(pair_id):
                # Downgrades remain auditable in raw_decision_ledger.
                pass

        self._rebuild()
        self.version += 1
        report.graph_version_after = self.version
        report.reduced_edge_count = len(self.reduced_edges)
        report.accepted_broader_assertion_count = len(self.broader_assertions)
        return report

    def snapshot(self) -> dict[str, Any]:
        groups = [
            sorted(values) for values in self.members.values() if len(values) > 1
        ]
        return {
            "schema_version": "safe-gated-relation-engine-v1",
            "graph_version": self.version,
            "labels": list(self.labels),
            "equivalence_groups": sorted(groups),
            "broader_assertions": [
                list(value) for value in sorted(self.broader_assertions)
            ],
            "none_assertions": [list(value) for value in sorted(self.none_assertions)],
            "disjoint_assertions": [
                list(value) for value in sorted(self.disjoint_assertions)
            ],
            "raw_decision_ledger": list(self.raw_decision_ledger),
        }

    @classmethod
    def from_snapshot(cls, value: Mapping[str, Any]) -> "SafeRelationEngine":
        if value.get("schema_version") != "safe-gated-relation-engine-v1":
            raise ValueError("unsupported relation engine snapshot")
        engine = cls(str(label) for label in value["labels"])
        for group in value.get("equivalence_groups", []):
            labels = [str(label) for label in group]
            for label in labels[1:]:
                engine._union(labels[0], label)
        engine.broader_assertions = {
            (str(left), str(right))
            for left, right in value.get("broader_assertions", [])
        }
        engine.none_assertions = {
            canonical_pair(str(left), str(right))
            for left, right in value.get("none_assertions", [])
        }
        engine.disjoint_assertions = {
            canonical_pair(str(left), str(right))
            for left, right in value.get("disjoint_assertions", [])
        }
        engine._invalidate_negative_caches()
        engine.raw_decision_ledger = list(value.get("raw_decision_ledger", []))
        engine.version = int(value["graph_version"])
        engine._rebuild()
        return engine

    def estimate_gain(self, left: str, right: str, outcome: str) -> int:
        a, b = self.find(left), self.find(right)
        if a == b or self.infer(left, right) is not None:
            return 0
        if outcome == "NONE":
            return self.component_size[a] * self.component_size[b]
        if outcome == "A_BROADER_THAN_B":
            return self._broader_gain(a, b)
        if outcome == "B_BROADER_THAN_A":
            return self._broader_gain(b, a)
        if outcome == "DISJOINT":
            gain = 0
            for x in self.descendants[a] | {a}:
                for y in self.descendants[b] | {b}:
                    if self.infer(x, y) is None:
                        gain += self.component_size[x] * self.component_size[y]
            return gain
        if outcome == "EQUIVALENT_TO":
            gain = self.component_size[a] * self.component_size[b]
            for other in self.roots:
                if other in {a, b}:
                    continue
                relation_a = self.infer(a, other)
                relation_b = self.infer(b, other)
                if (relation_a is None) != (relation_b is None):
                    missing_size = self.component_size[b if relation_a else a]
                    gain += missing_size * self.component_size[other]
            return gain
        if outcome == "UNCERTAIN":
            return 0
        raise ValueError(f"unknown gain outcome: {outcome}")

    def known_pair_counts(self) -> dict[str, int]:
        equivalent = sum(
            size * (size - 1) // 2 for size in self.component_size.values()
        )
        broader = sum(
            self.component_size[parent] * self.component_size[child]
            for parent in self.roots
            for child in self.descendants[parent]
        )
        none_component_pairs = set(self._mapped_none_pairs())
        for left, right in self._mapped_disjoint_pairs():
            for x in self.descendants[left] | {left}:
                for y in self.descendants[right] | {right}:
                    if x != y:
                        none_component_pairs.add(canonical_pair(x, y))
        none = sum(
            self.component_size[left] * self.component_size[right]
            for left, right in none_component_pairs
        )
        return {
            "EQUIVALENT_TO": equivalent,
            "BROADER_THAN": broader,
            "NONE": none,
            "TOTAL": equivalent + broader + none,
        }

    def _broader_gain(self, parent: str, child: str) -> int:
        if parent == child or parent in self.descendants[child]:
            return 0
        gain = 0
        for ancestor in self.ancestors[parent] | {parent}:
            for descendant in self.descendants[child] | {child}:
                if descendant not in self.descendants[ancestor]:
                    gain += (
                        self.component_size[ancestor]
                        * self.component_size[descendant]
                    )
        return gain


class SingleGainScorer:
    """Current verified score: expected closure gain per expected model call."""

    def score(self, engine: SafeRelationEngine, task: CandidateTask) -> ScoreBreakdown:
        task.validate()
        p = task.probabilities
        gains = {
            "EQUIVALENT_TO": engine.estimate_gain(
                task.label_a_id, task.label_b_id, "EQUIVALENT_TO"
            ),
            "A_BROADER_THAN_B": engine.estimate_gain(
                task.label_a_id, task.label_b_id, "A_BROADER_THAN_B"
            ),
            "B_BROADER_THAN_A": engine.estimate_gain(
                task.label_a_id, task.label_b_id, "B_BROADER_THAN_A"
            ),
            "NONE": engine.estimate_gain(
                task.label_a_id, task.label_b_id, "NONE"
            ),
            "DISJOINT": engine.estimate_gain(
                task.label_a_id, task.label_b_id, "DISJOINT"
            ),
            "UNCERTAIN": 0,
        }
        expected_gain = (
            p.equivalent * gains["EQUIVALENT_TO"]
            + p.a_broader_b * gains["A_BROADER_THAN_B"]
            + p.b_broader_a * gains["B_BROADER_THAN_A"]
            + p.none * gains["NONE"]
            + p.disjoint * gains["DISJOINT"]
        )
        expected_cost = p.expected_model_cost()
        return ScoreBreakdown(
            score=expected_gain / expected_cost,
            expected_gain=expected_gain,
            expected_cost=expected_cost,
            gains=gains,
        )


@dataclass
class _QueueRecord:
    task: CandidateTask
    graph_version: int
    model_version: str
    score: float
    generation: int


class VersionedPriorityQueue:
    """Lazy-versioned max-priority queue with exact infer-before-pop checks."""

    def __init__(self) -> None:
        self._records: dict[str, _QueueRecord] = {}
        self._heap: list[tuple[float, str, int]] = []
        self.inferred: dict[str, Relation] = {}

    def __len__(self) -> int:
        return len(self._records)

    def contains(self, pair_id: str) -> bool:
        return pair_id in self._records

    def cancel(self, pair_id: str) -> None:
        # Heap entries become stale because _records no longer contains pair_id.
        self._records.pop(pair_id, None)

    def upsert(
        self,
        task: CandidateTask,
        engine: SafeRelationEngine,
        scorer: Any,
        model_version: str,
        candidate_index: "PendingCandidateIndex | None" = None,
    ) -> None:
        relation = engine.infer(task.label_a_id, task.label_b_id)
        if relation is not None:
            self.inferred[task.pair_id] = relation
            self._records.pop(task.pair_id, None)
            return
        breakdown = (
            scorer.score(engine, task, candidate_index)
            if candidate_index is not None and isinstance(scorer, HybridGainScorer)
            else scorer.score(engine, task)
        )
        previous = self._records.get(task.pair_id)
        generation = 1 if previous is None else previous.generation + 1
        record = _QueueRecord(
            task=task,
            graph_version=engine.version,
            model_version=model_version,
            score=float(breakdown.score),
            generation=generation,
        )
        self._records[task.pair_id] = record
        heapq.heappush(self._heap, (-record.score, task.pair_id, generation))

    def pop_ready(
        self,
        engine: SafeRelationEngine,
        scorer: Any,
        model_version: str,
        refresh_probabilities: Callable[[CandidateTask], CandidateTask] | None = None,
        candidate_index: "PendingCandidateIndex | None" = None,
    ) -> CandidateTask | None:
        while self._heap:
            _, pair_id, generation = heapq.heappop(self._heap)
            record = self._records.get(pair_id)
            if record is None or record.generation != generation:
                continue
            relation = engine.infer(
                record.task.label_a_id, record.task.label_b_id
            )
            if relation is not None:
                self.inferred[pair_id] = relation
                del self._records[pair_id]
                continue
            if (
                record.graph_version != engine.version
                or record.model_version != model_version
            ):
                task = (
                    refresh_probabilities(record.task)
                    if refresh_probabilities is not None
                    else record.task
                )
                self.upsert(
                    task,
                    engine,
                    scorer,
                    model_version,
                    candidate_index,
                )
                continue
            del self._records[pair_id]
            return record.task
        return None


@dataclass(frozen=True)
class FrozenWave:
    wave_id: int
    graph_version: int
    model_version: str
    tasks: tuple[CandidateTask, ...]


class FrozenWavePlanner:
    def __init__(self) -> None:
        self.next_wave_id = 0

    def plan(
        self,
        queue: VersionedPriorityQueue,
        engine: SafeRelationEngine,
        scorer: Any,
        model_version: str,
        wave_size: int,
        refresh_probabilities: Callable[[CandidateTask], CandidateTask] | None = None,
        candidate_index: "PendingCandidateIndex | None" = None,
    ) -> FrozenWave:
        if wave_size <= 0:
            raise ValueError("wave_size must be positive")
        tasks: list[CandidateTask] = []
        while len(tasks) < wave_size:
            task = queue.pop_ready(
                engine,
                scorer,
                model_version,
                refresh_probabilities,
                candidate_index,
            )
            if task is None:
                break
            tasks.append(task)
        wave = FrozenWave(
            wave_id=self.next_wave_id,
            graph_version=engine.version,
            model_version=model_version,
            tasks=tuple(tasks),
        )
        self.next_wave_id += 1
        return wave

    @staticmethod
    def commit(
        engine: SafeRelationEngine,
        wave: FrozenWave,
        generators: Sequence[PairDecision],
        reviewers: Mapping[str, PairDecision] | None = None,
    ) -> WaveCommitReport:
        if wave.graph_version != engine.version:
            raise ValueError("wave graph version is stale")
        expected = {task.pair_id for task in wave.tasks}
        actual = {decision.pair_id for decision in generators}
        if actual != expected:
            raise ValueError("generator decisions do not exactly cover frozen wave")
        return engine.apply_wave(generators, reviewers)


# =============================================================================
# VNEXT_REFERENCE: exact graph deltas and pending-candidate pruning
# =============================================================================


@dataclass(frozen=True)
class InferredPairDelta:
    label_a_id: str
    label_b_id: str
    relation: Relation
    broader_direction: tuple[str, str] | None

    @property
    def pair(self) -> tuple[str, str]:
        return canonical_pair(self.label_a_id, self.label_b_id)


@dataclass(frozen=True)
class GraphDelta:
    """Exact reference delta between two graph states.

    ``newly_inferred_pairs`` is computed over all original Labels and is O(N^2).
    It is a correctness oracle, not the million-Label production algorithm.
    """

    graph_version_before: int
    graph_version_after: int
    merged_components: tuple[tuple[str, ...], ...]
    added_broader_assertions: tuple[tuple[str, str], ...]
    added_none_assertions: tuple[tuple[str, str], ...]
    added_disjoint_assertions: tuple[tuple[str, str], ...]
    affected_labels: frozenset[str]
    newly_inferred_pairs: tuple[InferredPairDelta, ...]

    def inferred_by_pair(self) -> dict[tuple[str, str], InferredPairDelta]:
        return {item.pair: item for item in self.newly_inferred_pairs}


@dataclass(frozen=True)
class WaveCommitWithDelta:
    report: WaveCommitReport
    delta: GraphDelta


def _known_original_pairs(
    engine: SafeRelationEngine,
) -> dict[tuple[str, str], tuple[Relation, tuple[str, str] | None]]:
    values = {}
    for left, right in itertools.combinations(engine.labels, 2):
        relation = engine.infer(left, right)
        if relation is None:
            continue
        direction = (
            engine.broader_direction(left, right)
            if relation == Relation.BROADER_THAN
            else None
        )
        values[canonical_pair(left, right)] = (relation, direction)
    return values


def _merged_components(
    before_root: Mapping[str, str], after: SafeRelationEngine
) -> tuple[tuple[str, ...], ...]:
    old_roots_by_new: dict[str, set[str]] = defaultdict(set)
    for label, old_root in before_root.items():
        old_roots_by_new[after.find(label)].add(old_root)
    return tuple(
        sorted(tuple(sorted(old_roots)) for old_roots in old_roots_by_new.values() if len(old_roots) > 1)
    )


def apply_wave_with_exact_delta(
    engine: SafeRelationEngine,
    generators: Sequence[PairDecision],
    reviewers: Mapping[str, PairDecision] | None = None,
) -> WaveCommitWithDelta:
    """Apply a wave and return an exact O(N^2) reference GraphDelta."""
    before_version = engine.version
    before_known = _known_original_pairs(engine)
    before_root = {label: engine.find(label) for label in engine.labels}
    before_broader = set(engine.broader_assertions)
    before_none = set(engine.none_assertions)
    before_disjoint = set(engine.disjoint_assertions)

    report = engine.apply_wave(generators, reviewers)
    after_known = _known_original_pairs(engine)

    newly_inferred = []
    affected_labels: set[str] = set()
    for pair, (relation, direction) in sorted(after_known.items()):
        if pair in before_known:
            continue
        affected_labels.update(pair)
        newly_inferred.append(
            InferredPairDelta(pair[0], pair[1], relation, direction)
        )

    delta = GraphDelta(
        graph_version_before=before_version,
        graph_version_after=engine.version,
        merged_components=_merged_components(before_root, engine),
        added_broader_assertions=tuple(
            sorted(engine.broader_assertions - before_broader)
        ),
        added_none_assertions=tuple(sorted(engine.none_assertions - before_none)),
        added_disjoint_assertions=tuple(
            sorted(engine.disjoint_assertions - before_disjoint)
        ),
        affected_labels=frozenset(affected_labels),
        newly_inferred_pairs=tuple(newly_inferred),
    )
    return WaveCommitWithDelta(report, delta)


class PendingCandidateIndex:
    """Reference reverse index for pending candidates.

    The stable original-label pair index makes exact GraphDelta intersection
    deterministic. ``by_label`` is also exposed for a future component-delta
    implementation that intentionally over-fetches and then calls engine.infer.
    """

    def __init__(self, tasks: Iterable[CandidateTask] = ()) -> None:
        self.tasks: dict[str, CandidateTask] = {}
        self.by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.by_label: dict[str, set[str]] = defaultdict(set)
        for task in tasks:
            self.add(task)

    def __len__(self) -> int:
        return len(self.tasks)

    def add(self, task: CandidateTask) -> None:
        task.validate()
        previous = self.tasks.get(task.pair_id)
        if previous is not None and previous != task:
            raise ValueError(f"conflicting candidate task: {task.pair_id}")
        if previous is not None:
            return
        self.tasks[task.pair_id] = task
        self.by_pair[canonical_pair(task.label_a_id, task.label_b_id)].add(
            task.pair_id
        )
        self.by_label[task.label_a_id].add(task.pair_id)
        self.by_label[task.label_b_id].add(task.pair_id)

    def remove(self, pair_id: str) -> CandidateTask | None:
        task = self.tasks.pop(pair_id, None)
        if task is None:
            return None
        key = canonical_pair(task.label_a_id, task.label_b_id)
        self.by_pair[key].discard(pair_id)
        if not self.by_pair[key]:
            del self.by_pair[key]
        for label in (task.label_a_id, task.label_b_id):
            self.by_label[label].discard(pair_id)
            if not self.by_label[label]:
                del self.by_label[label]
        return task

    def remove_many(self, pair_ids: Iterable[str]) -> None:
        for pair_id in list(pair_ids):
            self.remove(pair_id)

    def candidate_ids_for_delta(self, delta: GraphDelta) -> set[str]:
        result = set()
        for item in delta.newly_inferred_pairs:
            result.update(self.by_pair.get(item.pair, ()))
        return result

    def overfetch_by_affected_labels(self, affected_labels: Iterable[str]) -> set[str]:
        result = set()
        for label in affected_labels:
            result.update(self.by_label.get(label, ()))
        return result


@dataclass(frozen=True)
class PruneResult:
    proofs: Mapping[str, InferenceProof]
    expected_saved_model_cost: float

    @property
    def cancelled_count(self) -> int:
        return len(self.proofs)


def prune_newly_inferred_candidates(
    engine: SafeRelationEngine,
    delta: GraphDelta,
    pending_index: PendingCandidateIndex,
    *,
    exclude_pair_ids: Iterable[str] = (),
    remove: bool = True,
) -> PruneResult:
    """Intersect exact closure delta with pending candidates and verify via infer."""
    excluded = set(exclude_pair_ids)
    proofs: dict[str, InferenceProof] = {}
    saved_cost = 0.0
    for pair_id in sorted(pending_index.candidate_ids_for_delta(delta)):
        if pair_id in excluded:
            continue
        task = pending_index.tasks[pair_id]
        proof = engine.inference_proof(
            pair_id, task.label_a_id, task.label_b_id
        )
        if proof is None:
            # Indexes may over-fetch in production; engine.infer is authoritative.
            continue
        proofs[pair_id] = proof
        saved_cost += task.probabilities.expected_model_cost()
    if remove:
        pending_index.remove_many(proofs)
    return PruneResult(proofs, saved_cost)


def decision_for_outcome(task: CandidateTask, outcome: Outcome, model: str) -> PairDecision:
    if outcome == Outcome.A_BROADER_THAN_B:
        relation = Relation.BROADER_THAN
        first, second = task.label_a_id, task.label_b_id
    elif outcome == Outcome.B_BROADER_THAN_A:
        relation = Relation.BROADER_THAN
        first, second = task.label_b_id, task.label_a_id
    elif outcome == Outcome.EQUIVALENT_TO:
        relation = Relation.EQUIVALENT_TO
        first, second = task.label_a_id, task.label_b_id
    elif outcome == Outcome.NONE_PAIR_ONLY:
        relation = Relation.NONE
        first, second = task.label_a_id, task.label_b_id
    elif outcome == Outcome.NONE_DISJOINT_SUBTREES:
        relation = Relation.NONE_DISJOINT_SUBTREES
        first, second = task.label_a_id, task.label_b_id
    elif outcome == Outcome.UNCERTAIN:
        relation = Relation.UNCERTAIN
        first, second = task.label_a_id, task.label_b_id
    else:
        raise AssertionError(outcome)
    return PairDecision(
        task.pair_id,
        task.label_a_id,
        task.label_b_id,
        first,
        relation,
        second,
        model,
    )


@dataclass(frozen=True)
class PreviewGain:
    closure_gain: int
    queue_prune_gain: int
    saved_future_model_cost: float
    accepted: bool


def preview_outcome_gain(
    engine: SafeRelationEngine,
    task: CandidateTask,
    outcome: Outcome,
    pending_index: PendingCandidateIndex,
) -> PreviewGain:
    """Exact reference preview by cloning the graph and applying one safe outcome."""
    if outcome == Outcome.UNCERTAIN:
        return PreviewGain(0, 0, 0.0, True)
    clone = SafeRelationEngine.from_snapshot(engine.snapshot())
    before = clone.known_pair_counts()["TOTAL"]
    generator = decision_for_outcome(task, outcome, "preview-generator")
    reviewers = None
    if generator.relation in HIGH_RISK_RELATIONS:
        reviewers = {
            task.pair_id: decision_for_outcome(
                task, outcome, "preview-reviewer"
            )
        }
    committed = apply_wave_with_exact_delta(clone, [generator], reviewers)
    accepted = task.pair_id in (
        set(committed.report.accepted) | set(committed.report.inferred)
    )
    if not accepted:
        return PreviewGain(0, 0, 0.0, False)
    after = clone.known_pair_counts()["TOTAL"]
    pruned = prune_newly_inferred_candidates(
        clone,
        committed.delta,
        pending_index,
        exclude_pair_ids={task.pair_id},
        remove=False,
    )
    return PreviewGain(
        closure_gain=after - before,
        queue_prune_gain=pruned.cancelled_count,
        saved_future_model_cost=pruned.expected_saved_model_cost,
        accepted=True,
    )


@dataclass(frozen=True)
class HybridScoreBreakdown:
    score: float
    expected_utility: float
    expected_cost: float
    expected_closure_gain: float
    expected_queue_prune_gain: float
    expected_saved_future_model_cost: float
    previews: Mapping[str, PreviewGain]


class HybridGainScorer:
    """VNext reference score combining closure and pending-queue savings.

    Choose either ``queue_prune_weight`` or ``saved_cost_weight`` unless the
    two terms have been intentionally normalized; otherwise they double-count
    the same cancelled candidates.
    """

    def __init__(
        self,
        *,
        closure_weight: float = 1.0,
        queue_prune_weight: float = 0.0,
        saved_cost_weight: float = 1.0,
    ) -> None:
        if min(closure_weight, queue_prune_weight, saved_cost_weight) < 0:
            raise ValueError("gain weights must be non-negative")
        if queue_prune_weight and saved_cost_weight:
            raise ValueError(
                "queue_prune_weight and saved_cost_weight double-count cancellation"
            )
        self.closure_weight = closure_weight
        self.queue_prune_weight = queue_prune_weight
        self.saved_cost_weight = saved_cost_weight

    def score(
        self,
        engine: SafeRelationEngine,
        task: CandidateTask,
        candidate_index: PendingCandidateIndex | None,
    ) -> HybridScoreBreakdown:
        if candidate_index is None:
            raise ValueError("HybridGainScorer requires PendingCandidateIndex")
        task.validate()
        probability_by_outcome = dict(zip(PREDICTED_OUTCOMES, task.probabilities.values()))
        previews = {
            outcome.value: preview_outcome_gain(
                engine, task, outcome, candidate_index
            )
            for outcome in PREDICTED_OUTCOMES
        }
        expected_closure = sum(
            probability_by_outcome[outcome]
            * previews[outcome.value].closure_gain
            for outcome in PREDICTED_OUTCOMES
        )
        expected_prune = sum(
            probability_by_outcome[outcome]
            * previews[outcome.value].queue_prune_gain
            for outcome in PREDICTED_OUTCOMES
        )
        expected_saved_cost = sum(
            probability_by_outcome[outcome]
            * previews[outcome.value].saved_future_model_cost
            for outcome in PREDICTED_OUTCOMES
        )
        expected_utility = (
            self.closure_weight * expected_closure
            + self.queue_prune_weight * expected_prune
            + self.saved_cost_weight * expected_saved_cost
        )
        expected_cost = task.probabilities.expected_model_cost()
        return HybridScoreBreakdown(
            score=expected_utility / expected_cost,
            expected_utility=expected_utility,
            expected_cost=expected_cost,
            expected_closure_gain=expected_closure,
            expected_queue_prune_gain=expected_prune,
            expected_saved_future_model_cost=expected_saved_cost,
            previews=previews,
        )


# =============================================================================
# PROJECT_ADAPTERS: deployable features and probability providers
# =============================================================================


FEATURE_NAMES = (
    "channel_search_form",
    "channel_name_embedding",
    "channel_dense_local_block",
    "channel_curated_structure",
    "name_similarity",
    "name_similarity_missing",
    "name_rank",
    "name_rank_missing",
    "same_language",
    "left_char_length",
    "right_char_length",
    "char_length_difference",
    "left_word_count",
    "right_word_count",
    "surface_substring",
    "surface_sequence_ratio",
)


def feature_vector(
    evidence: Mapping[str, object], left_name: str, right_name: str
) -> tuple[float, ...]:
    """Current 16 runtime-reproducible probability-model features."""
    channels = {str(value) for value in evidence.get("channels", [])}
    similarity_value = evidence.get("name_similarity")
    similarity = None if similarity_value is None else float(similarity_value)
    rank_value = evidence.get("name_rank")
    rank = None if rank_value is None else int(rank_value)
    left_language, left_text = left_name.split(":", 1)
    right_language, right_text = right_name.split(":", 1)
    left_folded = left_text.casefold()
    right_folded = right_text.casefold()
    values = (
        float("SEARCH_FORM" in channels),
        float("NAME_EMBEDDING" in channels),
        float("DENSE_LOCAL_BLOCK" in channels),
        float("CURATED_STRUCTURE" in channels),
        float(similarity or 0.0),
        float(similarity is None),
        float(rank if rank is not None else 99),
        float(rank is None),
        float(left_language == right_language),
        float(len(left_text)),
        float(len(right_text)),
        float(len(left_text) - len(right_text)),
        float(len(left_text.split())),
        float(len(right_text.split())),
        float(left_folded in right_folded or right_folded in left_folded),
        float(SequenceMatcher(None, left_folded, right_folded).ratio()),
    )
    if len(values) != len(FEATURE_NAMES) or not all(map(math.isfinite, values)):
        raise ValueError("invalid probability feature vector")
    return values


def normalized_probabilities(values: Sequence[float]) -> CandidateProbabilities:
    if len(values) != len(PREDICTED_OUTCOMES):
        raise ValueError("probability outcome count changed")
    normalized = [max(float(value), 1e-6) for value in values]
    total = sum(normalized)
    normalized = [value / total for value in normalized]
    result = CandidateProbabilities(*normalized)
    result.validate()
    return result


class ProbabilityProvider(Protocol):
    version: str

    def predict(
        self,
        evidence: Mapping[str, object],
        left_name: str,
        right_name: str,
    ) -> CandidateProbabilities: ...


class EvidenceOnlyProvider:
    version = "evidence-only-v1"

    def predict(
        self,
        evidence: Mapping[str, object],
        left_name: str,
        right_name: str,
    ) -> CandidateProbabilities:
        del left_name, right_name
        channels = {str(value) for value in evidence.get("channels", [])}
        similarity_value = evidence.get("name_similarity")
        similarity = None if similarity_value is None else float(similarity_value)
        if "SEARCH_FORM" in channels:
            values = (0.75, 0.03, 0.03, 0.14, 0.01, 0.04)
        elif similarity is not None and similarity >= 0.95:
            values = (0.50, 0.14, 0.14, 0.12, 0.02, 0.08)
        elif similarity is not None and similarity >= 0.90:
            values = (0.25, 0.20, 0.20, 0.24, 0.04, 0.07)
        elif similarity is not None:
            values = (0.12, 0.20, 0.20, 0.35, 0.05, 0.08)
        elif "DENSE_LOCAL_BLOCK" in channels:
            values = (0.10, 0.20, 0.20, 0.38, 0.05, 0.07)
        else:
            values = (0.10, 0.20, 0.20, 0.35, 0.07, 0.08)
        return normalized_probabilities(values)


class ReleasedSklearnProvider:
    """Optional sklearn/joblib provider compatible with aepgs-prob-v0001."""

    def __init__(
        self,
        model_path: Path,
        *,
        expected_version: str | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        if expected_sha256 and sha256_file(model_path) != expected_sha256:
            raise ValueError("probability model hash mismatch")
        try:
            import joblib  # type: ignore
        except ImportError as exc:
            raise RuntimeError("joblib is required to load sklearn models") from exc
        bundle = joblib.load(model_path)
        if not isinstance(bundle, Mapping):
            raise ValueError("probability artifact must be a mapping")
        if tuple(bundle.get("outcomes", ())) != OUTCOME_VALUES:
            raise ValueError("probability outcome schema changed")
        if tuple(bundle.get("feature_names", ())) != FEATURE_NAMES:
            raise ValueError("probability feature schema changed")
        self.version = str(bundle["version"])
        if expected_version and self.version != expected_version:
            raise ValueError("probability model version mismatch")
        self.model = bundle["model"]

    def predict(
        self,
        evidence: Mapping[str, object],
        left_name: str,
        right_name: str,
    ) -> CandidateProbabilities:
        features = [feature_vector(evidence, left_name, right_name)]
        raw = self.model.predict_proba(features)[0]
        values = [0.0] * len(PREDICTED_OUTCOMES)
        for offset, class_id in enumerate(self.model.classes_):
            values[int(class_id)] = float(raw[offset])
        return normalized_probabilities(values)


def outcome_from_relation(
    relation: str, label_a_id: str, first_label_id: str
) -> Outcome:
    if relation == Relation.EQUIVALENT_TO.value:
        return Outcome.EQUIVALENT_TO
    if relation == Relation.BROADER_THAN.value:
        return (
            Outcome.A_BROADER_THAN_B
            if first_label_id == label_a_id
            else Outcome.B_BROADER_THAN_A
        )
    if relation == Relation.NONE.value:
        return Outcome.NONE_PAIR_ONLY
    if relation == Relation.NONE_DISJOINT_SUBTREES.value:
        return Outcome.NONE_DISJOINT_SUBTREES
    if relation == Relation.UNCERTAIN.value:
        return Outcome.UNCERTAIN
    raise ValueError(f"unsupported relation outcome: {relation}")


@dataclass(frozen=True)
class TrainingExample:
    pair_id: str
    label_a_id: str
    label_b_id: str
    label_a_name: str
    label_b_name: str
    evidence: Mapping[str, object]
    outcome: Outcome
    source: str

    def features(self) -> tuple[float, ...]:
        return feature_vector(self.evidence, self.label_a_name, self.label_b_name)


@dataclass(frozen=True)
class ProbabilityMetrics:
    rows: int
    log_loss: float | None
    brier: float | None
    ece: float | None
    top1_accuracy: float | None
    mean_confidence: float | None


def probability_metrics(
    targets: Sequence[int], probabilities: Sequence[Sequence[float]], bins: int = 10
) -> ProbabilityMetrics:
    if not targets:
        return ProbabilityMetrics(0, None, None, None, None, None)
    if len(targets) != len(probabilities):
        raise ValueError("target/probability row mismatch")
    normalized_rows = []
    for row in probabilities:
        values = [max(float(value), 1e-12) for value in row]
        total = sum(values)
        normalized_rows.append([value / total for value in values])
    losses = []
    brier_values = []
    confidences = []
    correct = []
    for target, row in zip(targets, normalized_rows):
        losses.append(-math.log(row[target]))
        brier_values.append(
            sum((value - float(offset == target)) ** 2 for offset, value in enumerate(row))
        )
        prediction = max(range(len(row)), key=row.__getitem__)
        confidences.append(row[prediction])
        correct.append(prediction == target)
    ece = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        selected = [
            offset
            for offset, confidence in enumerate(confidences)
            if lower <= confidence < upper or (index == bins - 1 and confidence == 1.0)
        ]
        if not selected:
            continue
        accuracy = sum(correct[offset] for offset in selected) / len(selected)
        confidence = sum(confidences[offset] for offset in selected) / len(selected)
        ece += len(selected) / len(targets) * abs(accuracy - confidence)
    return ProbabilityMetrics(
        rows=len(targets),
        log_loss=sum(losses) / len(losses),
        brier=sum(brier_values) / len(brier_values),
        ece=ece,
        top1_accuracy=sum(correct) / len(correct),
        mean_confidence=sum(confidences) / len(confidences),
    )


def _component_groups(examples: Sequence[TrainingExample]) -> list[int]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        root = value
        while parent[root] != root:
            root = parent[root]
        while parent[value] != value:
            previous = parent[value]
            parent[value] = root
            value = previous
        return root

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            keep, drop = sorted((a, b))
            parent[drop] = keep

    for example in examples:
        union(example.label_a_id, example.label_b_id)
    ids: dict[str, int] = {}
    result = []
    for example in examples:
        root = find(example.label_a_id)
        ids.setdefault(root, len(ids))
        result.append(ids[root])
    return result


def train_probability_model_reference(
    examples: Sequence[TrainingExample],
    output_dir: Path,
    *,
    version: str,
    random_state: int = 20260808,
) -> dict[str, Any]:
    """Optional reference trainer mirroring the current logistic release gate.

    Requires numpy, scikit-learn, and joblib. This function is deliberately not
    imported by core execution paths.
    """
    if len(examples) < 1_000:
        return {"status": "SKIPPED_TOO_FEW_ROWS", "rows": len(examples)}
    by_pair: dict[str, TrainingExample] = {}
    for example in examples:
        previous = by_pair.get(example.pair_id)
        if previous is not None and previous != example:
            raise ValueError(f"conflicting training label: {example.pair_id}")
        by_pair[example.pair_id] = example
    rows = [by_pair[key] for key in sorted(by_pair)]
    class_counts = Counter(row.outcome.value for row in rows)
    if any(class_counts.get(value, 0) < 20 for value in OUTCOME_VALUES):
        return {
            "status": "REJECTED_CLASS_COVERAGE",
            "rows": len(rows),
            "class_counts": dict(class_counts),
        }
    try:
        import joblib  # type: ignore
        import numpy as np  # type: ignore
        from sklearn.base import clone  # type: ignore
        from sklearn.linear_model import LogisticRegression  # type: ignore
        from sklearn.model_selection import GroupShuffleSplit  # type: ignore
        from sklearn.pipeline import make_pipeline  # type: ignore
        from sklearn.preprocessing import StandardScaler  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "numpy, scikit-learn, and joblib are required for training"
        ) from exc

    features = np.asarray([row.features() for row in rows], dtype=float)
    targets = np.asarray([PREDICTED_OUTCOMES.index(row.outcome) for row in rows], dtype=int)
    groups = np.asarray(_component_groups(rows), dtype=int)
    candidates = {
        "logistic_c0.1": make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=3000, C=0.1)
        ),
        "logistic_c1": make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=3000, C=1.0)
        ),
        "logistic_c10": make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=3000, C=10.0)
        ),
        "logistic_balanced": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=3000, C=1.0, class_weight="balanced"),
        ),
    }
    splitter = GroupShuffleSplit(
        n_splits=5, test_size=0.25, random_state=random_state
    )
    metrics_by_model: dict[str, list[ProbabilityMetrics]] = defaultdict(list)
    for train, test in splitter.split(features, targets, groups):
        if len(set(targets[train])) != len(PREDICTED_OUTCOMES):
            continue
        for name, model in candidates.items():
            fitted = clone(model).fit(features[train], targets[train])
            raw = fitted.predict_proba(features[test])
            expanded = []
            for probability_row in raw:
                values = [1e-8] * len(PREDICTED_OUTCOMES)
                for offset, class_id in enumerate(fitted.classes_):
                    values[int(class_id)] = float(probability_row[offset])
                total = sum(values)
                expanded.append([value / total for value in values])
            metrics_by_model[name].append(
                probability_metrics(targets[test].tolist(), expanded)
            )
    if not metrics_by_model or any(len(values) != 5 for values in metrics_by_model.values()):
        return {"status": "REJECTED_INVALID_GROUP_SPLITS", "rows": len(rows)}

    def mean_metric(values: Sequence[ProbabilityMetrics], name: str) -> float:
        available = [float(getattr(value, name)) for value in values if getattr(value, name) is not None]
        return sum(available) / len(available)

    summary = {
        name: {
            metric: mean_metric(values, metric)
            for metric in ("log_loss", "brier", "ece")
        }
        for name, values in metrics_by_model.items()
    }
    best_name = min(summary, key=lambda name: summary[name]["log_loss"])
    final_model = candidates[best_name].fit(features, targets)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.joblib"
    joblib.dump(
        {
            "version": version,
            "model": final_model,
            "outcomes": list(OUTCOME_VALUES),
            "feature_names": list(FEATURE_NAMES),
            "training_rows": len(rows),
        },
        model_path,
    )
    report = {
        "status": "TRAINED_REFERENCE_NOT_AUTO_RELEASED",
        "version": version,
        "rows": len(rows),
        "class_counts": dict(sorted(class_counts.items())),
        "candidate_metrics": summary,
        "best_model": best_name,
        "model_sha256": sha256_file(model_path),
    }
    atomic_write_json(output_dir / "report.json", report)
    return report


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


# =============================================================================
# PROJECT_ADAPTERS: SQLite task state and abstract model execution
# =============================================================================


LEDGER_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates(
    pair_id TEXT PRIMARY KEY,
    label_a_id TEXT NOT NULL,
    label_b_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    probabilities_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    graph_version INTEGER,
    priority REAL,
    wave_id INTEGER,
    selection_order INTEGER,
    generator_json TEXT,
    reviewer_json TEXT,
    gate_result TEXT,
    commit_status TEXT,
    inference_json TEXT,
    error TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS waves(
    wave_id INTEGER PRIMARY KEY,
    graph_version INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL,
    selected_count INTEGER NOT NULL,
    report_json TEXT,
    created_at REAL NOT NULL,
    committed_at REAL
);
CREATE TABLE IF NOT EXISTS snapshots(
    graph_version INTEGER PRIMARY KEY,
    wave_id INTEGER,
    snapshot_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS responses(
    response_id TEXT PRIMARY KEY,
    wave_id INTEGER,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    raw_output TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_pending
    ON candidates(status, wave_id);
CREATE INDEX IF NOT EXISTS idx_candidates_wave
    ON candidates(wave_id, selection_order);
"""


class PortableLedger:
    """Minimal single-file SQLite ledger for transfer and reference tests."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(LEDGER_SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PortableLedger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterable[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else str(row[0])

    def initialize(
        self,
        tasks: Sequence[CandidateTask],
        engine: SafeRelationEngine,
        model_version: str,
    ) -> None:
        existing = self.get_meta("schema_version")
        if existing is not None:
            if existing != PORTABLE_SCHEMA_VERSION:
                raise ValueError("portable ledger schema changed")
            if self.get_meta("probability_model_version") != model_version:
                raise ValueError("existing task has a frozen probability model")
            return
        now = time.time()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES ('schema_version',?)",
                (PORTABLE_SCHEMA_VERSION,),
            )
            connection.execute(
                "INSERT INTO meta(key,value) VALUES ('probability_model_version',?)",
                (model_version,),
            )
            for task in tasks:
                task.validate()
                connection.execute(
                    "INSERT INTO candidates(pair_id,label_a_id,label_b_id,evidence_json,"
                    "probabilities_json,model_version,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        task.pair_id,
                        task.label_a_id,
                        task.label_b_id,
                        json.dumps(dict(task.evidence), sort_keys=True),
                        json.dumps(asdict(task.probabilities), sort_keys=True),
                        model_version,
                        now,
                    ),
                )
            self._store_snapshot(connection, engine.snapshot(), None, now)

    @staticmethod
    def _store_snapshot(
        connection: sqlite3.Connection,
        snapshot: Mapping[str, Any],
        wave_id: int | None,
        now: float,
    ) -> None:
        payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        connection.execute(
            "INSERT INTO snapshots(graph_version,wave_id,snapshot_json,sha256,created_at) "
            "VALUES (?,?,?,?,?)",
            (int(snapshot["graph_version"]), wave_id, payload, digest, now),
        )

    def load_engine(self) -> SafeRelationEngine:
        row = self.connection.execute(
            "SELECT snapshot_json,sha256 FROM snapshots "
            "ORDER BY graph_version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("ledger has no graph snapshot")
        payload = str(row["snapshot_json"])
        if hashlib.sha256(payload.encode()).hexdigest() != row["sha256"]:
            raise RuntimeError("snapshot hash mismatch")
        return SafeRelationEngine.from_snapshot(json.loads(payload))

    @staticmethod
    def _row_to_task(row: Mapping[str, Any]) -> CandidateTask:
        return CandidateTask(
            pair_id=str(row["pair_id"]),
            label_a_id=str(row["label_a_id"]),
            label_b_id=str(row["label_b_id"]),
            probabilities=CandidateProbabilities(
                **json.loads(str(row["probabilities_json"]))
            ),
            evidence=json.loads(str(row["evidence_json"])),
        )

    def pending_tasks(self) -> list[CandidateTask]:
        rows = self.connection.execute(
            "SELECT * FROM candidates WHERE status='PENDING' AND wave_id IS NULL "
            "ORDER BY pair_id"
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        return dict(
            self.connection.execute(
                "SELECT status,count(*) FROM candidates GROUP BY status ORDER BY status"
            ).fetchall()
        )

    def mark_inferred_before_model(
        self, proofs: Mapping[str, InferenceProof]
    ) -> int:
        now = time.time()
        updated = 0
        with self.transaction() as connection:
            for pair_id, proof in sorted(proofs.items()):
                cursor = connection.execute(
                    "UPDATE candidates SET status='INFERRED',gate_result=?,"
                    "commit_status='INFERRED_BEFORE_MODEL',inference_json=?,updated_at=? "
                    "WHERE pair_id=? AND status='PENDING' AND wave_id IS NULL",
                    (
                        proof.relation.value,
                        json.dumps(proof.as_json(), sort_keys=True),
                        now,
                        pair_id,
                    ),
                )
                updated += cursor.rowcount
        return updated

    def update_priorities(
        self, values: Mapping[str, float], graph_version: int
    ) -> None:
        now = time.time()
        with self.transaction() as connection:
            for pair_id, priority in values.items():
                connection.execute(
                    "UPDATE candidates SET priority=?,graph_version=?,updated_at=? "
                    "WHERE pair_id=? AND status='PENDING'",
                    (float(priority), graph_version, now, pair_id),
                )

    def freeze_wave(self, wave: FrozenWave, priorities: Mapping[str, float]) -> None:
        now = time.time()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO waves(wave_id,graph_version,model_version,status,"
                "selected_count,created_at) VALUES (?,?,?,?,?,?)",
                (
                    wave.wave_id,
                    wave.graph_version,
                    wave.model_version,
                    "FROZEN",
                    len(wave.tasks),
                    now,
                ),
            )
            for order, task in enumerate(wave.tasks):
                cursor = connection.execute(
                    "UPDATE candidates SET wave_id=?,selection_order=?,graph_version=?,"
                    "priority=?,updated_at=? WHERE pair_id=? AND status='PENDING' "
                    "AND wave_id IS NULL",
                    (
                        wave.wave_id,
                        order,
                        wave.graph_version,
                        float(priorities[task.pair_id]),
                        now,
                        task.pair_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"failed to freeze candidate {task.pair_id}")

    def set_running(self, pair_ids: Sequence[str], role: str) -> None:
        expected, target = (
            ("PENDING", "RUNNING_GENERATOR")
            if role == "GENERATOR"
            else ("GENERATED", "RUNNING_REVIEWER")
        )
        now = time.time()
        with self.transaction() as connection:
            for pair_id in pair_ids:
                cursor = connection.execute(
                    "UPDATE candidates SET status=?,updated_at=? "
                    "WHERE pair_id=? AND status=?",
                    (target, now, pair_id, expected),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"candidate is not claimable: {pair_id}")

    def recover_interrupted(self) -> dict[str, int]:
        now = time.time()
        with self.transaction() as connection:
            generator = connection.execute(
                "UPDATE candidates SET status='PENDING',error=NULL,updated_at=? "
                "WHERE status='RUNNING_GENERATOR'",
                (now,),
            ).rowcount
            reviewer = connection.execute(
                "UPDATE candidates SET status='GENERATED',error=NULL,updated_at=? "
                "WHERE status='RUNNING_REVIEWER'",
                (now,),
            ).rowcount
        return {
            "running_generator_to_pending": generator,
            "running_reviewer_to_generated": reviewer,
        }

    def record_response(
        self,
        *,
        response_id: str,
        wave_id: int,
        role: str,
        model: str,
        usage: Mapping[str, int],
        raw_output: str,
        decisions: Sequence[PairDecision],
    ) -> None:
        now = time.time()
        expected = "RUNNING_GENERATOR" if role == "GENERATOR" else "RUNNING_REVIEWER"
        target = "GENERATED" if role == "GENERATOR" else "READY"
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO responses(response_id,wave_id,role,model,usage_json,"
                "raw_output,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    response_id,
                    wave_id,
                    role,
                    model,
                    json.dumps(dict(usage), sort_keys=True),
                    raw_output,
                    now,
                ),
            )
            for decision in decisions:
                decision.validate()
                payload = json.dumps(
                    {
                        **asdict(decision),
                        "relation": decision.relation.value,
                    },
                    sort_keys=True,
                )
                next_status = target
                if role == "GENERATOR" and decision.relation not in HIGH_RISK_RELATIONS:
                    next_status = "READY"
                column = "generator_json" if role == "GENERATOR" else "reviewer_json"
                cursor = connection.execute(
                    f"UPDATE candidates SET status=?,{column}=?,updated_at=? "
                    "WHERE pair_id=? AND status=?",
                    (next_status, payload, now, decision.pair_id, expected),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"response task is not in expected state: {decision.pair_id}"
                    )

    def commit_wave(
        self,
        wave_id: int,
        report: WaveCommitReport,
        engine: SafeRelationEngine,
    ) -> None:
        if report.review_required:
            raise RuntimeError("wave still requires review")
        now = time.time()
        expected = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT pair_id FROM candidates WHERE wave_id=?", (wave_id,)
            )
        }
        terminal = (
            set(report.accepted)
            | set(report.inferred)
            | set(report.uncertain)
            | set(report.rejected)
        )
        if expected != terminal:
            raise RuntimeError("commit report does not cover frozen wave")
        with self.transaction() as connection:
            for pair_id in report.accepted:
                connection.execute(
                    "UPDATE candidates SET status='COMMITTED',gate_result=?,"
                    "commit_status='ACCEPTED',updated_at=? WHERE pair_id=?",
                    (report.gated_relations[pair_id], now, pair_id),
                )
            for pair_id in report.inferred:
                connection.execute(
                    "UPDATE candidates SET status='INFERRED',gate_result=?,"
                    "commit_status='REDUNDANT_AFTER_GATE',updated_at=? WHERE pair_id=?",
                    (report.gated_relations[pair_id], now, pair_id),
                )
            for pair_id in report.uncertain:
                connection.execute(
                    "UPDATE candidates SET status='UNCERTAIN',gate_result='UNCERTAIN',"
                    "commit_status='UNRESOLVED',updated_at=? WHERE pair_id=?",
                    (now, pair_id),
                )
            for pair_id, reason in report.rejected.items():
                connection.execute(
                    "UPDATE candidates SET status='REJECTED',gate_result=?,"
                    "commit_status='GATE_REJECTED',error=?,updated_at=? WHERE pair_id=?",
                    (report.gated_relations[pair_id], reason, now, pair_id),
                )
            self._store_snapshot(connection, engine.snapshot(), wave_id, now)
            connection.execute(
                "UPDATE waves SET status='COMMITTED',report_json=?,committed_at=? "
                "WHERE wave_id=?",
                (json.dumps(asdict(report), sort_keys=True), now, wave_id),
            )


class BatchRelationModelAdapter(Protocol):
    model_name: str

    async def classify(
        self, tasks: Sequence[CandidateTask]
    ) -> Sequence[PairDecision]: ...


async def execute_frozen_wave_reference(
    *,
    engine: SafeRelationEngine,
    wave: FrozenWave,
    generator: BatchRelationModelAdapter,
    reviewer: BatchRelationModelAdapter,
) -> WaveCommitWithDelta:
    """Generic model-adapter execution with strict Frozen Wave coverage."""
    if wave.graph_version != engine.version:
        raise ValueError("wave graph version is stale")
    generated = list(await generator.classify(wave.tasks))
    expected = {task.pair_id for task in wave.tasks}
    if {decision.pair_id for decision in generated} != expected:
        raise ValueError("generator did not exactly cover frozen wave")
    task_by_id = {task.pair_id: task for task in wave.tasks}
    high_risk_tasks = [
        task_by_id[decision.pair_id]
        for decision in generated
        if decision.relation in HIGH_RISK_RELATIONS
    ]
    reviewed = list(await reviewer.classify(high_risk_tasks)) if high_risk_tasks else []
    if {decision.pair_id for decision in reviewed} != {
        task.pair_id for task in high_risk_tasks
    }:
        raise ValueError("reviewer did not exactly cover high-risk tasks")
    return apply_wave_with_exact_delta(
        engine,
        generated,
        {decision.pair_id: decision for decision in reviewed},
    )


@dataclass(frozen=True)
class RuntimeUtility:
    direct_safe_relations: int
    new_known_unique_pairs: int
    extra_inferred_pairs: int
    relation_amplification: float | None
    generator_pairs: int
    inferred_before_model: int
    physical_candidate_pruning_rate: float


def runtime_utility(
    *,
    before: SafeRelationEngine,
    after: SafeRelationEngine,
    direct_safe_relations: int,
    generator_pairs: int,
    inferred_before_model: int,
) -> RuntimeUtility:
    new_known = (
        after.known_pair_counts()["TOTAL"]
        - before.known_pair_counts()["TOTAL"]
    )
    denominator = inferred_before_model + generator_pairs
    return RuntimeUtility(
        direct_safe_relations=direct_safe_relations,
        new_known_unique_pairs=new_known,
        extra_inferred_pairs=new_known - direct_safe_relations,
        relation_amplification=(
            new_known / direct_safe_relations if direct_safe_relations else None
        ),
        generator_pairs=generator_pairs,
        inferred_before_model=inferred_before_model,
        physical_candidate_pruning_rate=(
            inferred_before_model / denominator if denominator else 0.0
        ),
    )


# =============================================================================
# SELF_TESTS and demo CLI
# =============================================================================


def _assert_equal(actual: Any, expected: Any, message: str = "") -> None:
    if actual != expected:
        raise AssertionError(f"{message}\nactual={actual!r}\nexpected={expected!r}")


def _assert_close(actual: float, expected: float, tolerance: float = 1e-9) -> None:
    if not math.isclose(actual, expected, abs_tol=tolerance, rel_tol=tolerance):
        raise AssertionError(f"actual={actual!r}, expected={expected!r}")


def _probabilities(**changes: float) -> CandidateProbabilities:
    values = {
        "equivalent": 0.10,
        "a_broader_b": 0.25,
        "b_broader_a": 0.15,
        "none": 0.35,
        "disjoint": 0.05,
        "uncertain": 0.10,
    }
    values.update(changes)
    result = CandidateProbabilities(**values)
    result.validate()
    return result


def _certain(outcome: Outcome) -> CandidateProbabilities:
    values = [0.0] * len(PREDICTED_OUTCOMES)
    values[PREDICTED_OUTCOMES.index(outcome)] = 1.0
    return CandidateProbabilities(*values)


def _task(pair_id: str, left: str, right: str, outcome: Outcome | None = None) -> CandidateTask:
    return CandidateTask(
        pair_id,
        left,
        right,
        _certain(outcome) if outcome else _probabilities(),
        {"channels": ["NAME_EMBEDDING"], "name_similarity": 0.9, "name_rank": 1},
    )


def _decision(
    pair_id: str,
    left: str,
    right: str,
    relation: Relation,
    *,
    first: str | None = None,
    model: str = "test-generator",
) -> PairDecision:
    if relation == Relation.BROADER_THAN:
        first_value = first or left
        second_value = right if first_value == left else left
    else:
        first_value, second_value = left, right
    return PairDecision(
        pair_id,
        left,
        right,
        first_value,
        relation,
        second_value,
        model,
    )


def test_transitive_reduction_and_reachability() -> None:
    engine = SafeRelationEngine(["A", "B", "C"])
    engine.apply_wave(
        [
            _decision("AB", "A", "B", Relation.BROADER_THAN),
            _decision("BC", "B", "C", Relation.BROADER_THAN),
            _decision("AC", "A", "C", Relation.BROADER_THAN),
        ]
    )
    _assert_equal(engine.reduced_edges, {("A", "B"), ("B", "C")})
    _assert_equal(engine.broader_direction("A", "C"), ("A", "C"))
    _assert_equal(engine.known_pair_counts()["BROADER_THAN"], 3)


def test_equivalent_component_expansion() -> None:
    engine = SafeRelationEngine(["A1", "A2", "B1", "B2", "X"])
    engine.apply_wave(
        [_decision("A", "A1", "A2", Relation.EQUIVALENT_TO)],
        {"A": _decision("A", "A1", "A2", Relation.EQUIVALENT_TO, model="reviewer")},
    )
    engine.apply_wave(
        [_decision("B", "B1", "B2", Relation.EQUIVALENT_TO)],
        {"B": _decision("B", "B1", "B2", Relation.EQUIVALENT_TO, model="reviewer")},
    )
    before = engine.known_pair_counts()["TOTAL"]
    engine.apply_wave(
        [_decision("AB", "A1", "B1", Relation.EQUIVALENT_TO)],
        {"AB": _decision("AB", "A1", "B1", Relation.EQUIVALENT_TO, model="reviewer")},
    )
    after = engine.known_pair_counts()["TOTAL"]
    _assert_equal(after - before, 4, "2x2 component merge must add four pairs")
    _assert_equal(engine.find("A2"), engine.find("B2"))


def test_disjoint_descendant_propagation() -> None:
    engine = SafeRelationEngine(["A", "A1", "B", "B1"])
    engine.apply_wave(
        [
            _decision("AA1", "A", "A1", Relation.BROADER_THAN),
            _decision("BB1", "B", "B1", Relation.BROADER_THAN),
        ]
    )
    engine.apply_wave(
        [_decision("D", "A", "B", Relation.NONE_DISJOINT_SUBTREES)],
        {"D": _decision("D", "A", "B", Relation.NONE_DISJOINT_SUBTREES, model="reviewer")},
    )
    for left in ("A", "A1"):
        for right in ("B", "B1"):
            _assert_equal(engine.infer(left, right), Relation.NONE)
    _assert_equal(engine.known_pair_counts()["NONE"], 4)


def test_exact_delta_cancels_pending_candidate() -> None:
    engine = SafeRelationEngine(["A", "B", "C"])
    engine.apply_wave([_decision("AB", "A", "B", Relation.BROADER_THAN)])
    pending = PendingCandidateIndex(
        [_task("AC", "A", "C"), _task("BA", "B", "A")]
    )
    committed = apply_wave_with_exact_delta(
        engine,
        [_decision("BC", "B", "C", Relation.BROADER_THAN)],
    )
    result = prune_newly_inferred_candidates(
        engine, committed.delta, pending, remove=True
    )
    _assert_equal(set(result.proofs), {"AC"})
    _assert_equal(result.proofs["AC"].relation, Relation.BROADER_THAN)
    _assert_equal(result.proofs["AC"].broader_direction, ("A", "C"))
    _assert_equal(set(pending.tasks), {"BA"})


def test_hybrid_gain_counts_future_call_savings() -> None:
    engine = SafeRelationEngine(["A", "B", "C"])
    engine.apply_wave([_decision("BC", "B", "C", Relation.BROADER_THAN)])
    primary = _task("AB", "A", "B", Outcome.A_BROADER_THAN_B)
    pending = PendingCandidateIndex(
        [primary, _task("AC", "A", "C", Outcome.NONE_PAIR_ONLY)]
    )
    current = SingleGainScorer().score(engine, primary)
    hybrid = HybridGainScorer(
        closure_weight=1.0,
        queue_prune_weight=0.0,
        saved_cost_weight=1.0,
    ).score(engine, primary, pending)
    _assert_equal(hybrid.previews[Outcome.A_BROADER_THAN_B.value].queue_prune_gain, 1)
    if hybrid.score <= current.score:
        raise AssertionError("hybrid score must include saved future AC call")


def test_versioned_queue_and_stale_wave() -> None:
    engine = SafeRelationEngine(["A", "B", "C"])
    scorer = SingleGainScorer()
    queue = VersionedPriorityQueue()
    queue.upsert(_task("AB", "A", "B"), engine, scorer, "m1")
    queue.upsert(_task("AC", "A", "C"), engine, scorer, "m1")
    planner = FrozenWavePlanner()
    wave = planner.plan(queue, engine, scorer, "m1", 1)
    _assert_equal(len(wave.tasks), 1)
    engine.apply_wave([_decision("OTHER", "B", "C", Relation.BROADER_THAN)])
    try:
        FrozenWavePlanner.commit(engine, wave, [])
    except ValueError as exc:
        if "stale" not in str(exc):
            raise
    else:
        raise AssertionError("stale Frozen Wave commit must fail")


def test_snapshot_roundtrip() -> None:
    engine = SafeRelationEngine(["A", "A2", "B", "C", "D"])
    engine.apply_wave(
        [_decision("EQ", "A", "A2", Relation.EQUIVALENT_TO)],
        {"EQ": _decision("EQ", "A", "A2", Relation.EQUIVALENT_TO, model="reviewer")},
    )
    engine.apply_wave(
        [
            _decision("AB", "A", "B", Relation.BROADER_THAN),
            _decision("BC", "B", "C", Relation.BROADER_THAN),
            _decision("CD", "C", "D", Relation.NONE),
        ]
    )
    restarted = SafeRelationEngine.from_snapshot(engine.snapshot())
    _assert_equal(restarted.snapshot(), engine.snapshot())
    _assert_equal(restarted.reduced_edges, engine.reduced_edges)
    _assert_equal(restarted.descendants, engine.descendants)


def test_probability_features_and_metrics() -> None:
    evidence = {
        "channels": ["NAME_EMBEDDING"],
        "name_similarity": 0.91,
        "name_rank": 7,
    }
    first = feature_vector(evidence, "en:cat", "en:animal")
    second = feature_vector(evidence, "en:cat", "en:animal")
    _assert_equal(first, second)
    _assert_equal(len(first), len(FEATURE_NAMES))
    probabilities = EvidenceOnlyProvider().predict(
        {"channels": ["SEARCH_FORM"]},
        "en:WHO",
        "en:World Health Organization",
    )
    _assert_close(probabilities.equivalent, 0.75)
    metrics = probability_metrics(
        [0, 3],
        [
            [0.8, 0.04, 0.04, 0.04, 0.04, 0.04],
            [0.05, 0.05, 0.05, 0.75, 0.05, 0.05],
        ],
    )
    _assert_equal(metrics.rows, 2)
    if metrics.log_loss is None or metrics.log_loss <= 0:
        raise AssertionError("probability metrics must produce positive log loss")


def test_portable_sqlite_ledger() -> None:
    with tempfile.TemporaryDirectory(prefix="aepgs-portable-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        engine = SafeRelationEngine(["A", "B", "C"])
        tasks = [_task("AB", "A", "B"), _task("AC", "A", "C")]
        with PortableLedger(path) as ledger:
            ledger.initialize(tasks, engine, "m1")
            _assert_equal(ledger.status_counts(), {"PENDING": 2})
            engine.apply_wave(
                [
                    _decision("X1", "A", "B", Relation.BROADER_THAN),
                    _decision("X2", "B", "C", Relation.BROADER_THAN),
                ]
            )
            proof = engine.inference_proof("AC", "A", "C")
            if proof is None:
                raise AssertionError("A-C should be inferred")
            _assert_equal(ledger.mark_inferred_before_model({"AC": proof}), 1)
            _assert_equal(ledger.status_counts(), {"INFERRED": 1, "PENDING": 1})
            # Ledger snapshot is deliberately still v0 until a committed wave.
            _assert_equal(ledger.load_engine().version, 0)


def run_self_tests() -> dict[str, Any]:
    tests = [
        test_transitive_reduction_and_reachability,
        test_equivalent_component_expansion,
        test_disjoint_descendant_propagation,
        test_exact_delta_cancels_pending_candidate,
        test_hybrid_gain_counts_future_call_savings,
        test_versioned_queue_and_stale_wave,
        test_snapshot_roundtrip,
        test_probability_features_and_metrics,
        test_portable_sqlite_ledger,
    ]
    started = time.perf_counter()
    completed = []
    for test in tests:
        test()
        completed.append(test.__name__)
    return {
        "status": "PASS",
        "schema_version": PORTABLE_SCHEMA_VERSION,
        "tests": completed,
        "test_count": len(completed),
        "elapsed_seconds": time.perf_counter() - started,
    }


def demo() -> dict[str, Any]:
    """Small end-to-end demonstration of closure expansion and queue pruning."""
    engine = SafeRelationEngine(["World", "Vehicle", "Car", "EV"])
    engine.apply_wave(
        [
            _decision("WV", "World", "Vehicle", Relation.BROADER_THAN),
            _decision("CE", "Car", "EV", Relation.BROADER_THAN),
        ]
    )
    bridge = _task("VC", "Vehicle", "Car", Outcome.A_BROADER_THAN_B)
    pending = PendingCandidateIndex(
        [
            bridge,
            _task("WE", "World", "EV"),
            _task("WC", "World", "Car"),
            _task("VE", "Vehicle", "EV"),
        ]
    )
    score = HybridGainScorer(saved_cost_weight=1.0).score(
        engine, bridge, pending
    )
    committed = apply_wave_with_exact_delta(
        engine,
        [_decision("VC", "Vehicle", "Car", Relation.BROADER_THAN)],
    )
    pruned = prune_newly_inferred_candidates(
        engine,
        committed.delta,
        pending,
        exclude_pair_ids={"VC"},
    )
    return {
        "status": "PASS",
        "known_pair_counts": engine.known_pair_counts(),
        "reduced_edges": sorted(map(list, engine.reduced_edges)),
        "newly_inferred_pairs": [
            {
                "pair": list(item.pair),
                "relation": item.relation.value,
                "direction": list(item.broader_direction) if item.broader_direction else None,
            }
            for item in committed.delta.newly_inferred_pairs
        ],
        "cancelled_pending_candidates": sorted(pruned.proofs),
        "hybrid_score": dataclasses.asdict(score),
    }


def describe() -> dict[str, Any]:
    return {
        "schema_version": PORTABLE_SCHEMA_VERSION,
        "current_verified": [
            "SafeRelationEngine",
            "SingleGainScorer",
            "VersionedPriorityQueue",
            "FrozenWavePlanner",
            "PortableLedger reference",
            "16-feature probability interface",
        ],
        "vnext_reference": [
            "Exact O(N^2) GraphDelta correctness oracle",
            "PendingCandidateIndex",
            "Queue prune and inference proofs",
            "HybridGainScorer",
        ],
        "production_replacements_required": [
            "component-level GraphDelta",
            "persistent reverse candidate index",
            "dynamic reachability",
            "incremental priority refresh",
            "project-specific model HTTP adapters",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("self-test", "demo", "describe"),
        help="Run embedded tests, a small closure/pruning demo, or print scope.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "self-test":
        result = run_self_tests()
    elif args.command == "demo":
        result = demo()
    elif args.command == "describe":
        result = describe()
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
