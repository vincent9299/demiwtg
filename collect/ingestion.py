"""Business conversion from collector results to the current material entities."""
import json
from .materials import source_record, sha, document_identity, sections, document_images


def downloaded_image(row, data, *, system):
    names = row.get('concepts') or ([row['name']] if row.get('name') else [])
    source = source_record({**row, 'concepts':names}, system=system)
    width, height = row.get('actual_width'), row.get('actual_height')
    resolution = None
    if width and height:
        resolution = dict(width=width, height=height, stored_width=width, stored_height=height,
            megapixels=width*height/1e6, aspect_ratio=width/height,
            orientation_basis='decoded_pixels', measurement_source='collection_decoder')
    return dict(sha256=sha(data), ext=row['ext'], byte_size=len(data), storage_mode='lance_blob',
        data=data, concepts=sorted(set(names)), sources=[source], availability='available', resolution=resolution)


def collected_document(row):
    text = row['text']
    source_identity = row.get('page_url') or row.get('url') or 'offline:' + sha(text)
    content_sha = sha(text)
    names = row.get('concepts') or ([row['name']] if row.get('name') else [])
    source = source_record({k:v for k,v in {**row, 'concepts':names}.items()
        if k not in {'text','passages'}}, system=row.get('source') or 'web_collection')
    images = [dict(sha256=i.get('sha256'), url=i.get('src'), caption=i.get('alt'))
              for p in row.get('passages', []) for i in p.get('images', [])]
    return dict(document_id=document_identity(source_identity,content_sha,row.get('revision_id')),
        document_type='web', language=row.get('lang'), title=row.get('title'),
        source_identity=source_identity, revision_id=row.get('revision_id'),
        content_sha256=content_sha, text=text, sections=[], concepts=sorted(set(names)),
        sources=[source], images=document_images(images), content_status='available')


def wiki_document(row):
    structured = sections(row.get('sections'))
    text = '\n\n'.join((s['title']+'\n'+s['text']).strip() for s in structured)
    source_identity = f"wiki:{row['lang']}:{row['page_id']}"
    revision = str(row['revision_id']) if row.get('revision_id') is not None else None
    known = {'sections','images','links','categories'}
    source = source_record({k:v for k,v in row.items() if k not in known}, system='wiki_dump')
    return dict(document_id=document_identity(source_identity,sha(text),revision), document_type='wiki',
        language=row['lang'], title=row['title'], source_identity=source_identity, revision_id=revision,
        content_sha256=sha(text), source_content_sha256=row.get('text_sha256'), text=None,
        sections=structured, concepts=[], sources=[source], images=document_images(row.get('images')),
        content_status='available', page_id=str(row['page_id']), qid=row.get('qid'),
        parser_version=row.get('parser_version'), is_redirect=row.get('is_redirect'),
        redirect_target=row.get('redirect_target'), is_disambig=row.get('is_disambig'),
        categories=row.get('categories') or [], links_json=json.dumps(row.get('links') or [],ensure_ascii=False),
        link_count=row.get('link_count'))
