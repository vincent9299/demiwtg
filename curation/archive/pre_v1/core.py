"""Storage, validation and fail-closed human approval for the pilot."""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import contextlib
import copy
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
from curation.prompts import CONTENT_TYPES

ROOT = _ARCHIVE_ROOT
DEFAULT_RUN = ROOT / 'state/curation/core_pilot_v3'

def blob_shas(blobs_root=None):
    """List content-addressed filenames without a stat/hash per image."""
    directory = Path(blobs_root) if blobs_root is not None else ROOT / 'datasets/demiwtg/blobs'
    present = set()
    if not directory.exists():
        return present
    for sub in directory.iterdir():
        if sub.is_dir():
            with os.scandir(sub) as entries:
                for entry in entries:
                    sha = entry.name.partition('.')[0]
                    if len(sha) == 64 and all(c in '0123456789abcdef' for c in sha):
                        present.add(sha)
    return present

def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(data).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)

@contextlib.contextmanager
def locked(run):
    Path(run).mkdir(parents=True, exist_ok=True)
    with (Path(run) / '.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield

def safe_file(base, relative):
    base = Path(base).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError('path escapes dataset')
    return path

def require(ok, message):
    if not ok:
        raise ValueError(message)

def nonempty(value):
    return isinstance(value, str) and bool(value.strip())

def strings(value, allow_empty=True):
    return isinstance(value, list) and (allow_empty or bool(value)) and all(nonempty(x) for x in value) and len(value) == len(set(value))

def validate_docs(task, result):
    require(isinstance(result, dict), 'result must be object')
    sources = {s['source_id']: s for s in task['input']['sources']}
    decisions = result.get('source_decisions')
    require(isinstance(decisions, list), 'source_decisions required')
    require(all(isinstance(x, dict) for x in decisions), 'invalid source decision')
    require(len(decisions) == len(sources) and {x.get('source_id') for x in decisions} == set(sources), 'every source needs exactly one decision')
    for x in decisions:
        require(x.get('relation') in ('same_concept', 'related', 'unrelated', 'uncertain') and nonempty(x.get('reason')), 'invalid source relation')
    allowed = {x['source_id'] for x in decisions if x['relation'] == 'same_concept'}
    facts = result.get('facts')
    require(isinstance(facts, list) and len(facts) <= 3, 'facts must have 0..3 entries')
    require(strings(result.get('gaps')), 'gaps required')
    seen = set()
    for fact in facts:
        require(isinstance(fact, dict), 'fact must be object')
        for k in ('statement', 'visual_consequence', 'core_reason'):
            require(nonempty(fact.get(k)), 'missing ' + k)
        require(fact['statement'] not in seen, 'duplicate statement')
        seen.add(fact['statement'])
        for k in ('conditions', 'related_concepts', 'content_types', 'knowledge_domains'):
            require(strings(fact.get(k), allow_empty=k == 'conditions'), 'invalid ' + k)
        require(set(fact['content_types']) <= set(CONTENT_TYPES), 'unknown content type')
        require(set(fact['knowledge_domains']) <= set(task['input']['domains']), 'unknown domain')
        require(type(fact.get('visualizable')) is bool, 'visualizable must be bool')
        citations = fact.get('citations')
        require(isinstance(citations, list) and citations, 'citations required')
        for c in citations:
            require(isinstance(c, dict) and c.get('source_id') in allowed, 'citation not from same_concept source')
            require(nonempty(c.get('quote')) and len(c['quote'].strip()) >= 12, 'quote too short')
            source_text=sources[c['source_id']]['text']
            if c['quote'] not in source_text:
                match=difflib.SequenceMatcher(None,c['quote'],source_text,autojunk=False).find_longest_match()
                start=max(0,match.b-match.a-60)
                nearby=source_text[start:start+min(len(c['quote'])+150,1200)]
                raise ValueError('quote is not an exact source substring; source_id='+c['source_id']+'; submitted='+c['quote'][:500]+'; nearby ORIGINAL (context only, not automatic correction)='+nearby)
    return result

def validate_evidence(task, result):
    require(isinstance(result, dict), 'result must be object')
    require(nonempty(result.get('representation')) and nonempty(result.get('reason')), 'representation/reason required')
    require(result.get('evidence_status') in ('supports', 'conflicts', 'indeterminate', 'not_applicable'), 'invalid evidence status')
    require(result.get('condition_status') in ('matched', 'mismatched', 'unknown', 'not_applicable'), 'invalid condition status')
    require(result.get('coverage') in ('full', 'partial', 'none'), 'invalid coverage')
    obs = result.get('observations')
    require(isinstance(obs, list) and all(isinstance(x, dict) and nonempty(x.get('anchor')) and nonempty(x.get('visible')) for x in obs), 'invalid observations')
    if result['evidence_status'] == 'supports':
        require(bool(obs) and result['coverage'] != 'none', 'support needs visible evidence')
    modern = task.get('input', {}).get('evidence_protocol') == 3
    if modern:
        require(strings(result.get('unsupported_aspects')), 'unsupported_aspects required')
        ids = [o.get('id') for o in obs]
        require(strings(ids), 'observations need unique ids')
        if result['coverage'] == 'partial':
            require(bool(result['unsupported_aspects']), 'partial coverage needs explicit gaps')
        if result['coverage'] == 'full':
            require(not result['unsupported_aspects'], 'full coverage cannot have unsupported aspects')
        require(not (result['evidence_status'] == 'conflicts' and result['condition_status'] == 'mismatched'), 'different applicability is not a factual contradiction')
    for branch in ('t2i', 'edit'):
        b = result.get(branch)
        require(isinstance(b, dict) and b.get('status') in ('usable', 'needs_more', 'unusable') and nonempty(b.get('reason')), 'invalid ' + branch)
        if b['status'] == 'usable':
            if not modern or branch == 't2i':
                require(result['evidence_status'] == 'supports' and result['condition_status'] in ('matched', 'not_applicable'), 'usable requires supported evidence and known conditions')
            if modern and branch == 't2i':
                require(result['coverage'] == 'full', 'partial evidence cannot qualify the full T2I target')
            keys = ('target',) if branch == 't2i' else ('initial_state', 'instruction', 'expected_change')
            for k in keys:
                require(nonempty(b.get(k)), 'usable task missing ' + k)
            require(strings(b.get('checks' if branch == 't2i' else 'preserve'), False), 'usable task needs checks/preservation')
        if modern and branch == 'edit':
            require(b.get('source_state') in ('clear', 'uncertain') and b.get('source_conditions') in ('sufficient', 'uncertain'), 'edit source state/conditions required')
            require(strings(b.get('source_anchors')) and set(b['source_anchors']) <= set(ids), 'edit anchors must reference visible observations')
            if b['status'] == 'usable':
                require(b['source_state'] == 'clear' and b['source_conditions'] == 'sufficient' and bool(b['source_anchors']), 'usable edit needs a clear, grounded source state')
                require(nonempty(b.get('knowledge_dependency')), 'usable edit needs knowledge dependency')
    return result

def task_record(stage, payload, prompt):
    body = {'stage': stage, 'input': payload, 'prompt': prompt, 'schema_version': 1}
    return {**body, 'task_id': stage + '_' + digest(body)[:24], 'input_sha256': digest(body)}

def rules_hash():
    return digest({p.name: digest(p.read_bytes()) for p in [Path(__file__), Path(__file__).with_name('pipeline.py'), Path(__file__).with_name('prompts.py')]})

def require_frozen(run):
    frozen = read(Path(run) / 'frozen_rules.json')
    allowed = {rules_hash()}
    # Accept the original freeze only for the verified, relocation-only snapshot.
    archive = ROOT / 'state/curation/archive_reorganization_v1/manifest.json'
    if archive.exists():
        records = {Path(r['old']).name: r for r in read(archive)['files']
                   if r['old'] in ('curation/core.py', 'curation/pipeline.py', 'curation/prompts.py')}
        if len(records) == 3 and all(digest((ROOT/r['new']).read_bytes()) == r['after_sha256'] for r in records.values()):
            allowed.add(digest({name:r['before_sha256'] for name,r in records.items()}))
    require(frozen['rules_sha256'] in allowed, 'rules changed after freeze; use a fresh holdout/run')
    require(frozen['manifest_sha256'] == digest(read(Path(run)/'manifest.json')), 'manifest changed after freeze')

def tasks(run, stage):
    directory = Path(run) / 'tasks' / stage
    values = [read(p) for p in sorted(directory.glob('*.json'))]
    for t in values:
        verify_task(t)
    return values

def verify_task(task):
    expected = task_record(task['stage'], task['input'], task['prompt'])
    require(task == expected, 'task content/hash changed; use a new run')

def accepted_results(run, stage):
    for t in tasks(run, stage):
        p = Path(run) / 'results' / stage / (t['task_id'] + '.json')
        if p.exists():
            r = read(p)
            require(r['input_sha256'] == t['input_sha256'], 'stale result')
            yield t, r

def ingest(run, stage, envelope):
    envelope = normalize_transport(stage, envelope)
    tid = envelope.get('task_id', '')
    require(isinstance(tid, str) and tid.startswith(stage + '_') and tid.replace('_', '').isalnum(), 'invalid task id')
    with locked(run):
        t = read(Path(run) / 'tasks' / stage / (tid + '.json'))
        verify_task(t)
        if t['input']['split'] == 'holdout':
            require_frozen(run)
        require(envelope.get('input_sha256') == t['input_sha256'], 'stale input hash')
        require(nonempty(envelope.get('model')), 'model provenance required')
        validator = validate_docs if stage == 'docs' else validate_evidence
        validator(t, envelope.get('result'))
        dest = Path(run) / 'results' / stage / (tid + '.json')
        if dest.exists():
            require(read(dest) == envelope, 'result is immutable; use a new run for revisions')
        else:
            write(dest, envelope)
    return tid

def normalize_transport(stage, envelope):
    """Repair only an unambiguous misplaced branch; preserve original model output."""
    result = envelope.get('result')
    if stage != 'evidence' or not isinstance(result, dict) or not isinstance(result.get('t2i'), dict):
        return envelope
    if 'edit' not in result['t2i']:
        return envelope
    require('edit' not in result, 'ambiguous duplicate edit branches')
    value = copy.deepcopy(envelope)
    value['transport_original_result'] = copy.deepcopy(result)
    value['transport_normalizations'] = ['promote t2i.edit to result.edit; no content rewritten']
    value['result']['edit'] = value['result']['t2i'].pop('edit')
    return value

def facts(run):
    for task, envelope in accepted_results(run, 'docs'):
        for index, fact in enumerate(envelope['result']['facts']):
            yield {'fact_id': task['task_id'] + '_f' + str(index + 1), 'fact': fact,
                   'concept': task['input']['concept'], 'split': task['input']['split'],
                   'sources': task['input']['sources'], 'docs_task_id': task['task_id']}

def review(run, kind, record_id, decision, reviewer, notes='', t2i='unreviewed', edit='unreviewed', corrected_result=None):
    require(kind in ('fact', 'evidence'), 'invalid review kind')
    require(decision in ('accept', 'reject', 'uncertain'), 'invalid decision')
    require(nonempty(reviewer) and nonempty(notes), 'reviewer and rationale required')
    require(corrected_result is None or kind == 'evidence', 'only evidence corrections are supported; changing a fact requires a new run')
    with locked(run):
        if kind == 'fact':
            records = {r['fact_id']: r for r in facts(run)}
            require(record_id in records, 'unknown fact')
            target = records[record_id]
        else:
            records = {t['task_id']: {'task': t, 'result': r['result']} for t, r in accepted_results(run, 'evidence')}
            require(record_id in records, 'unknown evidence')
            target = records[record_id]
            if corrected_result is not None:
                validate_evidence(target['task'], corrected_result)
            for status in (t2i, edit):
                require(status in ('usable', 'needs_more', 'unusable', 'unreviewed'), 'invalid human task status')
            if decision == 'accept':
                result = corrected_result if corrected_result is not None else target['result']
                if target['task']['input'].get('evidence_protocol') == 3:
                    validate_evidence(target['task'], result)
                else:
                    require(result['evidence_status'] == 'supports' and result['condition_status'] in ('matched', 'not_applicable'), 'revise conflicting/unknown machine record in a new run before acceptance')
                require(t2i != 'unreviewed' and edit != 'unreviewed', 'review both task branches')
                for branch, status in [('t2i', t2i), ('edit', edit)]:
                    require(status != 'usable' or result[branch]['status'] == 'usable', 'usable needs a complete candidate task')
        value = {'id': record_id, 'kind': kind, 'decision': decision, 'reviewer': reviewer,
                 'notes': notes, 'record_sha256': digest(target), 't2i': t2i, 'edit': edit}
        if corrected_result is not None:
            value['corrected_result'] = corrected_result
        write(Path(run) / 'reviews' / kind / (record_id + '.json'), value)

def reviewed(run, kind, record_id, target):
    p = Path(run) / 'reviews' / kind / (record_id + '.json')
    if not p.exists():
        return None
    r = read(p)
    require(r['record_sha256'] == digest(target), 'stale human review')
    return r

def core_records(run):
    by_fact = {f['fact_id']: f for f in facts(run)}
    for task, envelope in accepted_results(run, 'evidence'):
        f = by_fact[task['input']['fact_id']]
        fr = reviewed(run, 'fact', f['fact_id'], f)
        er = reviewed(run, 'evidence', task['task_id'], {'task': task, 'result': envelope['result']})
        if not fr or not er or fr['decision'] != 'accept' or er['decision'] != 'accept':
            continue
        for branch in ('t2i', 'edit'):
            if er[branch] == 'usable':
                yield {'evidence_id': task['task_id'], 'task': branch, 'knowledge': f,
                       'image': task['input']['image'], 'evidence': er.get('corrected_result', envelope['result']),
                       'image_origin': image_origin(task['input']['image']),
                       'fact_review': fr, 'evidence_review': er}

def image_origin(image):
    """A web source does not prove a photograph is authentic; leave it unverified."""
    source = str(image.get('source') or '').lower()
    if any(token in source for token in ('qwen-image', 'gpt-image', 'dall-e', 'midjourney', 'stable-diffusion')):
        return 'declared_generated'
    return 'unverified_origin'
