"""Material identities and typed source conversion shared by ingestion and migration."""
import hashlib
import json
from .material_schema import SOURCE


def sha(value):
    return hashlib.sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()


def source_record(record, *, system, source_file='', source_row=0):
    r=dict(record)
    concepts=r.pop('concepts',None) or r.pop('instances',None) or []
    if isinstance(concepts,str): concepts=[concepts]
    out={'source_system':system,'source_file':source_file,'source_row':source_row,
         'concepts':sorted(set(concepts))}
    mapping={'declared_width':'width','declared_height':'height','original_width':'orig_width',
             'original_height':'orig_height','declared_bytes':'size_bytes','declared_mime':'mime',
             'query_languages':'query_langs'}
    for field in SOURCE:
        key=field.name
        if key in out or key in {'source_record_id','attributes_json'}: continue
        value=r.pop(mapping.get(key,key),None)
        if key=='fetched_at' and value is not None: value=str(value)
        if key in ('queries','query_languages'):
            value={str(k):str(v) for k,v in (value or {}).items()}
        if key=='identity' and value is not None: value=bool(value)
        out[key]=value
    out['attributes_json']=json.dumps(r,ensure_ascii=False,sort_keys=True,default=str)
    out['source_record_id']=sha(json.dumps(out,ensure_ascii=False,sort_keys=True,default=str))
    return out


def document_identity(source_identity, content_sha, revision=None):
    return sha(json.dumps([source_identity,revision,content_sha],ensure_ascii=False))


def sections(values):
    if isinstance(values,str): values=json.loads(values)
    result=[]
    for value in values or []:
        if not isinstance(value,dict): value={'text':str(value)}
        v=dict(value)
        result.append({'title':str(v.pop('title','') or ''),'text':str(v.pop('text','') or ''),
                       'attributes_json':json.dumps(v,ensure_ascii=False,sort_keys=True)})
    return result


def document_images(values):
    if isinstance(values,str): values=json.loads(values)
    result=[]
    for i,value in enumerate(values or []):
        v=dict(value) if isinstance(value,dict) else {'value':value}
        result.append({'sha256':v.pop('sha256',None),'url':v.pop('url',None),
            'position':i,'caption':v.pop('caption',None),
            'attributes_json':json.dumps(v,ensure_ascii=False,sort_keys=True)})
    return result


def generation_origin(record):
    """A known generated source cannot be erased by merging source associations."""
    origins=[record.get('generation_origin')]
    for source in record.get('sources',[]):
        origins.append(json.loads(source.get('attributes_json') or '{}').get('generation_origin'))
    for origin in origins:
        if origin in {'generated','synthetic','ai_generated'}:return origin
    return next((o for o in origins if o),'not_verified')
