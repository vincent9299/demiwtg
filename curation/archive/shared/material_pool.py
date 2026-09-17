"""Deterministic material leads, not questions or approved knowledge.

build samples 20 slots per original domain using branch-stratified hash order.
summary is the public reader used by reporting. No network or model calls.
"""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time

REPO = _ARCHIVE_ROOT
META = REPO / 'datasets/demiwtg/meta'
CLEAN = REPO / 'state/collect/docs_clean/pages_clean.jsonl'
AUDIT = REPO / 'state/curation/supply_audit_v1/report.json'
DEFAULT = REPO / 'state/curation/knowledge_application_v1/material_pool.json'
SEED = 'knowledge_application_v1.material_pool.1'


def rank(*parts):
    return hashlib.sha256('\0'.join((SEED, *parts)).encode()).hexdigest()


def fingerprint(path):
    stat = path.stat()
    return dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def read_pool(path=DEFAULT):
    data = json.loads(Path(path).read_text())
    if data.get('version') != SEED:
        raise ValueError('Unknown material pool version')
    rows = data['candidates']
    if len({r['slot_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate slot IDs')
    return data


def summary(data):
    rows = data['candidates']
    unique = {r['concept']: r for r in rows}
    return dict(domain_slots=len(rows), unique_concepts=len(unique),
                cross_domain_repeated_slots=len(rows)-len(unique),
                slots_with_clean_docs=sum(r['source_availability']['clean_page_count'] > 0 for r in rows),
                unique_concepts_with_clean_docs=sum(r['source_availability']['clean_page_count'] > 0 for r in unique.values()),
                unique_concepts_with_image_refs=sum(bool(r['reference_images']) for r in unique.values()),
                image_refs=sum(len(r['reference_images']) for r in unique.values()),
                readable_hash_matched_refs=sum(i['sha256_matches'] is True for r in unique.values() for i in r['reference_images']),
                per_domain=dict(Counter(r['original_domain'] for r in rows)),
                reviewed_identity=0, approved_questions=0,
                scope='Counts describe original-mount candidate leads; no semantic-domain, fact or image-identity approval.')


def domain_branches(node, known):
    """Preserve all original paths; top-level branch is only a sampling stratum."""
    members = defaultdict(set)
    paths = defaultdict(set)
    def visit(part, branch):
        for name in part.get('instances', []):
            if name in known:
                members[branch].add(name)
                paths[name].add(part['path'])
        for child in part.get('children', []):
            visit(child, branch)
    if node.get('instances'):
        visit({'instances': node['instances'], 'path': node['path']}, '(direct)')
    for child in node.get('children', []):
        visit(child, child['name'])
    return members, paths


def stratified(members, domain, eligible, selected, count, lane):
    queues = {}
    for branch, names in members.items():
        queues[branch] = sorted((n for n in names if n not in selected and eligible(n)),
                                key=lambda n: rank(domain, branch, lane, n))
    order = sorted(queues, key=lambda branch: rank(domain, lane, branch))
    result = []
    while len(result) < count:
        added = False
        for branch in order:
            queue = queues[branch]
            while queue and queue[0] in selected:
                queue.pop(0)
            if queue:
                name = queue.pop(0)
                selected.add(name)
                result.append((name, branch, lane))
                added = True
                if len(result) == count:
                    break
        if not added:
            break
    return result


def image_path(row):
    raw = row.get('blob_path') or row.get('path')
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else META.parent / path
    digest, ext = row.get('sha256'), row.get('ext')
    return META.parent / 'blobs' / digest[:2] / f'{digest}.{ext}' if digest and ext else None


def inspect_file(path, expected):
    result = dict(local_exists=False, readable=False, actual_sha256=None, sha256_matches=None,
                  file_error=None)
    if path is None:
        result['file_error'] = 'No resolvable path in manifest'
        return result
    try:
        result['local_exists'] = path.is_file()
        if not result['local_exists']:
            result['file_error'] = 'Missing local file'
            return result
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        result.update(readable=True, actual_sha256=digest.hexdigest(),
                      sha256_matches=digest.hexdigest() == expected if expected else None)
    except OSError as exc:
        result['file_error'] = str(exc)
    return result


def build(output=DEFAULT):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Immutable pool output exists: {output}')
    inputs = [META/'concepts.json', META/'taxonomy.json', META/'images.jsonl', CLEAN, AUDIT]
    before = {str(p): fingerprint(p) for p in inputs}
    concepts = {c['name']: c for c in json.loads((META/'concepts.json').read_text())['concepts']}
    tree = json.loads((META/'taxonomy.json').read_text())['tree']
    audit = json.loads(AUDIT.read_text())
    docs = defaultdict(list)
    for line in CLEAN.open():
        p = json.loads(line)
        for name in p.get('concepts', []):
            docs[name].append(dict(url=p['url'], page_sha=p['page_sha'], title=p.get('title'),
                                   clean_text_sha256=hashlib.sha256(p['text'].encode()).hexdigest(),
                                   clean_chars=len(p['text']), fetched_at=p.get('fetched_at'),
                                   status='associated_unverified_not_read_for_fact'))
    rows = []
    for domain in tree['children']:
        name = domain['name']
        branches, paths = domain_branches(domain, concepts)
        selected = set()
        picks = stratified(branches, name, lambda n: bool(docs[n]), selected, 5, 'docs_priority')
        picks += stratified(branches, name, lambda n: not docs[n], selected, 20-len(picks), 'without_clean_docs')
        picks += stratified(branches, name, lambda n: True, selected, 20-len(picks), 'general_fill')
        if len(picks) != 20:
            raise ValueError(f'{name}: only {len(picks)} distinct candidates available')
        for index, (concept, branch, lane) in enumerate(picks, 1):
            pages = sorted(docs[concept], key=lambda p: (p['page_sha'], p['url']))
            rows.append(dict(slot_id=f'{rank(name)[:12]}_{index:02}', original_domain=name,
                             concept=concept, concept_id=rank('concept', concept),
                             aliases=concepts[concept].get('aliases', []), carriers=concepts[concept].get('carriers'),
                             selected_branch=branch, original_mount_paths=sorted(paths[concept]),
                             selection_lane=lane, status='unreviewed_material_lead',
                             semantic_primary_domain=None, domain_status='original_mount_requires_semantic_review',
                             source_availability=dict(clean_page_count=len(pages), references=pages[:2],
                                                      passage_reviewed=False, sufficient_for_question=None),
                             image_associations=0, reference_images=[], image_identity='unreviewed',
                             knowledge_status='unverified', question=None))
    needed = {r['concept'] for r in rows}
    refs = defaultdict(list)
    seen = defaultdict(set)
    counts = Counter()
    manifest_hash = hashlib.sha256()
    scanned = 0
    # Exactly one complete streaming scan, collecting at most two distinct refs/concept.
    with (META/'images.jsonl').open('rb') as stream:
        for line in stream:
            manifest_hash.update(line)
            image = json.loads(line)
            scanned += 1
            linked = set(image.get('concepts') or image.get('instances') or []) & needed
            for concept in linked:
                counts[concept] += 1
                digest = image.get('sha256')
                if len(refs[concept]) >= 2 or digest in seen[concept]:
                    continue
                seen[concept].add(digest)
                path = image_path(image)
                refs[concept].append(dict(path=str(path) if path else None, sha256=digest,
                                          source=image.get('source'), landing_url=image.get('landing_url'),
                                          content_url=image.get('content_url'), license=image.get('license'),
                                          author=image.get('author'), viewed=False,
                                          support_scope='Manifest association only; appearance/identity/knowledge support not reviewed.'))
            if scanned % 500000 == 0:
                print(f'Scanned {scanned} manifest rows', flush=True)
    inspected = {}
    for images in refs.values():
        for image in images:
            key = image['path'], image['sha256']
            if key not in inspected:
                inspected[key] = inspect_file(Path(image['path']) if image['path'] else None, image['sha256'])
            image.update(inspected[key])
    domain_membership = defaultdict(list)
    for row in rows:
        domain_membership[row['concept']].append(row['original_domain'])
    for row in rows:
        row['image_associations'] = counts[row['concept']]
        row['reference_images'] = refs[row['concept']]
        row['selected_domain_memberships'] = domain_membership[row['concept']]
        row['cross_domain_duplicate'] = len(domain_membership[row['concept']]) > 1
    if any(fingerprint(p) != before[str(p)] for p in inputs):
        raise RuntimeError('Inputs changed during scan; output not published')
    payload = dict(version=SEED, created_at=time.time(), candidates=rows, inputs=before,
                   methodology=dict(slots_per_domain=20, docs_priority_cap=5,
                                    stratification='Round-robin top-level branches; SHA256(seed,domain,branch,lane,concept) order; unique within domain.',
                                    remainder='Prefer no-clean-docs concepts; general fill only if needed.',
                                    image_selection='First <=2 distinct SHA associations per concept in one manifest scan; not best-view selection.',
                                    manifest_rows_scanned=scanned, manifest_sha256=manifest_hash.hexdigest(),
                                    audit_context=audit['global'],
                                    review_scope='Material availability only; semantic domains, facts, licenses and image identity are unreviewed. No generated questions or model calls.'),
                   summary=None)
    payload['summary'] = summary(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create prevents overwriting another build's output.
    with output.open('x') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'summary'])
    parser.add_argument('--out', type=Path, default=DEFAULT)
    args = parser.parse_args()
    data = build(args.out) if args.command == 'build' else read_pool(args.out)
    print(json.dumps(summary(data), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
