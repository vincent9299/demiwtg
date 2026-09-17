"""Read-only targeted extraction of source rows; consumer supports title lookup."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import re
from pathlib import Path

ROOT = _ARCHIVE_ROOT
OUT = ROOT / 'state/curation/knowledge_application_v1/expansion20_v1/new_corpus_evidence'
TITLES = ['Agateware', 'Nerikomi', 'Pancake ice', 'Larch', 'Larix', 'Scallop',
          'Flag of South Africa', 'Ukiyo-e', 'Flowchart', 'Lycoris radiata',
          'Dryopteris', 'Harp', 'Potassium permanganate', 'Flag of Nepal',
          'Stalactite', '绞胎瓷', '絞胎瓷', '饼状冰', '餅狀冰', '落叶松属',
          '落葉松屬', '扇贝', '扇貝', '南非国旗', '南非國旗', '浮世绘', '浮世繪',
          '流程图', '流程圖', '石蒜', '鳞毛蕨属', '鱗毛蕨屬', '竖琴', '豎琴',
          '高锰酸钾', '高錳酸鉀', '尼泊尔国旗', '尼泊爾國旗', '钟乳石', '鐘乳石']
PATTERN = re.compile(rb'"title"\s*:\s*"(' + b'|'.join(re.escape(t.encode()) for t in TITLES) + rb')"')

def scan(path):
    found = []
    with gzip.open(path, 'rb') as handle:
        for lineno, line in enumerate(handle, 1):
            if not PATTERN.search(line[:2000]):
                continue
            row = json.loads(line)
            if row.get('title') not in TITLES:
                continue
            digest = hashlib.sha256(line).hexdigest()
            record = {'source_file': str(path), 'source_line': lineno,
                      'raw_line_sha256': digest, 'raw_line_bytes': len(line), 'page': row}
            target = OUT / (digest + '.json')
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            found.append({'title': row['title'], 'lang': row['lang'], 'qid': row.get('qid'),
                          'revision_id': row.get('revision_id'), 'path': str(target),
                          'source_file': str(path), 'source_line': lineno})
    return {'shard': str(path), 'lines_scanned': lineno, 'matches': found}

def load_sources(title=None):
    index = json.loads((OUT / 'index.json').read_text())
    return [json.loads(Path(m['path']).read_text()) for s in index['shards'] for m in s['matches']
            if title is None or m['title'] == title]

def map_qids():
    """Join with fat sitelinks by language + page_id; never mutate original rows."""
    pages = load_sources()
    wanted = {(x['page']['lang'], x['page']['page_id']) for x in pages}
    source = ROOT / 'datasets/demiwtg/meta/qid_concepts.fat.jsonl.gz'
    matches = []
    with gzip.open(source, 'rb') as handle:
        for lineno, line in enumerate(handle, 1):
            if not any(t.encode() in line for t in TITLES):
                continue
            row = json.loads(line)
            for lang in ('en', 'zh'):
                site = row.get(lang) or {}
                if (lang, site.get('page_id')) in wanted:
                    matches.append({'qid': row['qid'], 'lang': lang, 'page_id': site['page_id'],
                                    'title': site['title'], 'source_file': str(source), 'source_line': lineno,
                                    'raw_line_sha256': hashlib.sha256(line).hexdigest(),
                                    'record': row, 'join_method': 'exact language + page_id'})
    (OUT / 'qid_mapping.json').write_text(json.dumps(matches, ensure_ascii=False, indent=2))
    print('Mapped sitelinks:', len(matches), flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extract', action='store_true')
    parser.add_argument('--title')
    parser.add_argument('--map-qids', action='store_true')
    args = parser.parse_args()
    if args.map_qids:
        map_qids()
    elif args.extract:
        OUT.mkdir(parents=True, exist_ok=True)
        shards = sorted((ROOT / 'datasets/demiwtg/corpus').glob('*.gz'))
        results = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=21) as pool:
            for result in pool.map(scan, shards):
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        (OUT / 'index.json').write_text(json.dumps({'query_titles': TITLES, 'shards': results}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(load_sources(args.title), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
