"""Explicit transport ingestion into entity tables (never a runtime file fallback).

JSONL records provide concepts and source fields. Image records additionally have
an explicit local bytes_path; document records contain source_identity and text.
"""
import argparse
from pathlib import Path
import json
import io
from PIL import Image,ImageOps
from collect.materials import sha,source_record,document_identity
from collect.material_writer import write_images,write_documents
from project import resolve_root


def ingest(records,kind,root):
    rows=[]
    for index,record in enumerate(records):
        r=dict(record);path=r.pop('bytes_path',None)
        source=source_record({k:v for k,v in r.items() if kind=='images' or k!='text'},
                             system=r.get('source') or 'transport_ingestion',source_row=index)
        if kind=='images':
            raw=Path(path).read_bytes() if path else None
            identity=sha(raw) if raw is not None else r['sha256']
            if r.get('sha256') and identity!=r['sha256']:raise ValueError('Transport SHA mismatch')
            resolution=None
            if raw is not None:
                with Image.open(io.BytesIO(raw)) as image:
                    oriented=ImageOps.exif_transpose(image)
                    w,h=oriented.size
                    resolution={'width':w,'height':h,'stored_width':image.width,'stored_height':image.height,
                        'megapixels':w*h/1e6,'aspect_ratio':w/h,'orientation_basis':'exif_transposed',
                        'measurement_source':'transport_ingestion'}
                    ext=(image.format or '').lower()
            else:ext=r.get('ext') or 'unknown'
            rows.append({'sha256':identity,'data':raw,'ext':ext,'byte_size':len(raw) if raw is not None else 0,
                'storage_mode':'lance_blob','concepts':source['concepts'],'sources':[source],
                'availability':'available' if raw is not None else 'metadata_only','resolution':resolution})
        else:
            identity=r.get('source_identity') or r['url'];text=r.get('text');digest=sha(text) if text is not None else None
            rows.append({'document_id':document_identity(identity,digest,r.get('revision_id')),
                'document_type':'web','language':r.get('language'),'title':r.get('title'),
                'source_identity':identity,'revision_id':r.get('revision_id'),
                'content_sha256':digest,'text':text,'sections':[],'concepts':source['concepts'],
                'sources':[source],'images':[],'content_status':'available' if text is not None else 'metadata_only'})
    if kind=='images':write_images(root,rows)
    else:write_documents(root,rows)


def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['images','documents']);p.add_argument('input',type=Path);p.add_argument('--root',type=Path,default=resolve_root())
    args=p.parse_args();batch=[]
    with args.input.open() as stream:
        for line in stream:
            if not line.strip():continue
            batch.append(json.loads(line))
            if len(batch)>=128:ingest(batch,args.kind,args.root);batch=[]
    if batch:ingest(batch,args.kind,args.root)
if __name__=='__main__':main()
