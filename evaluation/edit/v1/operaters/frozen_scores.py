#!/usr/bin/env python3
"""Validate and read frozen two-model bench200 scores."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from evaluation.operaters.evidence import read_bytes as evidence_bytes, read_text as evidence_text, exists as evidence_exists

EVAL_DIR = Path(__file__).resolve().parents[1]
EDIT_DIR = EVAL_DIR.parents[2] / "benchmark/edit/v1"
RUN = EDIT_DIR / 'bench200/scores_qib_v22_astra_medium_20260908'
NAMES = {'a': 'Qwen-Image-Edit-2511', 'b': 'BAGEL-7B-MoT'}
TAG = 'bench200-qib-v22-astra-medium-20260908'


def artifact_path(value):
    """Resolve pre-versioning absolute locators without rewriting frozen records."""
    path = Path(value)
    old_root = EDIT_DIR.parent
    if path.is_absolute() and path.is_relative_to(old_root):
        suffix = path.relative_to(old_root)
        if suffix.parts and suffix.parts[0] not in {'v1', 'v2'}:
            return EDIT_DIR / suffix
    return path


def read_jsonl(path):
    rows = [json.loads(x) for x in evidence_text(artifact_path(path)).splitlines() if x.strip()]
    by_id = {r['qid']: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError(f'Duplicate qid in {path}')
    return by_id


def load_review(run=RUN):
    """No temporary scores are opened before the orchestrator freezes the run."""
    import pandas as pd
    run = artifact_path(run)
    if not evidence_exists(run / 'runtime/frozen.json'):
        return None
    import sys
    repo = str(EDIT_DIR.parents[2])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    frozen = json.loads(evidence_text(run / 'runtime/frozen.json'))
    expected_config = dict(n_questions=200, n_candidates=400, n_unique_contexts=400,
                           judge_model='gpt-6-astra', reasoning_effort='medium')
    for key, expected in expected_config.items():
        if frozen.get(key) != expected:
            raise ValueError(f'Frozen configuration mismatch: {key}')
    def check_hash(path, expected, label):
        if not isinstance(expected, str) or hashlib.sha256(evidence_bytes(path)).hexdigest() != expected:
            raise ValueError(f'Frozen artifact changed or hash missing: {label}')
    required = {f'{c}/{filename}' for c in NAMES
                for filename in ('scores.jsonl', 'report.json', 'blind_manifest.jsonl')}
    required |= {'comparison/paired_scores.jsonl', 'comparison/report.json'}
    artifacts = frozen.get('artifacts', {})
    if not isinstance(artifacts, dict) or not required <= set(artifacts):
        raise ValueError('Frozen artifacts map is incomplete')
    for relative, expected in artifacts.items():
        artifact = (run / relative).resolve()
        if Path(relative).is_absolute() or not artifact.is_relative_to(run.resolve()):
            raise ValueError(f'Invalid frozen artifact path: {relative}')
        check_hash(artifact, expected, relative)
    check_hash(EDIT_DIR / 'bench200/questions.jsonl', frozen.get('questions_sha256'), 'questions.jsonl')
    check_hash(EVAL_DIR / 'eval_codex_score.py', frozen.get('pipeline_sha256'), 'eval_codex_score.py')
    check_hash(EVAL_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md', frozen.get('template_sha256'), 'judge template')
    allowed = ('qid', 'edit_type', 'edit_instruction', 'level', 'suite', '_batch',
               'source_kind', 'source_type', 'construction_profile', 'difficulty')
    qs = {qid: {k: r.get(k) for k in allowed} for qid, r in
          read_jsonl(EDIT_DIR / 'bench200/questions.jsonl').items()}
    pilot = set(read_jsonl(EDIT_DIR / 'synth_v61_pilot/questions.jsonl'))
    assert len(qs) == 200 and len(pilot) == 20 and pilot <= set(qs)
    scores, manifests, totals, dims = {}, {}, [], []
    for candidate, name in NAMES.items():
        scores[candidate] = read_jsonl(run / candidate / 'scores.jsonl')
        manifests[candidate] = read_jsonl(run / candidate / 'blind_manifest.jsonl')
        assert set(scores[candidate]) == set(qs) == set(manifests[candidate])
        for qid, s in scores[candidate].items():
            m, q = manifests[candidate][qid], qs[qid]
            assert s['inputs'] == m['inputs'] and s['edit_type'] == q['edit_type']
            assert m['edit_instruction'] == q['edit_instruction']
            assert hashlib.sha256(q['edit_instruction'].encode()).hexdigest() == m['inputs']['instruction_sha256']
            status = s['validity']['status']
            assert status in ('ok', 'model_failure', 'invalid_question', 'judge_unscorable')
            base = {'qid': qid, 'model': name, 'candidate': candidate,
                    'cohort': 'pilot20' if qid in pilot else '新增180', **q,
                    'status': status, 'counted': status in ('ok', 'model_failure')}
            raw = s['raw_dimensions']
            assert [d['label'] for d in raw] == EDIT_DIMS[q['edit_type']]
            tiers = [d['tier'] for d in raw]
            assert len(tiers) == 3 and all(t in PHI for t in tiers)
            expected = [PHI[tiers[0]], PHI[min(tiers[0], tiers[1])], PHI[min(tiers[0], tiers[2])]]
            if status == 'model_failure':
                expected = [0, 0, 0]
            assert [s['official_dimensions'][f'd{i}'] for i in (1, 2, 3)] == expected
            assert abs(s['official_total'] - sum(expected) / 3) < .001
            totals.append({**base, 'official': s['official_total'],
                           'raw_mapped': sum(PHI[t] for t in tiers) / 3,
                           'clamped': any(tiers[i] > tiers[0] for i in (1, 2)) if status == 'ok' else False})
            for i, d in enumerate(raw, 1):
                dims.append({**base, 'dim': f'd{i}', 'label': d['label'], 'tier': d['tier'],
                             'official_tier': s['official_tiers'][f'd{i}'],
                             'raw_mapped': PHI[d['tier']], 'official': expected[i - 1]})
    for qid in qs:
        for key in ('source_sha256', 'instruction_sha256'):
            assert manifests['a'][qid]['inputs'][key] == manifests['b'][qid]['inputs'][key]
    total, dim = pd.DataFrame(totals), pd.DataFrame(dims)
    common = set.intersection(*(set(total[(total.candidate == c) & total.counted].qid) for c in NAMES))
    paired = read_jsonl(run / 'comparison/paired_scores.jsonl')
    assert set(paired) == common, 'Pipeline and review paired denominator differ'
    for qid, p in paired.items():
        assert abs(p['left_total'] - scores['a'][qid]['official_total']) < .001
        assert abs(p['right_total'] - scores['b'][qid]['official_total']) < .001
    rep = json.loads(evidence_text(run / 'comparison/report.json'))
    assert rep['overall']['n'] == len(common)
    return dict(run=run, frozen=frozen, qs=qs, scores=scores, manifests=manifests,
                total=total, dim=dim, common=common, pilot=pilot, paired=paired)

import re
from evaluation.edit.v1.operaters.judging import EDIT_DIMS
PHI = {0: 0, 1: 60, 2: 100}
TEMPLATE_RE = re.compile(r"<!--TEMPLATE-START-->\n(.*?)\n<!--TEMPLATE-END-->", re.S)
TYPE_BLOCK_RE = re.compile(r"<!--TYPE:(\w+)-->\n(.*?)\n<!--/TYPE-->", re.S)
OFFICIAL_BLOCK_RE = re.compile(r"<!--OFFICIAL_TYPE:(\w+)-->(.*?)<!--/OFFICIAL_TYPE-->", re.S)
OFFICIAL_CONTRACT = EVAL_DIR / "prompts/edit_score_prompts.json"

def _unique_blocks(pairs: list[tuple[str, str]], kind: str) -> dict:
    names = [name for name, _ in pairs]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"duplicate {kind} blocks: {dup}")
    return dict(pairs)

def _check_official_blocks(blocks: dict, source: str) -> None:
    """加载期闭锁：九类齐全、占位符唯一（评审 B-M03）。"""
    missing = sorted(set(EDIT_DIMS) - set(blocks))
    if missing:
        raise ValueError(f"official rubric missing types ({source}): {missing}")
    for edit_type, rubric_text in blocks.items():
        if rubric_text.count("<edit_prompt>") != 1:
            raise ValueError(
                f"bad <edit_prompt> placeholder ({source}): {edit_type}")

def load_template(args: argparse.Namespace) -> tuple[str, str | None, dict]:
    """Return (mode, qib_template_text, per-type rubric/notes blocks).

    modes: "qib"（TEMPLATE 块 + TYPE 分型块，占位符 {{...}}）与 "official"
    （ImgEdit 官方原文块，`<edit_prompt>` 单处占位；模板为 md 时九块与契约
    edit_score_prompts.json 做逐字节一致性校验，任何一侧改写即拒跑）。
    """
    if args.template.suffix == ".json":
        blocks = json.loads(args.template.read_text(encoding="utf-8"))
        _check_official_blocks(blocks, "contract json")
        return "official", None, blocks
    text = args.template.read_text(encoding="utf-8")
    match = TEMPLATE_RE.search(text)
    if match:
        blocks = _unique_blocks(TYPE_BLOCK_RE.findall(text), "TYPE")
        missing = sorted(set(EDIT_DIMS) - set(blocks))
        if missing:
            raise ValueError(f"template missing types: {missing}")
        return "qib", match.group(1), blocks
    pairs = OFFICIAL_BLOCK_RE.findall(text)
    if pairs:
        blocks = _unique_blocks(pairs, "OFFICIAL_TYPE")
        _check_official_blocks(blocks, str(args.template))
        if OFFICIAL_CONTRACT.is_file():
            contract = json.loads(OFFICIAL_CONTRACT.read_text(encoding="utf-8"))
            for edit_type, rubric_text in blocks.items():
                if edit_type not in contract:
                    raise ValueError(
                        f"official type not in contract json: {edit_type}")
                if rubric_text != contract[edit_type]:
                    raise ValueError(
                        f"official rubric block != contract json: {edit_type}")
        return "official", None, blocks
    raise ValueError(f"no template blocks found in {args.template}")

def _extract_json(text: str) -> dict:
    """判官裸输出可能包裹在多余文本里；取第一个可解码的 JSON 对象。"""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in judge output")
