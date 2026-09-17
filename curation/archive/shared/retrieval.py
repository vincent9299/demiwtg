"""Small actual text-retrieval diagnostic against frozen local clean pages.

BM25 scores only the original task. Relevance/source IDs are used after ranking
for source-page recall, which is explicitly not passage-support correctness.
"""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
from collections import Counter, defaultdict
import math
from pathlib import Path
import re
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ROOT, read
from bagel_runner import encoded, publish, sha


def tokens(text):
    latin = re.findall(r'[a-z0-9]+', text.lower())
    chinese = re.findall(r'[\u3400-\u9fff]+', text)
    return latin + [part[i:i+2] for part in chinese for i in range(max(1, len(part)-1))]


def run(args):
    cases = read(args.cases)['cases']
    raw = Path(args.corpus).read_bytes()
    pages = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    docs = []
    for p in pages:
        text = p.get('text', '')
        # Fixed overlapping windows; never center chunks on answer excerpts.
        for offset in range(0, len(text), 1400):
            body = text[offset:offset+1800]
            if body.strip():
                docs.append(dict(url=p.get('url'), page_sha=p.get('page_sha'), offset=offset,
                                 text=body, title=p.get('title', '')))
    freqs = [Counter(tokens(d['title'] + '\n' + d['text'])) for d in docs]
    lengths = [sum(f.values()) for f in freqs]
    avg = sum(lengths)/max(1, len(lengths))
    postings = defaultdict(list)
    for i, f in enumerate(freqs):
        for t, count in f.items():
            postings[t].append((i, count))
    results = []
    for c in cases:
        scores = defaultdict(float)
        for t in set(tokens(c['prompt'])):
            matches = postings.get(t, [])
            idf = math.log(1+(len(docs)-len(matches)+0.5)/(len(matches)+0.5))
            for i, count in matches:
                scores[i] += idf * count*2.2/(count+1.2*(0.25+0.75*lengths[i]/avg))
        ranked = sorted(scores, key=lambda i: (-scores[i], i))[:5]
        selected = [dict(**docs[i], score=scores[i]) for i in ranked]
        expected = {s['url'] for s in c['sources']}
        available = {p.get('url') for p in pages}
        results.append(dict(question_id=c['question_id'], query=c['prompt'], retrieved=selected,
                            expected_source_urls=sorted(expected), expected_sources_in_corpus=sorted(expected & available),
                            source_page_hits=sorted(expected & {d['url'] for d in selected}),
                            passage_support_review='pending_not_inferred_from_url_match'))
    report = dict(version='knowledge_application_v1.retrieval.1', corpus_path=str(Path(args.corpus).resolve()),
                  corpus_sha256=sha(raw), pages=len(pages), chunks=len(docs), top_k=5,
                  algorithm='BM25 k1=1.2 b=0.75, Latin tokens plus CJK bigrams; task only; fixed 1800-char windows stride1400',
                  limitation='Source-page hits do not prove sufficient passages; oracle generation results are separate from actual retrieval.',
                  results=results)
    publish(Path(args.out), encoded(report))
    print(json.dumps({'cases': len(cases), 'cases_with_source_page_hit': sum(bool(r['source_page_hits']) for r in results),
                      'cases_with_any_source_in_corpus': sum(bool(r['expected_sources_in_corpus']) for r in results)}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', required=True)
    p.add_argument('--corpus', default=str(ROOT/'state/collect/docs_clean/pages_clean.jsonl'))
    p.add_argument('--out', required=True)
    run(p.parse_args())
