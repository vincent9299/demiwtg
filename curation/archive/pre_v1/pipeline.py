#!/usr/bin/env python3
"""Knowledge core pilot. Run python3 -m curation.pipeline --help from repo root.

prepare -> render docs -> run/ingest docs -> render evidence -> run/ingest evidence
-> notebook/review -> report/export. All products live in state/curation, never meta.
"""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import argparse
import base64
import collections
import hashlib
import io
import json
import os
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from curation.core import (ROOT, DEFAULT_RUN, read, write, digest, locked, require,
    safe_file, tasks, accepted_results, task_record, ingest, facts, review, core_records, reviewed, rules_hash, require_frozen, blob_shas, image_origin, normalize_transport, validate_evidence, validate_docs)
from curation.prompts import CONTENT_TYPES, DOC_PROMPT, EVIDENCE_PROMPT


def jsonlines(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def domain_map(tree):
    mapping = collections.defaultdict(list)
    def walk(node, domain):
        for name in node.get('instances', []):
            if domain not in mapping[name]:
                mapping[name].append(domain)
        for child in node.get('children', []):
            walk(child, domain)
    for node in tree['children']:
        walk(node, node['name'])
    return dict(mapping), [n['name'] for n in tree['children']]


def old_stratum(row):
    if row.get('quality') is None or row.get('identity') is None:
        return 'unannotated'
    return 'old_pass' if row['identity'] is True and row['quality'] >= 8 else 'old_reject'


def source_rank(source, concept):
    title = source.get('title', '').casefold()
    names = [concept['name'], *concept.get('aliases', [])]
    title_match = any(len(n) > 1 and n.casefold() in title for n in names)
    return (not title_match, source.get('authority') != 'wiki', -min(len(source.get('text', '')), 20000), source['page_sha'])


def prepare(args):
    run = args.run
    require(args.concepts > 0 and 0 < args.holdout < 1, 'positive concepts and 0<holdout<1 required')
    require(args.images_per_stratum > 0 and args.pages > 0, 'positive image/page limits required')
    with locked(run):
        require(not (run / 'manifest.json').exists(), 'run already prepared; choose a new --run')
        base = args.dataset.resolve()
        all_concepts = {c['name']: c for c in read(base / 'meta/concepts.json')['concepts']}
        mapping, domains = domain_map(read(base / 'meta/taxonomy.json')['tree'])
        pages = collections.defaultdict(list)
        seen_pages = collections.defaultdict(set)
        for page in jsonlines(args.clean_pages):
            text = page.get('text', '')
            if len(text.strip()) < 100:
                continue
            text_sha = digest(' '.join(text.split()).casefold())
            for name in page.get('concepts', []):
                if name in all_concepts and text_sha not in seen_pages[name]:
                    pages[name].append(page)
                    seen_pages[name].add(text_sha)
        print(f'资料候选 {len(pages)} 个概念；扫描实存图片目录', flush=True)
        present = blob_shas(base / 'blobs')
        pools = collections.defaultdict(dict)
        totals = collections.Counter()
        for index, row in enumerate(jsonlines(base / 'meta/images.jsonl'), 1):
            if row.get('sha256') not in present:
                continue
            for name in set(row.get('instances') or []):
                if name not in pages:
                    continue
                group = old_stratum(row)
                totals[(name, group)] += 1
                key = (name, group)
                sha = row['sha256']
                bucket = pools[key]
                rank = digest([args.seed, name, sha])
                if sha in bucket or (len(bucket) >= args.images_per_stratum and rank >= max(v[0] for v in bucket.values())):
                    continue
                fields = ('sha256', 'path', 'ext', 'source', 'content_url', 'landing_url', 'caption', 'quality', 'identity', 'kb_match', 'width', 'height')
                rec = {k: row.get(k) for k in fields}
                rec['path'] = row.get('path') or row.get('blob_path') or f"blobs/{sha[:2]}/{sha}.{row.get('ext', 'jpg')}"
                rec['old_stratum'] = group
                # Listing is cheap; only selected candidates incur stat/decode later.
                safe_file(base, rec['path'])
                bucket[sha] = (rank, rec)
                if len(bucket) > args.images_per_stratum:
                    del bucket[max(bucket, key=lambda s: bucket[s][0])]
            if index % 1000000 == 0:
                print(f'已扫描清单 {index} 行', flush=True)
        available = {name for name, group in pools if pools[(name, group)]}
        selected, covered = [], collections.Counter()
        # Round-robin domain quotas, tie broken by a stable hash, not old quality.
        while len(selected) < min(args.concepts, len(available)):
            remaining = available - set(selected)
            target = min((d for d in domains if any(d in mapping.get(n, []) for n in remaining)), key=lambda d: (covered[d], domains.index(d)), default=None)
            candidates = [n for n in remaining if target in mapping.get(n, [])] if target else list(remaining)
            name = min(candidates, key=lambda n: digest([args.seed, n]))
            selected.append(name)
            for d in mapping.get(name, []):
                covered[d] += 1
        order = sorted(selected, key=lambda n: digest(['split', args.seed, n]))
        holdout = set(order[:max(1, round(len(order) * args.holdout))]) if len(order) > 1 else set()
        benchmark_names = set()
        for task in ('t2i', 'edit'):
            f = ROOT / f'benchmark/{task}/bench200/questions.jsonl'
            if f.exists():
                for q in jsonlines(f):
                    benchmark_names.add(q.get('instance') or q.get('_instance'))
        records = []
        for name in selected:
            image_list = [v[1] for group in ('old_pass', 'old_reject', 'unannotated') for v in sorted(pools.get((name, group), {}).values(), key=lambda v: v[0])]
            sources = []
            for page in sorted(pages[name], key=lambda p: source_rank(p, all_concepts[name]))[:args.pages]:
                text = page['text'][:args.source_chars]
                sources.append({'source_id': page['page_sha'], 'url': page['url'], 'title': page.get('title', ''),
                    'text': text, 'text_sha256': digest(text), 'truncated': len(text) < len(page['text']),
                    'full_text_sha256': digest(page['text']), 'authority_hint': page.get('authority'),
                    'selection_note': 'title/wiki priority is a retrieval heuristic, not verified relevance or authority'})
            c = all_concepts[name]
            records.append({'name': name, 'aliases': c.get('aliases', []), 'domains': mapping.get(name, []),
                'split': 'holdout' if name in holdout else 'calibration', 'benchmark_overlap': name in benchmark_names,
                'sources': sources, 'images': image_list,
                'pool_rows': {g: totals[(name, g)] for g in ('old_pass', 'old_reject', 'unannotated')}})
        gaps = [{'domain': d, 'with_clean_docs': sum(d in mapping.get(n, []) for n in pages),
                 'with_docs_and_images': sum(d in mapping.get(n, []) for n in available), 'selected': covered[d]} for d in domains]
        manifest = {'schema_version': 1, 'dataset': str(base), 'seed': args.seed,
            'domains': domains, 'content_types': CONTENT_TYPES, 'concepts': records, 'domain_supply': gaps,
            'inputs': {str(f): {'size': f.stat().st_size, 'mtime_ns': f.stat().st_mtime_ns} for f in [base/'meta/images.jsonl', base/'meta/concepts.json', base/'meta/taxonomy.json', args.clean_pages]},
            'limits': {'pages_per_concept': args.pages, 'source_chars': args.source_chars, 'images_per_stratum': args.images_per_stratum},
            'split_scope': 'concept-disjoint curation holdout only; not proof of unseen benchmark concepts',
            'status': 'unverified_candidates'}
        write(run / 'manifest.json', manifest)
        print(json.dumps({'selected': len(records), 'images': sum(len(c['images']) for c in records), 'domains': sum(x['selected'] > 0 for x in gaps), 'holdout': len(holdout)}, ensure_ascii=False))


def render(args):
    with locked(args.run):
        m = read(args.run / 'manifest.json')
        prompt = DOC_PROMPT if args.stage == 'docs' else EVIDENCE_PROMPT
        require(all(t['prompt'] == prompt for t in tasks(args.run, args.stage)), 'prompt changed; create a new run rather than mixing protocols')
        require(args.per_stratum > 0, 'positive per-stratum limit required')
        records = []
        if args.stage == 'docs':
            for c in m['concepts']:
                records.append(task_record('docs', {'concept': c['name'], 'aliases': c['aliases'], 'concept_domains': c['domains'], 'domains': m['domains'], 'split': c['split'], 'sources': c['sources']}, DOC_PROMPT))
        else:
            concepts = {c['name']: c for c in m['concepts']}
            for f in facts(args.run):
                c = concepts[f['concept']]
                for group in ('old_pass', 'old_reject', 'unannotated'):
                    candidates = [i for i in c['images'] if i['old_stratum'] == group]
                    # A small frozen sample per stratum; no model score is used to cherry-pick.
                    for im in candidates[:args.per_stratum]:
                        image_path = safe_file(m['dataset'], im['path'])
                        try:
                            raw = image_path.read_bytes()
                            require(hashlib.sha256(raw).hexdigest() == im['sha256'], 'image hash mismatch')
                        except (OSError, ValueError) as e:
                            print(f"跳过不可用图片 {im['sha256'][:12]}: {e}", flush=True)
                            continue
                        records.append(task_record('evidence', {'evidence_protocol': 3, 'fact_id': f['fact_id'], 'concept': f['concept'], 'fact': f['fact'], 'split': f['split'], 'image': im}, EVIDENCE_PROMPT))
        for task in records:
            write(args.run / 'tasks' / args.stage / (task['task_id'] + '.json'), task)
        print(f'{args.stage}: {len(records)} tasks materialized')


def call_model(task, args, repair=None):
    validate_local_endpoint(args.base_url, args.model)
    # Strip old scores/captions from the model view; provenance remains in the immutable task.
    payload = dict(task['input'])
    content = []
    if task['stage'] == 'evidence':
        im = payload.pop('image')
        m = read(args.run / 'manifest.json')
        raw = safe_file(m['dataset'], im['path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == im['sha256'], 'image bytes changed')
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((args.max_edge, args.max_edge))
            buf = io.BytesIO(); image.save(buf, format='JPEG', quality=92)
            encoded = base64.b64encode(buf.getvalue()).decode()
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded}})
    content.append({'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)})
    body = {'model': args.model, 'temperature': 0, 'max_tokens': args.max_tokens,
        'messages': [{'role': 'system', 'content': task['prompt']}, {'role': 'user', 'content': content}],
        'response_format': {'type': 'json_object'}}
    if repair is not None:
        body['messages'].extend([
            {'role':'assistant','content':json.dumps(repair['previous_result'],ensure_ascii=False)},
            {'role':'user','content':'上次输出未通过协议校验：'+repair['validation_error']+'。重新核对原文'+('和图片' if task['stage']=='evidence' else '')+'，独立核验，再输出完整JSON。不要仅为过校验改标签；证据不足应明确待补充。'}])
    if 'qwen' in args.model.lower() and '/' not in args.model:
        body['chat_template_kwargs'] = {'enable_thinking': False}
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('CURATION_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['CURATION_API_KEY']
    req = urllib.request.Request(args.base_url.rstrip('/') + '/chat/completions', json.dumps(body).encode(), headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('model endpoint redirects are disabled')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=args.timeout) as r:
        response = json.load(r)
    choice = response['choices'][0]
    require(choice.get('finish_reason') != 'length', 'truncated model output')
    text = choice['message']['content'].strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    envelope = {'task_id': task['task_id'], 'input_sha256': task['input_sha256'], 'model': args.model,
        'result': json.loads(text), 'inference': {'endpoint': args.base_url, 'max_edge': args.max_edge, 'max_tokens': args.max_tokens, 'temperature': 0},
        'usage': response.get('usage', {})}
    if repair is not None:
        envelope['repair_context'] = repair
    return envelope


def run_model(args):
    validate_local_endpoint(args.base_url, args.model)
    require(args.limit > 0, 'explicit positive call limit required')
    if args.split == 'holdout':
        require_frozen(args.run)
    comparison_keys = None
    if args.compare_with:
        require(args.stage == 'evidence', 'compare-with is for evidence only')
        comparison_keys = {(t['input']['fact_id'], t['input']['image']['sha256']) for t,r in accepted_results(args.compare_with,'evidence')}
    selected = [t for t in tasks(args.run, args.stage) if t['input']['split'] == args.split
                and (not args.concept or t['input']['concept'] in args.concept)
                and (comparison_keys is None or (t['input']['fact_id'],t['input']['image']['sha256']) in comparison_keys)
                and (not args.repair_invalid or (args.run/'raw'/args.stage/(t['task_id']+'.json')).exists())
                and not (args.run / 'results' / args.stage / (t['task_id'] + '.json')).exists()]
    if args.one_per_fact:
        require(args.stage == 'evidence', 'one-per-fact is for evidence only')
        seen=set(); unique=[]
        for t in selected:
            fid=t['input']['fact_id']
            if fid not in seen:
                unique.append(t);seen.add(fid)
        selected=unique
    selected=selected[:args.limit]
    failures = 0
    for i, t in enumerate(selected, 1):
        try:
            repair = None
            raw_path=args.run/'raw'/args.stage/(t['task_id']+'.json')
            if args.repair_invalid and raw_path.exists():
                previous=read(raw_path)
                validator=validate_evidence if args.stage=='evidence' else validate_docs
                try:validator(t,normalize_transport(args.stage,previous)['result'])
                except (ValueError,KeyError,TypeError) as e:
                    repair={'previous_result':previous['result'],'previous_envelope_sha256':digest(previous),'validation_error':str(e)}
                write(args.run/'attempts'/args.stage/(t['task_id']+'_'+digest(previous)[:16]+'.json'),previous)
            result = call_model(t, args, repair)
            write(args.run / 'raw' / args.stage / (t['task_id'] + '.json'), result)
            ingest(args.run, args.stage, result)
            print(f'{i}/{len(selected)} accepted {t["task_id"]}', flush=True)
        except Exception as e:
            failures += 1
            print(f'{i}/{len(selected)} failed {t["task_id"]}: {type(e).__name__}: {str(e)[:200]}', flush=True)
    if failures:
        raise SystemExit(1)


def validate_local_endpoint(base_url, model):
    """User scope: direct local Qwen only. Paid gateways require a new explicit decision."""
    url = urlparse(base_url)
    require(url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')
            and url.port in (8000, 8001) and url.path.rstrip('/') == '/v1'
            and not url.username and not url.password and not url.query and not url.fragment,
            'only direct local Qwen on 8000/8001 is authorized; paid gateways (including 4001) are blocked')
    require(model.lower() == 'qwen3.8-27b', 'only local qwen3.8-27b is authorized')


def report(run):
    m = read(run / 'manifest.json')
    f = list(facts(run)); ev = list(accepted_results(run, 'evidence')); exported = list(core_records(run))
    matrix = []
    for d in m['domains']:
        for kind in CONTENT_TYPES:
            ff = [r for r in f if d in r['fact']['knowledge_domains'] and kind in r['fact']['content_types']]
            ids = {r['fact_id'] for r in ff}
            matrix.append({'domain': d, 'content_type': kind, 'candidate_facts': len(ids),
                'evidence_records': sum(t['input']['fact_id'] in ids for t, r in ev),
                'core_t2i': len({r['knowledge']['fact_id'] for r in exported if r['task'] == 't2i' and r['knowledge']['fact_id'] in ids}),
                'core_edit': len({r['knowledge']['fact_id'] for r in exported if r['task'] == 'edit' and r['knowledge']['fact_id'] in ids})})
    strata = collections.defaultdict(collections.Counter)
    for t, envelope in ev:
        key = t['input']['split'] + '/' + t['input']['image']['old_stratum']
        strata[key]['model_' + envelope['result']['evidence_status']] += 1
        human = reviewed(run, 'evidence', t['task_id'], {'task': t, 'result': envelope['result']})
        if human:
            strata[key]['human_' + human['decision']] += 1
    material_gaps=[]
    for fact in f:
        inspected=[r['result'] for t,r in ev if t['input']['fact_id']==fact['fact_id']]
        if any(r[b]['status']=='usable' for r in inspected for b in ('t2i','edit')):
            continue
        missing=list(dict.fromkeys(x for r in inspected for x in r.get('unsupported_aspects',[])))
        material_gaps.append({'fact_id':fact['fact_id'],'concept':fact['concept'],
            'statement':fact['fact']['statement'],'visual_target':fact['fact']['visual_consequence'],
            'inspected_image_count':len(inspected),'missing_aspects':missing,
            'status':'needs_human_fact_and_evidence_check' if inspected else 'image_check_pending',
            'note':'Candidate gap only; verify fact and model rejections before collecting more images.'})
    result = {'concepts': len(m['concepts']), 'domain_supply': m['domain_supply'],
        'split_scope': m['split_scope'], 'benchmark_overlap': sum(c['benchmark_overlap'] for c in m['concepts']),
        'tasks': {s: {'rendered': len(tasks(run, s)), 'validated_results': sum(1 for _ in accepted_results(run, s))} for s in ('docs', 'evidence')},
        'candidate_facts': len(f), 'core_task_records': len(exported),
        'human_reviews': {k: len(list((run/'reviews'/k).glob('*.json'))) for k in ('fact', 'evidence')},
        'matrix': matrix, 'strata_counts': {k:dict(v) for k,v in strata.items()},
        'image_origin_counts': dict(collections.Counter(image_origin(t['input']['image']) for t,r in ev)),
        'assistant_review_hints': read(run/'review_hints.json') if (run/'review_hints.json').exists() else {},
        'comparison_summary': read(run/'comparison.json')['summary'] if (run/'comparison.json').exists() else None,
        'material_gaps':material_gaps,
        'gap_counts':dict(collections.Counter(g['status'] for g in material_gaps)),
        'interpretation': 'candidate counts are not correctness estimates; source relevance and truth need human review; multi-label/domain counts are non-additive'}
    write(run/'report.json', result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('matrix','domain_supply','material_gaps')}, ensure_ascii=False, indent=2))


def compare(run, previous):
    old = {(t['input']['fact_id'],t['input']['image']['sha256']):(t,r) for t,r in accepted_results(previous,'evidence')}
    pairs=[]
    for task in tasks(run,'evidence'):
        key=(task['input']['fact_id'],task['input']['image']['sha256'])
        if key not in old: continue
        before_task,before=old[key]
        require(task['input']['fact']==before_task['input']['fact'],'comparison fact changed')
        path=run/'results/evidence'/(task['task_id']+'.json')
        raw=run/'raw/evidence'/(task['task_id']+'.json')
        after=read(path) if path.exists() else read(raw) if raw.exists() else None
        error=None
        if after is not None:
            try: validate_evidence(task,normalize_transport('evidence',after)['result'])
            except (ValueError,KeyError,TypeError) as e: error=str(e)
        pairs.append({'concept':task['input']['concept'],'fact_id':key[0],'image_sha256':key[1],
            'before_id':before_task['task_id'],'after_id':task['task_id'],
            'before':before['result'],'after':after['result'] if after else None,
            'after_validated':path.exists() and error is None,'validation_error':error})
    matched=[p for p in pairs if p['after_validated']]
    summary={'matched_pairs':len(pairs),'completed_outputs':sum(p['after'] is not None for p in pairs),
        'validated_pairs':len(matched),'invalid_outputs':sum(p['validation_error'] is not None for p in pairs),
        'evidence_transitions':dict(collections.Counter(p['before']['evidence_status']+' -> '+p['after']['evidence_status'] for p in matched)),
        'task_transitions':{b:dict(collections.Counter(p['before'][b]['status']+' -> '+p['after'][b]['status'] for p in matched)) for b in ['t2i','edit']},
        'interpretation':'Same facts/images, changed protocol; transitions are not accuracy gains without human labels.'}
    write(run/'comparison.json',{'previous_run':str(previous),'summary':summary,'pairs':pairs})
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, default=DEFAULT_RUN)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare'); a.add_argument('--dataset', type=Path, default=ROOT/'datasets/demiwtg'); a.add_argument('--clean-pages', type=Path, default=ROOT/'state/collect/docs_clean/pages_clean.jsonl')
    a.add_argument('--concepts', type=int, default=50); a.add_argument('--pages', type=int, default=6); a.add_argument('--source-chars', type=int, default=12000); a.add_argument('--images-per-stratum', type=int, default=3); a.add_argument('--seed', type=int, default=20260909); a.add_argument('--holdout', type=float, default=.24)
    a = sub.add_parser('render'); a.add_argument('stage', choices=['docs','evidence']); a.add_argument('--per-stratum', type=int, default=1)
    a = sub.add_parser('run'); a.add_argument('stage', choices=['docs','evidence']); a.add_argument('--base-url', default=os.environ.get('CURATION_BASE_URL', 'http://127.0.0.1:8000/v1')); a.add_argument('--model', default='qwen3.8-27b'); a.add_argument('--limit', type=int, required=True); a.add_argument('--split', choices=['calibration','holdout'], default='calibration'); a.add_argument('--max-edge', type=int, default=1536); a.add_argument('--max-tokens', type=int, default=6000); a.add_argument('--timeout', type=int, default=180)
    a.add_argument('--concept', action='append', help='Limit to named concepts; repeat for multiple concepts')
    a.add_argument('--compare-with', type=Path, help='Run only fact/image pairs with prior evidence results')
    a.add_argument('--repair-invalid',action='store_true',help='One explicit retry using previous invalid output and validation feedback; preserve the original')
    a.add_argument('--one-per-fact',action='store_true',help='Small pilot: one pending image for each distinct fact in this call')
    a = sub.add_parser('ingest'); a.add_argument('stage', choices=['docs','evidence']); a.add_argument('file', type=Path)
    a = sub.add_parser('review'); a.add_argument('kind', choices=['fact','evidence']); a.add_argument('id'); a.add_argument('decision', choices=['accept','reject','uncertain']); a.add_argument('--reviewer', required=True); a.add_argument('--notes', required=True); a.add_argument('--t2i', default='unreviewed'); a.add_argument('--edit', default='unreviewed')
    a.add_argument('--correction',type=Path)
    sub.add_parser('report'); sub.add_parser('export'); sub.add_parser('freeze')
    a = sub.add_parser('fork'); a.add_argument('destination', type=Path); a.add_argument('--reuse-docs', action='store_true')
    a = sub.add_parser('compare'); a.add_argument('previous',type=Path)
    args = p.parse_args(); args.run = args.run.resolve()
    if args.command == 'prepare': prepare(args)
    elif args.command == 'render': render(args)
    elif args.command == 'run': run_model(args)
    elif args.command == 'ingest': print(ingest(args.run,args.stage,read(args.file)))
    elif args.command == 'review': review(args.run,args.kind,args.id,args.decision,args.reviewer,args.notes,args.t2i,args.edit,read(args.correction) if args.correction else None)
    elif args.command == 'compare': compare(args.run,args.previous.resolve())
    elif args.command == 'report': report(args.run)
    elif args.command == 'fork':
        destination = args.destination.resolve()
        with locked(destination):
            require(not (destination/'manifest.json').exists(), 'destination already exists')
            manifest = read(args.run/'manifest.json')
            manifest['parent_run'] = str(args.run)
            write(destination/'manifest.json', manifest)
        if args.reuse_docs:
            for task,envelope in accepted_results(args.run,'docs'):
                require(task['prompt'] == DOC_PROMPT, 'cannot reuse docs across changed document prompts')
                require(task['input']['split'] == 'calibration', 'holdout reuse is forbidden during calibration')
                write(destination/'tasks/docs'/(task['task_id']+'.json'), task)
                ingest(destination,'docs',envelope)
        print('Frozen inputs reused; human reviews not copied; docs reused:', args.reuse_docs, destination)
    elif args.command == 'freeze':
        with locked(args.run):
            require(not (args.run / 'frozen_rules.json').exists(), 'already frozen; do not overwrite')
            require(any((args.run / 'reviews' / 'fact').glob('*.json')) and any((args.run / 'reviews' / 'evidence').glob('*.json')), 'calibrate with human fact and evidence reviews before freezing')
            write(args.run / 'frozen_rules.json', {'rules_sha256': rules_hash(), 'manifest_sha256': digest(read(args.run/'manifest.json'))})
    elif args.command == 'export':
        with locked(args.run):
            write(args.run/'core.json', {'schema_version':1, 'records':list(core_records(args.run)), 'status':'human_reviewed_only'})
            report(args.run)

if __name__ == '__main__': main()
