"""Freeze a same-case boundary and read-only export of existing preannotations.

SQLite is solely the existing preannotation store, never the pipeline engine.
No new model calls; never alter historical identity judgments or source data.
"""
import argparse,json,sqlite3
from pathlib import Path
from .contracts import digest,immutable,source_code,runtime_version,run_lock
from .ops.knowledge_stages import material_id
from .ops.operators import verify_image

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['identity','cleaned','annotations','dataset','run']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    with run_lock(a.run):
        source=[json.loads(l) for l in a.identity.open()]
        clean={r['material_id']:r['document'] for r in map(json.loads,a.cleaned.open())}
        protocol=json.loads((a.annotations/'protocol.json').read_text())
        db=sqlite3.connect(f'file:{(a.annotations/"annotations.sqlite").resolve()}?mode=ro',uri=True)
        db.execute('BEGIN')
        exports=[]
        for row in source:
            mapping={};materials=[]
            for orig in row['cleaned_materials']:
                m=dict(orig);old=material_id(m)
                if old in clean:
                    doc=clean[old]
                    if doc['raw_sha256']!=m['document']['sha256']:raise ValueError('raw document version differs')
                    m['record']=doc
                    m['cleaning']={**m['cleaning'],'text':doc['clean_text'],'blocks':doc['clean_blocks'],'counts':doc['clean_counts'],
                        'status':doc['clean_status'],'warnings':doc['clean_warnings'],'version':doc['clean_version'],
                        'extraction':doc['clean_extraction'],'media':doc['source_media'],'supplements':doc['source_supplements']}
                if m['kind'] in {'legacy_images','qid_images'}:
                    m['bytes']=verify_image(m['record'],a.dataset,[])
                    sha=m['record'].get('sha256');d=db.execute('SELECT description,status FROM images WHERE sha=?',(sha,)).fetchone()
                    annotation={'sha256':sha,'description':json.loads(d[0]) if d and d[0] else None,
                        'status':d[1] if d else 'not_in_annotation_snapshot',
                        'concept_matches':[json.loads(x[0]) for x in db.execute('SELECT result FROM concepts WHERE sha=? AND result IS NOT NULL ORDER BY name',(sha,))],
                        'author_type':'model','human_reviewed':False,'protocol':protocol}
                    m['record']={**m['record'],'preannotation':annotation};exports.append(annotation)
                new=material_id(m);mapping[old]=new;m['material_id']=new;materials.append(m)
            previous=row['identity'];identity={**previous,'accepted_material_ids':[mapping.get(x,x) for x in previous['accepted_material_ids']]}
            for field in ['rejected_materials','material_reviews']:
                identity[field]=[{**x,'material_id':mapping.get(x.get('material_id'),x.get('material_id'))} for x in previous.get(field,[])]
            old_ids={x['material_id'] for x in row['identity_materials']}
            row['cleaned_materials']=materials
            row['identity_materials']=[m for m in materials if m['material_id'] in {mapping[x] for x in old_ids if x in mapping}]
            row['identity']=identity;row['identity_boundary_provenance']={'source':str(a.identity),'original_identity':previous,
                'material_id_mapping':mapping,'scope':'Reused text identity; raw documents identical, cleaned representation updated; images reviewed separately'}
        db.rollback();db.close()
        manifest={'identity_sha256':digest(a.identity.read_bytes()),'cleaned_sha256':digest(a.cleaned.read_bytes()),
                  'annotation_protocol':protocol,'annotation_export_sha256':digest(exports),'code':source_code(),'runtime':runtime_version()}
        immutable(a.run/'manifest.json',manifest)
        for name,items in [('identity.jsonl',source),('image_annotations.jsonl',exports)]:
            dest=a.run/name;text=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in items)
            if dest.exists() and dest.read_text()!=text:raise ValueError('Frozen boundary changed')
            if not dest.exists():dest.write_text(text)
        print(a.run/'identity.jsonl')
if __name__=='__main__':main()
