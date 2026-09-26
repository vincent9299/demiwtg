#!/usr/bin/env python3
"""Validate and read frozen three-model bench200 scores and judge provenance."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from evaluation.operaters.evidence import read_bytes as evidence_bytes, read_text as evidence_text, exists as evidence_exists

EVAL_DIR = Path(__file__).resolve().parents[1]
EDIT_DIR = EVAL_DIR.parents[2] / "benchmark/edit/v1"
REPO = EDIT_DIR.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from evaluation.edit.v1.operaters import frozen_scores as previous
from evaluation.edit.v1.operaters.frozen_scores import EDIT_DIMS, PHI

NAMES = {'a': 'Qwen-Image-Edit-2511', 'b': 'BAGEL-7B-MoT', 'g': 'Gemini 3.1 Flash Image'}
TAG = 'bench200-three-model-qib-v22-astra-medium'


def _check_hash(path, expected):
    path = previous.artifact_path(path)
    if not isinstance(expected, str) or hashlib.sha256(evidence_bytes(path)).hexdigest() != expected:
        raise ValueError(f'Frozen file changed or hash missing: {path}')


def verify_gemini_inputs(run, candidate, qs, manifest, scores, old):
    """Check per-question materialization and actual files beyond the freeze summary."""
    from evaluation.edit.v1.operaters.frozen_scores import load_template, _extract_json
    template_path = EVAL_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md'
    mode, template, blocks = load_template(argparse.Namespace(template=template_path))
    if mode != 'qib':
        raise ValueError('Gemini requires the frozen QIB template')
    index = previous.read_jsonl(candidate / 'prompts/index.jsonl')
    if not set(qs) <= set(index) <= set(old['qs']):
        raise ValueError('Gemini prompt index does not cover the selected qids')
    old_threads, old_sessions = set(), set()
    for c in ('a', 'b'):
        for qid in old['qs']:
            path = old['run'] / 'runtime/provenance' / f'{c}_{qid}.json'
            provenance = json.loads(evidence_text(path))
            old_threads.add(provenance['thread_id'])
            old_sessions.add(str(Path(provenance['session_path']).resolve()))
    threads, sessions, provenance_rows = set(), set(), []
    for qid, q in qs.items():
        m, score, entry = manifest[qid], scores[qid], index[qid]
        job = f'{candidate.name}_{qid}'
        provenance = json.loads(evidence_text(run / 'runtime/provenance' / f'{job}.json'))
        for key, expected in (('job', job), ('model', 'gpt-6-astra'), ('reasoning_effort', 'medium'),
                              ('context_verified', True), ('tool_calls', 0), ('provider', 'openai')):
            if provenance.get(key) != expected:
                raise ValueError(f'Gemini provenance mismatch: {qid}/{key}')
        thread = provenance.get('thread_id')
        session = str(Path(provenance.get('session_path', '')).resolve())
        if (not thread or thread in threads or thread in old_threads
                or session in sessions or session in old_sessions):
            raise ValueError(f'Reused or missing judge context: {qid}')
        _check_hash(Path(session), provenance.get('session_sha256'))
        threads.add(thread); sessions.add(session)
        if provenance.get('input_hashes') != m['inputs']:
            raise ValueError(f'Gemini provenance input hashes mismatch: {qid}')
        _check_hash(Path(m['before']), m['inputs']['source_sha256'])
        _check_hash(Path(m['after']), m['inputs']['output_sha256'])
        instruction_sha = hashlib.sha256(q['edit_instruction'].encode()).hexdigest()
        if instruction_sha != m['inputs']['instruction_sha256'] or entry['instruction_sha256'] != instruction_sha:
            raise ValueError(f'Gemini instruction hash mismatch: {qid}')
        expected_prompt = (template.replace('{{EDIT_TYPE}}', q['edit_type'])
                           .replace('{{TYPE_NOTES}}', blocks[q['edit_type']].strip())
                           .replace('{{INSTRUCTION}}', q['edit_instruction']))
        prompt_path = candidate / 'prompts' / f'{qid}.txt'
        expected_prompt_sha = hashlib.sha256(expected_prompt.encode()).hexdigest()
        _check_hash(prompt_path, expected_prompt_sha)
        if entry['prompt_sha256'] != expected_prompt_sha or provenance.get('prompt_sha256') != expected_prompt_sha:
            raise ValueError(f'Gemini prompt provenance mismatch: {qid}')
        if entry.get('candidate_id') != m['candidate_id'] or score.get('candidate_id') != m['candidate_id']:
            raise ValueError(f'Gemini candidate binding mismatch: {qid}')
        raw_path = candidate / 'raw' / f'{qid}.txt'
        _check_hash(raw_path, provenance.get('raw_sha256'))
        raw = _extract_json(evidence_text(raw_path))
        if raw.get('validity') != score.get('validity'):
            raise ValueError(f'Raw/score validity mismatch: {qid}')
        raw_dims = raw.get('raw_dimensions', [])
        if len(raw_dims) != 3:
            raise ValueError(f'Raw dimension count mismatch: {qid}')
        for raw_dim, scored_dim in zip(raw_dims, score['raw_dimensions']):
            if (raw_dim.get('label') != scored_dim['label'] or raw_dim.get('tier') != scored_dim['tier']
                    or str(raw_dim.get('reason', '')).strip() != scored_dim['reason']):
                raise ValueError(f'Raw/score dimension mismatch: {qid}')
        provenance_rows.append(provenance)
    if len(threads) != len(qs) or len(sessions) != len(qs):
        raise ValueError('Gemini requires one distinct fresh session per scored qid')
    return provenance_rows


def load_three_review(gemini_run, candidate_subdir='g', allow_partial=False):
    """Require freezing by default; explicit partial review still verifies every available judgment."""
    import pandas as pd
    run = previous.artifact_path(gemini_run).resolve()
    frozen_path = run / 'runtime/frozen.json'
    is_partial = not evidence_exists(frozen_path)
    if is_partial and not allow_partial:
        return None
    old = previous.load_review()
    if old is None:
        return None
    candidate = (run / candidate_subdir).resolve()
    if not candidate.is_relative_to(run):
        raise ValueError('Candidate directory must be inside the Gemini run')
    frozen = None
    required = {str((candidate / name).relative_to(run))
                for name in ('scores.jsonl', 'report.json', 'blind_manifest.jsonl')}
    if not is_partial:
        frozen = json.loads(evidence_text(frozen_path))
        expected = dict(n_questions=200, n_candidates=200, n_unique_contexts=200,
                        judge_model='gpt-6-astra', reasoning_effort='medium')
        for key, value in expected.items():
            if frozen.get(key) != value:
                raise ValueError(f'Gemini frozen configuration mismatch: {key}')
        artifacts = frozen.get('artifacts', {})
        if not isinstance(artifacts, dict) or not required <= set(artifacts):
            raise ValueError('Gemini frozen artifact map is incomplete')
        for relative, expected_hash in artifacts.items():
            path = (run / relative).resolve()
            if Path(relative).is_absolute() or not path.is_relative_to(run):
                raise ValueError(f'Invalid frozen path: {relative}')
            _check_hash(path, expected_hash)
        _check_hash(EDIT_DIR / 'bench200/questions.jsonl', frozen.get('questions_sha256'))
        _check_hash(EVAL_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md', frozen.get('template_sha256'))
        _check_hash(EVAL_DIR / 'eval_codex_score.py', frozen.get('pipeline_sha256'))
    # This is an in-memory read snapshot, not a frozen.json or a declaration of completion.
    read_snapshot = {relative: hashlib.sha256(evidence_bytes(run / relative)).hexdigest()
                     for relative in required | {str((candidate / 'prompts/index.jsonl').relative_to(run))}}
    scores = previous.read_jsonl(candidate / 'scores.jsonl')
    manifest = previous.read_jsonl(candidate / 'blind_manifest.jsonl')
    qs = old['qs']
    if not scores or not set(scores) <= set(manifest) <= set(qs):
        raise ValueError('Gemini scores/manifest do not match benchmark qids')
    if not is_partial and (set(scores) != set(qs) or set(manifest) != set(qs)):
        raise ValueError('Frozen Gemini requires all 200 qids')
    report = json.loads(evidence_text(candidate / 'report.json'))
    if report.get('n') != len(scores):
        raise ValueError('Gemini report count differs from scored qids')
    provenance = verify_gemini_inputs(run, candidate, {qid: qs[qid] for qid in scores}, manifest, scores, old)
    totals, dimensions = [], []
    for qid, score in scores.items():
        q, m = qs[qid], manifest[qid]
        if score.get('schema') != 'edit-codex-v2-qib' or score.get('edit_type') != q['edit_type']:
            raise ValueError(f'Score schema/edit type mismatch: {qid}')
        if score.get('inputs') != m.get('inputs') or m.get('edit_instruction') != q['edit_instruction']:
            raise ValueError(f'Gemini question/image binding mismatch: {qid}')
        for key in ('source_sha256', 'instruction_sha256'):
            if m['inputs'][key] != old['manifests']['a'][qid]['inputs'][key]:
                raise ValueError(f'Cross-model input mismatch: {qid}/{key}')
        raw = score['raw_dimensions']
        if [d['label'] for d in raw] != EDIT_DIMS[q['edit_type']]:
            raise ValueError(f'Dimension contract mismatch: {qid}')
        tiers = [d['tier'] for d in raw]
        if len(tiers) != 3 or any(t not in PHI for t in tiers):
            raise ValueError(f'Invalid tiers: {qid}')
        status = score['validity']['status']
        if status not in ('ok', 'model_failure', 'invalid_question', 'judge_unscorable'):
            raise ValueError(f'Invalid validity status: {qid}')
        official_tiers = [tiers[0], min(tiers[0], tiers[1]), min(tiers[0], tiers[2])]
        if status == 'model_failure':
            official_tiers = [0, 0, 0]
        official = [PHI[t] for t in official_tiers]
        if ([score['official_dimensions'][f'd{i}'] for i in (1, 2, 3)] != official
                or [score['official_tiers'][f'd{i}'] for i in (1, 2, 3)] != official_tiers
                or abs(score['official_total'] - sum(official) / 3) >= .001):
            raise ValueError(f'Official mapping mismatch: {qid}')
        base = dict(**q, model=NAMES['g'], candidate='g',
                    cohort='pilot20' if qid in old['pilot'] else '新增180',
                    status=status, counted=status in ('ok', 'model_failure'))
        totals.append(dict(**base, official=score['official_total'], raw_mapped=sum(PHI[t] for t in tiers)/3,
                           clamped=any(t > tiers[0] for t in tiers[1:]) if status == 'ok' else False))
        for i, d in enumerate(raw, 1):
            dimensions.append(dict(**base, dim=f'd{i}', label=d['label'], tier=d['tier'],
                                   official_tier=official_tiers[i-1], raw_mapped=PHI[d['tier']], official=official[i-1]))
    total = pd.concat([old['total'], pd.DataFrame(totals)], ignore_index=True)
    dim = pd.concat([old['dim'], pd.DataFrame(dimensions)], ignore_index=True)
    common = set.intersection(*(set(total[(total.candidate == c) & total.counted].qid) for c in NAMES))
    for relative, expected_hash in read_snapshot.items():
        _check_hash(run / relative, expected_hash)
    return dict(qs=qs, pilot=old['pilot'], total=total, dim=dim, common=common,
                partial=is_partial, missing_gemini=set(qs)-set(scores), read_snapshot=read_snapshot,
                scores={**old['scores'], 'g': scores}, manifests={**old['manifests'], 'g': manifest},
                runs={'a': old['run'], 'b': old['run'], 'g': run},
                candidate_dirs={'a': old['run']/'a', 'b': old['run']/'b', 'g': candidate},
                frozen={'old': old['frozen'], 'gemini': frozen}, gemini_provenance=provenance)
