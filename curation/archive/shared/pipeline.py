"""Freeze source-bound development cases, build matched inputs, audit results.

Runtime protocol and artifacts are consumed by this CLI and the review notebook.
No formal benchmark compilation and no writes to legacy assets.
"""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = _ARCHIVE_ROOT
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bagel_runner import encoded, publish, sha, validate_jobs

STATE = ROOT / 'state/curation/knowledge_application_v1'
LEVELS = {'direct', 'conditional', 'relational', 'compositional'}
TYPES = {'特征与结构', '属性与状态', '关系与组织', '过程与变化', '功能与机制', '规则与约定'}
PROTOCOL = {
    'version': 'knowledge_application_v1', 'scope': 'development_only_before_formal_200',
    'reviewer': 'assistant_not_human_gold', 'conditions': ['baseline', 'text', 'image', 'multimodal'],
    'models': ['BAGEL-7B-MoT', 'openrouter/google/gemini-3.1-flash-image'],
    'initial_cases_target': 12, 'initial_repeats': 1, 'paid_attempt_cap': 60,
    'selection': 'Evidence validity and observable knowledge dependency before generation; preserve all outcomes.',
    'inputs': 'Original task plus raw verified excerpts and/or actual source images; no answer-caption compiler.',
    'retrieval': 'Four primary conditions use oracle selected evidence, not actual retrieval.',
    'metrics': ['knowledge_pass', 'observable', 'execution_pass', 'joint_pass', 'quality'],
    'edit_preservation': 'Both adapters request square outputs. Judge preservation of non-target scene structure and viewpoint; permit aspect-ratio padding or modest cropping only if required anchors remain, not arbitrary background replacement. Do not require pixel identity.',
    'process_success': ['29 domains have a reviewed sample or explicit checked gap',
                        'Frozen evidence, roles, prompts, criteria and matched model inputs',
                        'Every output or generation error retained and assessed',
                        'Reusable construction rules and unresolved limitations reported'],
    'empirical_success': 'Replicated knowledge gains in at least two independent knowledge families; report whether both models improve, not require SOTA parity.',
    'repeat_rule': 'Select up to three knowledge families with initial multimodal versus baseline gain; repeat both arms for both models with a new BAGEL seed, max 12 further paid attempts. This is adaptive development confirmation, not unbiased test accuracy.',
    'failure_rule': 'If direct source use fails, diagnose evidence sufficiency and explicit target execution separately; do not silently replace raw-evidence arm with answer caption.',
    'split_rule': 'All cases used here stay development; formal test separate by concept/rule family and source/image near-duplicate groups.',
    'training_contract': ['original_task', 'source_materials_with_roles_and_hashes', 'necessary_evidence_selection_and_application_links', 'verified_target_image_and_checks'],
    'training_exclusion': 'No development/test target, answer caption or near-duplicate target in retrieval. Generation output alone is not a verified training target. No fine-tuning is performed in this pre-formal run.',
    'formal_200': 'Excluded from this run. Final track/count choice remains pending and does not block development.',
}


def read(path):
    return json.loads(Path(path).read_text())


def check_case(c):
    required = ['question_id', 'concept', 'domain', 'knowledge_types', 'application_level',
                'task', 'prompt', 'knowledge_text', 'sources', 'reference_images',
                'application_links', 'knowledge_checks', 'execution_checks', 'exceptions']
    for k in required:
        if k not in c:
            raise ValueError(f"{c.get('question_id')}: missing {k}")
    if c['application_level'] not in LEVELS or c['task'] not in {'edit', 't2i'}:
        raise ValueError(f"Invalid level/task: {c['question_id']}")
    if not c['knowledge_types'] or set(c['knowledge_types']) - TYPES:
        raise ValueError(f"Invalid knowledge types: {c['question_id']}")
    if not c['sources'] or not c['knowledge_checks'] or not c['application_links']:
        raise ValueError('Missing grounding')
    ids = {s['source_id'] for s in c['sources']}
    for s in c['sources']:
        if not all(s.get(k) for k in ['url', 'quote', 'support_scope']):
            raise ValueError('Incomplete source')
    for k in c['knowledge_checks']:
        if not k.get('source_ids') or set(k['source_ids']) - ids:
            raise ValueError('Unbound knowledge criterion')
    for im in c['reference_images'] + ([c['edit_source']] if c.get('edit_source') else []):
        p = Path(im['path'])
        if not p.is_absolute() or sha(p.read_bytes()) != im['sha256']:
            raise ValueError(f'Image missing or changed: {p}')
    if bool(c.get('edit_source')) != (c['task'] == 'edit'):
        raise ValueError('Edit source/task mismatch')
    if not c['reference_images']:
        raise ValueError('Four-arm development needs an actual reference; use a separate text-only batch otherwise')
    if any(not im.get('viewed') for im in c['reference_images']):
        raise ValueError('Unviewed reference')


