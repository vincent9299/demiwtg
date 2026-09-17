"""Read-only source adapters. Bounded scans report limits rather than claim absence."""
import gzip
import hashlib
import json
from pathlib import Path
from .contracts import snapshot


def discover(dataset,project):
    dataset=Path(dataset);project=Path(project)
    fixed=[('legacy_concepts',dataset/'meta/concepts.json'),('taxonomy',dataset/'meta/taxonomy.json'),
      ('qid_concepts',dataset/'meta/qid_concepts.fat.jsonl.gz'),
      ('qid_concepts_base',dataset/'meta/qid_concepts.jsonl'),
      ('legacy_docs',dataset/'meta/docs.jsonl'),
      ('legacy_images',dataset/'meta/images.jsonl'),('qid_images',dataset/'meta/qid_images.jsonl.gz'),
      ('image_roles',dataset/'meta/qid_image_roles.tsv.gz'),('gallery_captions',dataset/'meta/qid_gallery_captions.tsv.gz'),
      ('qid_graph',dataset/'meta/qid_graph.jsonl.gz')]
    fixed.extend(('wiki_pages',p) for p in sorted((dataset/'corpus').glob('pages-*.jsonl.gz')))
    for batch in ['pilot','nonobject_v1','scene_v1','expansion20_v1','version3_20']:
        fixed.append(('historical_cases',project/'state/curation/knowledge_application_v1'/batch/'cases.json'))
    return [{'kind':kind,**snapshot(path)} for kind,path in fixed]


def open_bytes(path):
    return gzip.open(path,'rb') if str(path).endswith('.gz') else open(path,'rb')


def rows(path,kind,max_rows,stats):
    """Yield raw-row provenance; metadata listing limits never imply full coverage."""
    if kind in ('legacy_concepts','taxonomy','historical_cases'):
        raw=Path(path).read_bytes();doc=json.loads(raw)
        records=doc.get('concepts',[]) if kind=='legacy_concepts' else (doc.get('cases',[]) if kind=='historical_cases' else [doc])
        stats['total_rows']=len(records);stats['complete']=len(records)<=max_rows
        content_hash=hashlib.sha256(raw).hexdigest()
        for i,r in enumerate(records[:max_rows],1):
            stats['rows_scanned']=i
            yield r,{'source_path':str(path),'record_index':i,'source_content_sha256':content_hash}
        return
    with open_bytes(path) as f:
        for i,raw in enumerate(f,1):
            if i>max_rows:stats['complete']=False;break
            stats['rows_scanned']=i
            provenance={'source_path':str(path),'line':i,'raw_line_sha256':hashlib.sha256(raw).hexdigest()}
            try:
                if kind in ('image_roles','gallery_captions'):
                    cells=raw.decode().rstrip('\r\n').split('\t')
                    if kind=='image_roles':
                        if len(cells)!=4:raise ValueError('expected four role columns')
                        r=dict(zip(['qid','property','role','commons_file'],cells))
                    else:
                        if len(cells)<3:raise ValueError('expected caption columns')
                        r={'qid':cells[0],'commons_file':cells[1],'caption':'\t'.join(cells[2:])}
                else:r=json.loads(raw)
                yield r,provenance
            except (ValueError,UnicodeError) as e:
                stats['invalid_rows'].append({**provenance,'error':str(e)[:200]})
        else:stats['complete']=True


def matches(row,kind,request,links):
    value=request['value'];qid=value if request['kind']=='qid' else None
    if kind=='legacy_concepts':return request['kind']=='legacy' and row.get('name')==value
    if kind in ('qid_concepts','qid_concepts_base'):return qid is not None and row.get('qid')==qid
    if kind in ('legacy_docs','legacy_images'):
        return request['kind']=='legacy' and value in (row.get('concepts') or row.get('instances') or [])
    if kind in ('qid_images','image_roles','gallery_captions'):return qid is not None and row.get('qid')==qid
    if kind=='wiki_pages':
        if qid is None:return False
        if row.get('qid'):return row['qid']==qid
        return (row.get('lang'),row.get('page_id')) in links.get(qid,set())
    if kind=='qid_graph':return qid is not None and qid in (row.get('from'),row.get('to'))
    return False


def inspect_source(source):
    if not source['exists']:return {**source,'status':'missing'}
    if source['kind']=='historical_cases':
        return {**source,'status':'available','role':'Historical case/source pointers; never factual evidence or target-image references automatically.'}
    stats={'rows_scanned':0,'complete':False,'invalid_rows':[]}
    try:
        first=next(rows(source['path'],source['kind'],1,stats),None)
        if snapshot(source['path'])!={k:source[k] for k in ('path','exists','size','mtime_ns','inode')}:
            raise ValueError('source changed while inspecting')
        return {**source,'status':'readable' if first else 'empty_or_invalid','sample_fields':list(first[0]) if first else [],'inspection':'schema sample only; not row count or quality validation','errors':stats['invalid_rows']}
    except (OSError,ValueError,EOFError) as e:return {**source,'status':'read_error','error':str(e)}


def scan_source(source,requests,links,max_rows):
    stats={'rows_scanned':0,'complete':False,'invalid_rows':[]}
    out={'source':source,'scan':stats,'matches':[]}
    if not source['exists']:out['status']='missing';return out
    try:
        for row,origin in rows(source['path'],source['kind'],max_rows,stats):
            for request in requests:
                if matches(row,source['kind'],request,links):
                    method='exact_source_identity'
                    if source['kind']=='wiki_pages' and not row.get('qid'):method='exact_language_page_id_from_sitelink'
                    out['matches'].append({'request':request,'record':row,'provenance':origin,'association_method':method})
        expected={k:source[k] for k in ('path','exists','size','mtime_ns','inode')}
        if snapshot(source['path'])!=expected:raise ValueError('source changed during scan; use a new snapshot')
        out['status']='scanned' if stats['complete'] else 'budget_limited'
    except (OSError,ValueError,EOFError) as e:
        out['status']='read_error';out['error']=str(e)
        # Incomplete/changed-source reads are diagnostic only, never material inputs.
        out['matches']=[]
    return out
