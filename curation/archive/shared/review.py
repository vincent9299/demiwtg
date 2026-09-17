"""Render complete evidence cards and validate assistant output reviews."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
import base64
from collections import Counter, defaultdict
import html
import json
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ROOT, STATE, read
from bagel_runner import encoded, publish, sha


def esc(x):
    return html.escape(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, indent=2))


def picture(path, label):
    p = Path(path)
    if not p.exists():
        return '<p>Missing image: ' + esc(path) + '</p>'
    mime = {'.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(p.suffix.lower(), 'image/png')
    return '<figure><img loading="lazy" src="data:' + mime + ';base64,' + base64.b64encode(p.read_bytes()).decode() + '"><figcaption>' + esc(label) + '</figcaption></figure>'


def render(args):
    run = Path(args.run)
    cases_path = run / 'cases.json'
    cases = read(cases_path)['cases'] if cases_path.exists() else []
    domains = read(STATE / 'domain_cases.json')
    blocks = ['<!doctype html><meta charset="utf-8"><title>图文知识获取与应用：开发验证</title>',
              '<style>body{max-width:1200px;margin:32px auto;padding:0 20px;font:16px/1.6 sans-serif}article{border-top:2px solid #aaa;margin:32px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f5;padding:12px}img{max-width:100%;max-height:540px}figure{display:inline-block;max-width:46%;vertical-align:top;margin:10px}figcaption{overflow-wrap:anywhere}summary{cursor:pointer;font-weight:bold}</style>',
              '<h1>图文知识获取与应用：开发验证</h1><p>独立新版本；仅开发校准，未编制正式200题。审核由助手完成，不是人工金标准。原始题面、资料与判断分别展示。</p>']
    pool = STATE / 'material_pool.json'
    if pool.exists():
        blocks.append('<h2>扩量材料池</h2><p>候选槽与文件存在检查，不等于合格知识或可用题目。</p><pre>' + esc(read(pool).get('summary', {})) + '</pre>')
    score_path = run / 'scores_summary.json'
    scores = read(score_path) if score_path.exists() else {}
    output_scores = {(x['model'], x['job_id']): x for x in scores.get('per_output', [])}
    if scores:
        blocks.append('<h2>开发结果</h2><table border="1" cellpadding="8"><tr><th>模型／资料条件</th><th>知识全通过</th><th>全部可观察</th><th>执行通过</th><th>联合通过</th></tr>')
        for label, s in sorted(scores.get('by_model_condition', {}).items()):
            blocks.append('<tr><td>' + esc(label) + '</td>' + ''.join('<td>' + str(s[k]) + '/' + str(s['total']) + '</td>' for k in ['knowledge_pass', 'observable', 'execution_pass', 'joint_pass']) + '</tr>')
        blocks.append('</table><p>仅此开发批次；知识通过与执行通过分别计分，不代表总体SOTA水平。</p>')
    for name in ['protocol.json', 'findings.json', 'scores_summary.json', 'legacy_check.json', 'retrieval_support_review.json', 'design_audit.json']:
        p = run / name
        if p.exists():
            blocks.append('<details><summary>' + esc(name) + ' 完整记录</summary><pre>' + esc(read(p)) + '</pre></details>')
    for c in cases:
        blocks += ['<article><h2>' + esc(c['question_id'] + ' · ' + c['concept']) + '</h2>',
                   '<p>' + esc({k: c.get(k) for k in ['domain', 'knowledge_types', 'application_level', 'task', 'evidence_mode', 'readiness']}) + '</p>',
                   '<h3>给模型的原题</h3><pre>' + esc(c['prompt']) + '</pre>']
        if c.get('edit_source'):
            blocks.append(picture(c['edit_source']['path'], '编辑原图：' + str(c['edit_source'].get('observations', ''))))
        blocks.append('<h3>来源原文与支持范围</h3>')
        for source in c['sources']:
            blocks.append('<p><a href="' + esc(source['url']) + '">' + esc(source.get('title', source['source_id'])) + '</a></p><blockquote>' + esc(source['quote']) + '</blockquote><p>' + esc(source['support_scope']) + '</p>')
        blocks.append('<details><summary>实际送入模型的文字资料</summary><pre>' + esc(c['knowledge_text']) + '</pre></details>')
        for im in c['reference_images']:
            blocks.append(picture(im['path'], '检索参考材料：' + str(im.get('support_scope', ''))))
        for title, key in [('条件→知识→可见结果', 'application_links'), ('知识判据', 'knowledge_checks'),
                           ('执行判据', 'execution_checks'), ('合理例外', 'exceptions'), ('证据缺口', 'gaps')]:
            blocks.append('<h3>' + title + '</h3><pre>' + esc(c.get(key, [])) + '</pre>')
        for model in ['bagel', 'gemini']:
            blocks.append('<h3>' + model + ' 全部输出</h3>')
            for result_path in sorted((run / model / 'jobs').glob(c['question_id'] + '__*/result.json')):
                result = read(result_path)
                p = result_path.parent / 'image.png'
                if not p.exists():
                    p = next(iter(result_path.parent.glob('image.*')), p)
                if p.exists() and result.get('ok') and sha(p.read_bytes()) == result.get('output_sha256'):
                    score = output_scores.get((model, result_path.parent.name))
                    label = result_path.parent.name
                    if score:
                        label += ' · 知识 ' + str(score['knowledge']) + ' · 执行 ' + str(score['execution_pass']) + ' · 画质 ' + str(score['quality']) + '/5'
                    blocks.append(picture(p, label))
                blocks.append('<details><summary>' + esc(result_path.parent.name) + ' 运行记录</summary><pre>' + esc(result) + '</pre></details>')
        blocks.append('</article>')
    blocks.append('<h2>29域材料样例或明确缺口</h2><p>gap表示本次有限检查尚未形成完整题，不代表领域没有可用知识。</p>')
    for d in domains['domains']:
        blocks.append('<details><summary>' + esc(d['domain'] + ' · ' + d['status'] + ' · ' + d['concept']) + '</summary><pre>' + esc(d) + '</pre>')
        for im in d['reference_images']:
            blocks.append(picture(im['path'], im.get('support_scope', '')))
        blocks.append('</details>')
    # Derived report may be regenerated; source artifacts never overwritten.
    (run / 'review.html').write_text('\n'.join(blocks))
    print(str(run / 'review.html'))


def summarize(args):
    run = Path(args.run)
    cases = {c['question_id']: c for c in read(run / 'cases.json')['cases']}
    jobs = [json.loads(s) for s in (run / 'jobs.jsonl').read_text().splitlines()]
    expected = {(m, j['job_id']): j for m in ['bagel', 'gemini'] for j in jobs}
    reviews = read(args.reviews)['reviews']
    seen = set(); groups = defaultdict(Counter); strata = defaultdict(Counter); rows = []
    for r in reviews:
        key = r['model'], r['job_id']
        if key not in expected or key in seen:
            raise ValueError('Unknown or duplicate review: ' + str(key))
        seen.add(key)
        j = expected[key]; c = cases[j['question_id']]
        rp = run / r['model'] / 'jobs' / r['job_id'] / 'result.json'
        result = read(rp) if rp.exists() else {'ok': False, 'error': 'missing_result'}
        image_path = Path(result['image']) if result.get('image') else None
        valid_image = bool(result.get('ok') and image_path and image_path.exists()
                           and sha(image_path.read_bytes()) == result.get('output_sha256'))
        if valid_image and r.get('output_sha256') != result['output_sha256']:
            raise ValueError('Review not bound to generated image hash: ' + str(key))
        ids = {k['id'] for k in c['knowledge_checks']}
        if set(r['knowledge']) != ids or any(v not in {'pass', 'conflict', 'unobservable'} for v in r['knowledge'].values()):
            raise ValueError('Knowledge review incomplete: ' + str(key))
        if not r.get('observations') or not r.get('reviewer'):
            raise ValueError('Review needs observations and identity')
        if not valid_image and (any(v != 'unobservable' for v in r['knowledge'].values()) or r['execution_pass'] is not False):
            raise ValueError('Missing/failed/changed output cannot be scored as observed or passing: ' + str(key))
        observable = all(v != 'unobservable' for v in r['knowledge'].values())
        knowledge = all(v == 'pass' for v in r['knowledge'].values())
        execution = r['execution_pass'] is True
        if valid_image and (type(r.get('quality')) is not int or not 1 <= r['quality'] <= 5):
            raise ValueError('Valid output needs separate quality score1..5: ' + str(key))
        counts = dict(total=1, generation_error=int(not valid_image), observable=int(observable),
                      knowledge_pass=int(knowledge), execution_pass=int(execution), joint_pass=int(knowledge and execution),
                      knowledge_items=len(ids), knowledge_items_pass=sum(v == 'pass' for v in r['knowledge'].values()),
                      knowledge_items_observable=sum(v != 'unobservable' for v in r['knowledge'].values()))
        counter = groups[r['model'] + '/' + j['condition']]
        counter.update(counts)
        for dimension in ['domain', 'application_level', 'task']:
            strata['/'.join([r['model'], j['condition'], dimension, c[dimension]])].update(counts)
        rows.append(dict(model=r['model'], job_id=r['job_id'], question_id=j['question_id'], condition=j['condition'],
                         family=c.get('knowledge_family_id', c['concept']), knowledge_pass=knowledge,
                         execution_pass=execution, joint_pass=knowledge and execution, observable=observable,
                         quality=r.get('quality'), knowledge=r['knowledge']))
    result = {'reviewer_type': 'assistant_not_human_gold', 'expected': len(expected), 'reviewed': len(seen),
              'missing': [list(k) for k in expected if k not in seen],
              'by_model_condition': {k: dict(v) for k, v in groups.items()},
              'by_stratum': {k: dict(v) for k, v in strata.items()}, 'per_output': rows,
              'scope': 'Development results, not formal test accuracy. Quality remains per-output; unobservable is not a pass.'}
    publish(run / 'scores_summary.json', encoded(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def prepare_review(args):
    run = Path(args.run)
    cases = {c['question_id']: c for c in read(run / 'cases.json')['cases']}
    jobs = [json.loads(s) for s in (run / 'jobs.jsonl').read_text().splitlines()]
    order = [(m, j) for m in ['bagel', 'gemini'] for j in jobs]
    random.Random(873541).shuffle(order)
    mapping = []; packets = []
    for index, (model, job) in enumerate(order, 1):
        d = run / model / 'jobs' / job['job_id']
        rp = d / 'result.json'
        result = read(rp) if rp.exists() else {'ok': False, 'error': 'missing_result'}
        images = sorted(d.glob('image.*'))
        opaque = f'output_{index:03d}'
        image_path = None
        if result.get('ok') and images and sha(images[0].read_bytes()) == result.get('output_sha256'):
            dest = run / 'blind' / (opaque + images[0].suffix)
            publish(dest, images[0].read_bytes()); image_path = str(dest.resolve())
        c = cases[job['question_id']]
        packet = {'review_id': opaque, 'image_path': image_path, 'output_sha256': result.get('output_sha256') if image_path else None,
                  'generation_error': None if image_path else result.get('error', 'no_image'),
                  'case': c, 'review_instruction': 'Inspect actual image and supplied scoring evidence. For every K id return pass/conflict/unobservable plus concrete observations; execution_pass boolean and quality1to5 separately. Model/condition concealed, not guaranteed unguessable. No human-gold claim.'}
        packets.append(packet)
        mapping.append(dict(review_id=opaque, model=model, job_id=job['job_id']))
    publish(run / 'blind_mapping.json', encoded(mapping))
    for i in range(3):
        publish(run / 'blind' / f'packets_{i}.json', encoded({'packets': packets[i::3]}))
    print(json.dumps({'packets': len(packets), 'images': sum(bool(p['image_path']) for p in packets)}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['render', 'summarize', 'prepare-review']); p.add_argument('--run', required=True); p.add_argument('--reviews')
    args = p.parse_args()
    {'render': render, 'summarize': summarize, 'prepare-review': prepare_review}[args.command](args)


if __name__ == '__main__':
    main()