def freeze(args):
    data = read(args.cases)
    cases = data['cases']
    ids = [c['question_id'] for c in cases]
    if len(set(ids)) != len(ids) or not 1 <= len(cases) <= 12:
        raise ValueError('Duplicate IDs or outside small-batch bound')
    for c in cases:
        check_case(c)
    out = Path(args.out)
    wanted = {s.get('page_sha') for c in cases for s in c['sources'] if s.get('page_sha')}
    found = {}
    if wanted:
        with (ROOT / 'state/collect/docs_clean/pages_clean.jsonl').open() as stream:
            for line in stream:
                p = json.loads(line)
                if p.get('page_sha') in wanted:
                    found[p['page_sha']] = p
    for c in cases:
        for s in c['sources']:
            page = found.get(s.get('page_sha'))
            if page and page.get('url') == s['url']:
                raw_page = encoded(page)
                dest = out / 'source_snapshots' / (s['page_sha'] + '.json')
                publish(dest, raw_page)
                s['snapshot_path'] = str(dest.resolve())
                s['snapshot_sha256'] = sha(raw_page)
                s['origin'] = 'existing_local_clean_document'
            elif s.get('snapshot_path'):
                source_path = Path(s['snapshot_path'])
                digest = sha(source_path.read_bytes())
                if s.get('content_sha256') and s['content_sha256'] != digest:
                    raise ValueError('Changed source snapshot: ' + str(source_path))
                s['snapshot_sha256'] = digest
            else:
                s['snapshot_status'] = 'missing_full_source_snapshot_explicit_development_gap'
    publish(out / 'protocol.json', encoded(PROTOCOL))
    publish(out / 'cases.json', encoded(data))
    jobs = []
    for c in cases:
        for condition in PROTOCOL['conditions']:
            images = []
            if c.get('edit_source'):
                images.append(dict(role='edit_source', **{k: c['edit_source'][k] for k in ['path', 'sha256']}))
            if condition in {'image', 'multimodal'}:
                images.extend(dict(role='retrieval_reference', **{k: im[k] for k in ['path', 'sha256']}) for im in c['reference_images'])
            prompt = ('TASK\n' + c['prompt'] + '\n\n'
                      'Produce one image fulfilling the task. An edit_source image is the scene to edit; '
                      'retrieval_reference images are knowledge materials, not the target composition. '
                      'Use relevant supplied materials to infer the required appearance. Do not reproduce source-page layouts or add explanatory text unless the task requests it.')
            if condition in {'text', 'multimodal'}:
                prompt += '\n\nSOURCE MATERIALS\n' + c['knowledge_text']
            jobs.append(dict(job_id=c['question_id'] + '__' + condition + '__r1', question_id=c['question_id'],
                             task=c['task'], condition=condition, prompt=prompt, images=images, seed=20260912))
    raw = ''.join(json.dumps(j, ensure_ascii=False, sort_keys=True) + '\n' for j in jobs).encode()
    publish(out / 'jobs.jsonl', raw)
    validate_jobs(out / 'jobs.jsonl')
    print(json.dumps({'cases': len(cases), 'jobs_per_model': len(jobs), 'jobs_sha256': sha(raw),
                      'levels': dict(Counter(c['application_level'] for c in cases)),
                      'tasks': dict(Counter(c['task'] for c in cases))}, ensure_ascii=False))


def legacy(args):
    snapshot = read(STATE / 'legacy_snapshot.json')
    changed = [p for p, digest in snapshot['files'].items() if not Path(p).exists() or sha(Path(p).read_bytes()) != digest]
    report = {'checked_files': len(snapshot['files']), 'changed_files': changed,
              'scope': 'Legacy paths captured before new-version implementation; running preannotation state excluded.'}
    print(json.dumps(report, ensure_ascii=False))
    if args.out:
        publish(Path(args.out), encoded(report))
    if changed:
        raise SystemExit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    f = sub.add_parser('freeze'); f.add_argument('--cases', required=True); f.add_argument('--out', required=True); f.set_defaults(func=freeze)
    l = sub.add_parser('legacy-check'); l.add_argument('--out'); l.set_defaults(func=legacy)
    args = p.parse_args(); args.func(args)


if __name__ == '__main__':
    main()
