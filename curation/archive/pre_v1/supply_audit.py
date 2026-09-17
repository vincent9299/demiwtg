"""Read-only, domain-stratified supply audit; counts are availability, not validity."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import time

ROOT = _ARCHIVE_ROOT
DEFAULT = ROOT / 'state/curation/supply_audit_v1'


def rows(path):
    with path.open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def render(run):
    report = json.loads((run / 'report.json').read_text())
    e = lambda value, **kwargs: html.escape(str(value or ''), **kwargs)
    parts = ['<meta charset="utf-8"><title>29域材料供给审查</title>',
             '<style>body{font-family:sans-serif;max-width:1300px;margin:32px auto}td,th{border:1px solid #ccc;padding:7px}table{border-collapse:collapse}img{max-width:220px;max-height:170px}pre{white-space:pre-wrap}</style>',
             '<h1>29域材料供给审查</h1><p>元数据关联与清洗文本可用性，不是事实通过、图片身份通过或可出题率。抽样未按模型成绩筛选。领域允许交叉挂载，领域计数不能相加作为全局唯一量。</p>',
             '<pre>' + e(json.dumps(report['global'], ensure_ascii=False, indent=2)) + '</pre>',
             '<table><tr><th>领域</th><th>概念</th><th>有图片关联</th><th>有原始docs关联</th><th>有净版docs</th><th>净版docs＋≥2图片关联</th></tr>']
    for d in report['domains']:
        parts.append('<tr>' + ''.join('<td>' + e(str(d[k])) + '</td>' for k in
                     ['domain', 'concepts', 'with_images', 'with_docs', 'with_clean_docs', 'clean_and_two_images']) + '</tr>')
    parts.append('</table>')
    if (run / 'domain_supply.csv').exists():
        parts.append('<p><a href="' + e(str(run / 'domain_supply.csv'), quote=True) + '">29域供给与方向 CSV（研究批次导出）</a></p>')
    parts.append('<h2>分层候选材料包（尚未审图、未逐条事实核验）</h2>')
    for d in report['domains']:
        parts.append('<h3>' + e(d['domain']) + '</h3>')
        for c in d['samples']:
            parts.append('<details><summary>' + e(c['name']) + ' · ' + e(c['supply_lane']) + '</summary>')
            parts.append('<p>' + e(' / '.join(c['branches'])) + '</p>')
            for doc in c['docs']:
                parts.append('<p><a href="' + e(doc['url'], quote=True) + '">' + e(doc['title']) + '</a></p><pre>' + e(doc['preview']) + '</pre>')
            for im in c['images']:
                if im['local_exists']:
                    parts.append('<a href="' + e(im['landing_url'], quote=True) + '"><img src="' + e(im['path'], quote=True) + '"></a>')
            parts.append('<p>图片仅检查文件存在；文字片段只是定位预览，不自动支持候选题答案。</p></details>')
    for filename, title in [('research.json', '补充数据与扩题研究'), ('reviewed_cards.json', '助手审查案例'), ('alarm_case.json', '海竿报警铃：旧结果与新提案')]:
        p = run / filename
        if p.exists():
            parts.append('<h2>' + title + '</h2>')
            if filename == 'reviewed_cards.json':
                for card in json.loads(p.read_text())['cards']:
                    parts.append('<h3>' + e(card['concept']) + '</h3><pre>' + e(json.dumps({k: v for k, v in card.items() if k != 'images'}, ensure_ascii=False, indent=2)) + '</pre>')
                    for im in card['images']:
                        parts.append('<img style="max-width:440px;max-height:350px" src="' + e(im['path'], quote=True) + '"><pre>' + e(json.dumps({k: v for k, v in im.items() if k != 'path'}, ensure_ascii=False, indent=2)) + '</pre>')
            else:
                parts.append('<details><summary>完整研究记录</summary><pre>' + e(p.read_text()) + '</pre></details>')
            if filename == 'research.json':
                research = json.loads(p.read_text())
                parts.append('<ol>' + ''.join('<li>' + e(step) + '</li>' for step in research['pipeline']) + '</ol>')
                for source in research['sources']:
                    parts.append('<p><a href="' + e(source['url'], quote=True) + '">' + e(source['name']) + '</a> — ' + e(source['priority']) + '；' + e(source['use']) + '</p>')
                parts.append('<table><tr><th>领域</th><th>候选方向（未核验题）</th><th>需避免的问题</th></tr>')
                for d in research['domain_plan']:
                    parts.append('<tr>' + ''.join('<td>' + e(d[k]) + '</td>' for k in ['domain', 'candidate_direction', 'guard']) + '</tr>')
                parts.append('</table>')
            if filename == 'alarm_case.json':
                alarm = json.loads(p.read_text())
                if 'external_reference_example' in alarm:
                    ref = alarm['external_reference_example']
                    parts.append('<p><a href="' + e(ref['source_url'], quote=True) + '">独立商品参考图候选</a></p><img src="' + e(ref['image_url'], quote=True) + '">')
                for im in alarm['images']:
                    parts.append('<p>' + e(im['role']) + '</p><img style="max-width:500px;max-height:450px" src="' + e(im['path'], quote=True) + '">')
    (run / 'report.html').write_text('\n'.join(parts))


def audit(run):
    run.mkdir(parents=True, exist_ok=True)
    meta = ROOT / 'datasets/demiwtg/meta'
    inputs = [meta / n for n in ['taxonomy.json', 'concepts.json', 'images.jsonl', 'docs.jsonl']]
    clean_path = ROOT / 'state/collect/docs_clean/pages_clean.jsonl'
    inputs.append(clean_path)
    summary_path = ROOT / 'state/collect/concepts_docs_draft.jsonl'
    if summary_path.exists():
        inputs.append(summary_path)
    before = {str(p): {'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in inputs}
    concepts = {c['name']: c for c in json.loads(inputs[1].read_text())['concepts']}
    summary_names = {d['name'] for d in rows(summary_path)} if summary_path.exists() else set()
    tree = json.loads(inputs[0].read_text())['tree']
    memberships = defaultdict(set)
    branches = defaultdict(set)
    domains = [d['name'] for d in tree['children']]

    def walk(node, domain, branch):
        for name in node.get('instances', []):
            if name in concepts:
                memberships[name].add(domain)
                branches[(name, domain)].add(branch)
        for child in node.get('children', []):
            walk(child, domain, child['name'] if node['name'] == domain else branch)

    for d in tree['children']:
        walk(d, d['name'], d['name'])
    docs = defaultdict(set)
    doc_rows = 0
    for d in rows(inputs[3]):
        doc_rows += 1
        for name in set(d.get('concepts', [])):
            docs[name].add(d['page_sha'])
    clean = defaultdict(list)
    clean_pages = 0
    for d in rows(clean_path):
        clean_pages += 1
        for name in set(d.get('concepts', [])):
            clean[name].append({k: d.get(k, '') for k in ['page_sha', 'title', 'url', 'text']})
    image_counts = Counter()
    im_rows = unknown_links = 0
    first_images = defaultdict(list)
    for im in rows(inputs[2]):
        im_rows += 1
        for name in set(im.get('instances', [])):
            if name not in concepts:
                unknown_links += 1
                continue
            image_counts[name] += 1
            if len(first_images[name]) < 2:
                first_images[name].append({k: im.get(k, '') for k in ['sha256', 'path', 'landing_url', 'content_url', 'source', 'license']})
        if im_rows % 500000 == 0:
            print('image manifest rows', im_rows, flush=True)
    report = {'created': time.time(), 'inputs': before,
              'scope': 'Metadata availability only; no model calls, no original writes; first two manifest images are not curated references.',
              'global': {'concepts': len(concepts), 'domains': len(domains), 'image_manifest_rows': im_rows,
                         'concepts_with_images': len(image_counts), 'raw_docs_rows': doc_rows,
                         'concepts_with_raw_docs': len(set(docs) & concepts.keys()),
                         'clean_pages': clean_pages, 'concepts_with_clean_docs': len(set(clean) & concepts.keys()),
                         'concepts_with_unverified_summary': len(summary_names & concepts.keys()),
                         'unknown_image_concept_links': unknown_links,
                         'unmounted_concepts': len(concepts.keys() - memberships.keys()),
                         'warning': 'Images counted as manifest associations, not verified distinct usable files. Raw/clean docs can be wrong-concept or stale. All domains are cross-membership counts.'}, 'domains': []}
    selected_global = set()
    for domain in domains:
        names = [n for n in memberships if domain in memberships[n]]
        d = {'domain': domain, 'concepts': len(names), 'with_images': sum(image_counts[n] > 0 for n in names),
             'with_docs': sum(bool(docs[n]) for n in names), 'with_clean_docs': sum(bool(clean[n]) for n in names),
             'clean_and_two_images': sum(bool(clean[n]) and image_counts[n] >= 2 for n in names), 'samples': []}
        for lane in ['docs_and_images', 'images_without_clean_docs']:
            eligible = [n for n in names if image_counts[n] >= 2 and bool(clean[n]) == (lane == 'docs_and_images')]
            eligible.sort(key=lambda n: hashlib.sha256((domain + ':' + n).encode()).hexdigest())
            used_branches = set()
            picked = []
            for allow_repeat_branch in [False, True]:
                for name in eligible:
                    if name in picked or name in selected_global:
                        continue
                    bs = branches[(name, domain)]
                    if not allow_repeat_branch and bs & used_branches:
                        continue
                    picked.append(name)
                    used_branches.update(bs)
                    if len(picked) == 4:
                        break
                if len(picked) == 4:
                    break
            for name in picked:
                selected_global.add(name)
                images = []
                for im in first_images[name]:
                    im = dict(im)
                    path = ROOT / 'datasets/demiwtg' / im['path']
                    im['path'] = str(path)
                    im['local_exists'] = path.is_file()
                    images.append(im)
                d['samples'].append({'name': name, 'supply_lane': lane, 'branches': sorted(branches[(name, domain)]),
                                     'image_associations': image_counts[name], 'clean_doc_count': len(clean[name]),
                                     'docs': [{'title': x['title'], 'url': x['url'], 'page_sha': x['page_sha'], 'preview': x['text'][:1600]} for x in clean[name][:2]],
                                     'images': images, 'status': 'unreviewed_supply_sample'})
        report['domains'].append(d)
    after = {str(p): {'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in inputs}
    report['inputs_changed_during_scan'] = [p for p in before if before[p] != after[p]]
    report['global']['sampled_unique_concepts'] = len(selected_global)
    (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    render(run)
    print(json.dumps(report['global'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['audit', 'render'])
    parser.add_argument('--run', type=Path, default=DEFAULT)
    args = parser.parse_args()
    (audit if args.command == 'audit' else render)(args.run.resolve())
