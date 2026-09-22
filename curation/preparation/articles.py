"""Knowledge article entities: drafts and reviewed publications share one table."""
import json
from pathlib import Path
import pyarrow as pa
from .images import canonical, identity

ARTICLES_URI='curated/articles.lance'
REFERENCE=pa.struct([(k,pa.string()) for k in ('title','url','local_path','source_note','original_url')]+[
    ('kinds',pa.list_(pa.string())),('paragraph_indices',pa.list_(pa.int64())),('source_ids',pa.list_(pa.string()))])
PLACEMENT=pa.struct([('caption',pa.large_string()),('figure_number',pa.int64()),('image_id',pa.string()),
    ('limitations',pa.large_string()),('paragraph_index',pa.int64()),('region',pa.string())])
TOPIC=pa.struct([('title',pa.string()),('content',pa.struct([
    ('paragraphs',pa.list_(pa.large_string())),('images',pa.list_(PLACEMENT))])),('references',pa.list_(REFERENCE))])
CITATION=pa.struct([('source_id',pa.string()),('source_family',pa.string()),('title',pa.string()),('text',pa.large_string()),
    ('sections',pa.list_(pa.string())),('context_before',pa.list_(pa.large_string())),
    ('context_after',pa.list_(pa.large_string())),('reference_notes',pa.list_(pa.large_string()))])
ILLUSTRATION=pa.struct([('image_id',pa.string()),('sha256',pa.string()),('evidence_json',pa.large_string())])
ARTICLES=pa.schema([pa.field('article_id',pa.string(),nullable=False),
    ('concept',pa.string()),('article_kind',pa.string()),('case_id',pa.string()),
    ('review_status',pa.string()),('status_reason',pa.large_string()),('release_ids',pa.list_(pa.string())),
    ('content',pa.list_(TOPIC)),('citations',pa.list_(CITATION)),('illustrations',pa.list_(ILLUSTRATION)),
    ('context_json',pa.large_string()),('source_file',pa.string()),('source_row',pa.int64()),('run_id',pa.string())])


def article_entity(row, *, release_id=None, run_id=None, source_file=None, source_row=None, article_id=None):
    known={'concept','case_id','status','status_reason','knowledge','published_passages','published_images'}
    illustrations=[]
    for item in row.get('published_images',[]):
        illustrations.append({'image_id':item.get('image_id') or item.get('record',{}).get('image_id'),
            'sha256':item.get('bytes',{}).get('sha256') or item.get('sha256'), 'evidence_json':canonical(item)})
    return dict(article_id=article_id or identity(['article',run_id or release_id,row['concept']]),
        concept=row['concept'],article_kind='knowledge',case_id=row.get('case_id'),
        review_status=row.get('status') or 'unreviewed',status_reason=row.get('status_reason'),
        release_ids=[release_id] if release_id and row.get('status')=='reviewed' else [],
        content=row.get('knowledge',[]),citations=row.get('published_passages',[]),illustrations=illustrations,
        context_json=canonical({k:v for k,v in row.items() if k not in known}),
        source_file=source_file,source_row=source_row,run_id=run_id)


def draft_entity(row):
    return dict(article_id=identity(['draft',row['source_file'],row['source_row']]),concept=row['concept'],
        article_kind=row.get('draft_kind') or 'summary',review_status=row['review_status'],release_ids=[],
        content=[{'title':'','content':{'paragraphs':[row['text']],'images':[]},'references':[]}],
        citations=[],illustrations=[],context_json='{}',source_file=row['source_file'],source_row=row['source_row'])


def article_record(row):
    result=json.loads(row.get('context_json') or '{}')
    # Drop absent optional reference properties to preserve the original business contract.
    content=[]
    for t in row.get('content') or []:
        content.append({**t,'references':[{k:v for k,v in r.items() if v is not None} for r in t['references'] or []]})
    result.update(concept=row['concept'],case_id=row.get('case_id'),status=row['review_status'],
        status_reason=row.get('status_reason'),knowledge=content,published_passages=row.get('citations') or [],
        published_images=[json.loads(i['evidence_json']) for i in row.get('illustrations') or []])
    return result


def write_articles(root, rows):
    from demiflow.lance.transaction import registered_table_edit
    from demiflow.lance.registry import Catalog
    import lance
    rows=list(rows)
    if not rows:raise ValueError('No articles to write')
    table=pa.Table.from_pylist(rows,schema=ARTICLES)
    with registered_table_edit(root,ARTICLES_URI,schema_name='articles',schema_version='v1') as ds:
        if ds is None:
            ds=lance.write_dataset(table,str(Path(root)/ARTICLES_URI))
            ds.create_scalar_index('article_id','BTREE')
            ds.create_scalar_index('concept','BTREE')
            ds.create_scalar_index('release_ids','LABEL_LIST')
        else:
            keys=', '.join("'"+r['article_id']+"'" for r in rows)
            existing={r['article_id']:r for r in ds.to_table(filter=f'article_id IN ({keys})').to_pylist()}
            changed=[r for r in table.to_pylist() if existing.get(r['article_id'])!=r]
            if changed:ds.merge_insert('article_id').when_matched_update_all().when_not_matched_insert_all().execute(pa.Table.from_pylist(changed,schema=ARTICLES))
    return max((r for r in Catalog(root).registered() if r.relative_uri==ARTICLES_URI),key=lambda r:r.lance_version)
