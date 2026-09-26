"""197 个已发布概念的出题适用性讨论实验；不接入正式出题链。"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[5]
ROOT = PROJECT.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(ROOT / 'demiflow' / 'src'))

import lance
import pyarrow as pa
import yaml
from demiflow import data
from demiflow.operator_llm.parser import parse_prompt_pack

HERE = Path(__file__).resolve().parent
TABLES = PROJECT / 'benchmark/t2i/v2/datasets'
RUN = 'screening197_20260926'
INPUT = TABLES / f'inputs__{RUN}.lance'
CODEX = TABLES / f'codex__{RUN}.lance'
GLM = TABLES / f'glm__{RUN}.lance'
FIELDS = ['decision', 'scope', 'candidate_point', 'visual_check', 'basis', 'reason']
LABEL = pa.struct([(k, pa.string()) for k in FIELDS])


def article_card(row):
    """已审核文章的确定性节选；保留章节标题，明确不等于完整文章。"""
    topics = row.get('content') or []
    excerpts = []
    for topic in topics[:8]:
        text = ' '.join((topic.get('content') or {}).get('paragraphs') or [])
        if text.strip():
            excerpts.append({'title': topic.get('title', ''), 'excerpt': text[:250],
                             'excerpt_truncated': len(text) > 250})
    return {'concept': row['concept'], 'article': {
        'article_id': row['article_id'], 'topic_titles': [t.get('title', '') for t in topics],
        'excerpts': excerpts, 'topics_omitted': max(0, len(topics) - 8),
    }}


def image_cards(row):
    for assessment in row.get('concept_assessments') or []:
        if not assessment.get('published') or assessment.get('review_status') != 'keep':
            continue
        description = assessment.get('description') or {}
        support = assessment.get('visual_support') or {}
        yield {'concept': assessment['concept'], 'image': {
            'sha256': row['sha256'], 'caption': (description.get('caption') or '')[:250],
            'representation': description.get('representation', ''),
            'supports': (support.get('supports') or '')[:250],
            'limitations': (support.get('limitations') or '')[:200],
        }}


def pack_card(row):
    payload = {
        'concept': row['concept'], 'aliases': row.get('aliases') or [],
        'taxonomy': json.loads(row.get('taxonomy') or '[]'),
        'reviewed_article_excerpts': row.get('articles') or [],
        'published_image_count': row['image_count'],
        'published_image_annotation_samples': row['image_samples'],
        'input_note': '本轮仅提供文字。文章为确定性节选；图片字段为既有模型标注，并非实际像素证据。'
                      '缺少某事实不表示事实不存在；允许使用可靠已有知识。别名/分类可能混有不同义项。',
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    return {'concept': row['concept'], 'payload_json': encoded,
            'input_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
            'article_count': len(row.get('articles') or []), 'image_count': row['image_count']}


def prepare():
    # 一行一张已发布图片，展开其 keep 概念关系，再按概念聚合。
    images = (data.read_lance(str(ROOT / 'demiwtg/preparation/datasets/images.lance'), version=5,
                             columns=['sha256', 'concept_assessments'],
                             filter='array_length(published_concepts) > 0')
              .flat_map(image_cards)
              .reduce_by_key('concept', lambda acc, row: {
                  'concept': row['concept'], 'image_count': (acc['image_count'] if acc else 0) + 1,
                  'image_samples': ((acc['image_samples'] if acc else []) + [row['image']])[:3],
              }))
    # 一行一篇 reviewed 文章；本次只取少量节选用于廉价概念级筛选。
    articles = (data.read_lance(str(ROOT / 'demiwtg/preparation/datasets/articles.lance'), version=4,
                               columns=['concept', 'article_id', 'content'],
                               filter="review_status = 'reviewed'")
                .map(article_card)
                .reduce_by_key('concept', lambda acc, row: {
                    'concept': row['concept'], 'articles': (acc['articles'] if acc else []) + [row['article']],
                }))
    concepts = (data.read_lance(str(ROOT / 'datasets/master_concepts.lance'), version=2,
                               columns=['name', 'aliases', 'taxonomy'])
                .map(lambda row: {'concept': row['name'], 'aliases': row['aliases'], 'taxonomy': row['taxonomy']}))
    (images.join(articles, on='concept', how='left')
     .join(concepts, on='concept', how='left').map(pack_card)
     .write_lance(str(INPUT), mode='overwrite', schema=pa.schema([
         ('concept', pa.string()), ('payload_json', pa.large_string()), ('input_sha256', pa.string()),
         ('article_count', pa.int64()), ('image_count', pa.int64()),
     ])))
    ds = lance.dataset(str(INPUT))
    assert ds.count_rows() == 197
    print(f'Prepared {ds.count_rows()} concepts; input version={ds.version}', flush=True)


def import_codex():
    # 此 TSV 是助手逐条撰写的独立标注稿，不从 GLM 输出生成。
    rows = list(csv.DictReader((HERE / 'codex_labels.tsv').open(), delimiter='\t'))
    names = [r['concept'] for r in rows]
    expected = set(lance.dataset(str(INPUT)).to_table(columns=['concept']).column('concept').to_pylist())
    assert len(rows) == len(set(names)) == 197 and set(names) == expected
    assert all(r['decision'] in {'keep', 'hold'} for r in rows)
    assert all(r['candidate_point'].strip() and r['visual_check'].strip()
               for r in rows if r['decision'] == 'keep')
    (data.from_items(rows)
     .map(lambda row: {'concept': row['concept'], 'label': {k: row[k] for k in FIELDS}})
     .join(data.read_lance(str(INPUT), columns=['concept', 'input_sha256']), on='concept', how='inner')
     .write_lance(str(CODEX), mode='overwrite', schema=pa.schema([
         ('concept', pa.string()), ('label', LABEL), ('input_sha256', pa.string()),
     ])))
    print('Imported 197 independently authored Codex labels', flush=True)


def normalize(row):
    call = dict(row.get('model_call') or (row.get('model_error') or {}).get('call') or {})
    reasoning = call.pop('reasoning', None)
    if 'attempts' in call:
        call['attempts'] = [{k: v for k, v in item.items() if k != 'reasoning'} for item in call['attempts']]
    error = row.get('model_error')
    label = row.get('label')
    if not error and label and label['decision'] == 'keep' and not (
        label['candidate_point'].strip() and label['visual_check'].strip() and label['basis'].strip()
    ):
        error = {'type': 'invalid_label', 'detail': 'keep requires a concrete point, visible check and basis'}
    print(f"{row['concept']}: {label['decision'] if label and not error else 'ERROR'}; reused={call.get('reused')}", flush=True)
    return {'concept': row['concept'], 'input_sha256': row['input_sha256'],
            'status': 'error' if error else 'ok', 'label': label,
            'error_json': json.dumps(error, ensure_ascii=False) if error else None,
            'reasoning': reasoning, 'call_json': json.dumps(call, ensure_ascii=False)}


def run_glm(limit=None):
    assert CODEX.exists(), 'Finish independent Codex annotations first'
    spec = yaml.safe_load((HERE / 'screening_v0.1.yaml').read_text())
    pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
    os.environ.setdefault('MODELHUB_API_KEY', 'anything')
    ds = data.read_lance(str(INPUT))
    if limit:
        ds = ds.limit(limit)
    options = {
        'lance_journal': {'root': str(ROOT), 'relative_uri': str((TABLES / f'calls__{RUN}.lance').relative_to(ROOT))},
        'timeout_s': 300, 'verify_model': 'listed', 'require_finish_reason_stop': True, 'trust_env': False,
        'request_options': {'temperature': 0, 'max_tokens': 4096,
                            'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'}},
    }
    target = TABLES / f'glm_probe__{RUN}.lance' if limit else GLM
    (ds.map(lambda row: {**row, 'payload': json.loads(row['payload_json'])})
     .map_prompt_async('screen_concept', config=pack, options=options, max_requests=197,
                       inputs={'payload': 'payload'}, output='label', call_output='model_call',
                       error_output='model_error', concurrency=4, queue_depth=1)
     .map(normalize).materialize()
     .write_lance(str(target), mode='overwrite', schema=pa.schema([
         ('concept', pa.string()), ('input_sha256', pa.string()), ('status', pa.string()),
         ('label', LABEL), ('error_json', pa.large_string()), ('reasoning', pa.large_string()),
         ('call_json', pa.large_string()),
     ])))
    print(f'GLM output: {target}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'import_codex', 'glm'])
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.stage == 'prepare': prepare()
    elif args.stage == 'import_codex': import_codex()
    else: run_glm(args.limit)
