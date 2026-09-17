"""Freeze bounded adaptive development replication, retaining original inputs."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bagel_runner import encoded, publish, sha, validate_jobs
from pipeline import read


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pilot', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--case-ids', required=True)
    p.add_argument('--reason', required=True)
    args = p.parse_args()
    selected = args.case_ids.split(',')
    if not 1 <= len(selected) <= 2 or len(set(selected)) != len(selected):
        raise ValueError('Select at most two distinct cases')
    data = read(args.pilot / 'cases.json')
    cases = [c for c in data['cases'] if c['question_id'] in selected]
    if len(cases) != len(selected):
        raise ValueError('Unknown case')
    raw = (args.pilot / 'jobs.jsonl').read_bytes()
    originals = [json.loads(s) for s in raw.decode().splitlines()]
    jobs = []
    for j in originals:
        if j['question_id'] in selected and j['condition'] in {'baseline', 'multimodal'}:
            for rep, seed in [(2, 20260913), (3, 20260914)]:
                copy = dict(j, job_id=f"{j['question_id']}__{j['condition']}__r{rep}", seed=seed)
                jobs.append(copy)
    data['cases'] = cases
    data['scope'] = 'adaptive_development_replication_not_independent_test'
    protocol = {'version': 'knowledge_application_v1.repeat.1', 'pilot_jobs_sha256': sha(raw),
                'selected_cases': selected, 'selection_reason': args.reason,
                'conditions': ['baseline', 'multimodal'], 'additional_repeats': 2,
                'jobs_per_model': len(jobs), 'paid_cap_this_run': len(jobs),
                'total_paid_cap_all_new_runs': 60,
                'already_used_pilot_and_image_primary': 52,
                'inputs': 'Same exact original pilot prompts and image bytes. Only BAGEL seed changes; Gemini provider has no supported seeded noise.',
                'scope': 'Positive cases selected after pilot under its adaptive replication rule; no formal test generalization or unbiased dataset accuracy claim.'}
    publish(args.out / 'cases.json', encoded(data))
    publish(args.out / 'protocol.json', encoded(protocol))
    publish(args.out / 'review_protocol.json', (args.pilot / 'review_protocol.json').read_bytes())
    publish(args.out / 'jobs.jsonl', ''.join(json.dumps(j, ensure_ascii=False, sort_keys=True)+'\n' for j in jobs).encode())
    validate_jobs(args.out / 'jobs.jsonl')
    print(json.dumps(protocol, ensure_ascii=False))


if __name__ == '__main__':
    main()
