"""Curated image/article schemas and fixed-source entity updates."""
from demiflow import data
import pyarrow as pa

PROVENANCE = pa.struct([
    ('run_id', pa.string()), ('config_id', pa.string()), ('config_path', pa.string()),
    ('model', pa.string()), ('source_file', pa.string()), ('source_row', pa.int64()),
    ('record_sha256', pa.string()), ('details_json', pa.large_string()),
])
DESCRIPTION = pa.struct([
    ('caption', pa.large_string()), ('representation', pa.string()),
    ('view_tags', pa.list_(pa.string())),
    ('objects', pa.list_(pa.struct([('name',pa.string()),('location',pa.string()),('visible_features',pa.large_string())]))),
    ('text_regions', pa.list_(pa.struct([('text',pa.large_string()),('location',pa.string()),('readability',pa.string())]))),
    ('observability_issues', pa.list_(pa.struct([('type',pa.string()),('location',pa.string()),('detail',pa.large_string())]))),
    ('uncertainties', pa.list_(pa.string())),
])
DESCRIPTION_RECORD = pa.struct([
    ('annotation_id', pa.string()), ('config_id',pa.string()), ('status',pa.string()),
    ('description', DESCRIPTION), ('attempts',pa.int64()), ('provenance', PROVENANCE),
])
CONCEPT_MATCH = pa.struct([
    ('annotation_id',pa.string()), ('concept',pa.string()), ('config_id',pa.string()),
    ('status',pa.string()), ('reason',pa.large_string()), ('provenance',PROVENANCE),
])
SUPPORT = pa.struct([('supports',pa.large_string()),('region',pa.string()),('limitations',pa.large_string())])
ASSESSMENT = pa.struct([
    ('assessment_id',pa.string()), ('concept',pa.string()), ('image_id',pa.string()),
    ('review_status',pa.string()), ('published',pa.bool_()), ('release_ids',pa.list_(pa.string())),
    ('identity_relation',pa.string()), ('reason',pa.large_string()),
    ('visual_support',SUPPORT), ('description',DESCRIPTION),
    ('human_reviewed',pa.bool_()), ('provenance',PROVENANCE),
    # Full model reviews/call traces remain execution evidence, not query keys.
    ('review_json',pa.large_string()), ('observation_json',pa.large_string()),
])
IMAGE_CURATION = pa.schema([
    ('descriptions',pa.list_(DESCRIPTION_RECORD)),
    ('concept_matches',pa.list_(CONCEPT_MATCH)),
    ('concept_assessments',pa.list_(ASSESSMENT)),
    ('published_concepts',pa.list_(pa.string())),
    ('release_ids',pa.list_(pa.string())),
])

# No source bytes, acquisition metadata or duplicate raw concepts in this table.
IMAGES_URI = 'demiwtg/preparation/datasets/images.lance'
IMAGES = pa.schema([
    pa.field('sha256', pa.string(), nullable=False),
    ('source_refs', pa.list_(pa.large_string())),
    ('concepts', pa.list_(pa.string())),
    *IMAGE_CURATION,
])


REP=set('photo illustration diagram flowchart map chart document screenshot mixed unknown'.split())

VIEWS=set('front side top oblique closeup interior cross_section exploded multi_panel stage_comparison unknown'.split())

ISSUES=set('blur occlusion cropping small_text watermark glare other'.split())

def nonempty(x):return isinstance(x,str) and bool(x.strip())

def validate_description(x):
    if not isinstance(x,dict) or not nonempty(x.get('caption')) or x.get('representation') not in REP:raise ValueError('invalid caption/representation')
    tags=x.get('view_tags')
    if not isinstance(tags,list) or not tags or any(t not in VIEWS for t in tags):raise ValueError('invalid views')
    for key,limit,fields in [('objects',8,('name','location','visible_features')),('text_regions',12,('location',)),('observability_issues',20,('location','detail'))]:
        rows=x.get(key)
        if not isinstance(rows,list) or len(rows)>limit:raise ValueError('invalid '+key)
        for row in rows:
            if not isinstance(row,dict) or any(not nonempty(row.get(f)) for f in fields):raise ValueError('invalid '+key+' item')
    for r in x['text_regions']:
        if not isinstance(r.get('text'),str) or r.get('readability') not in ('readable','partial','unreadable'):raise ValueError('invalid OCR')
        if r['readability']!='unreadable' and not nonempty(r['text']):raise ValueError('empty readable OCR')
    for r in x['observability_issues']:
        if r.get('type') not in ISSUES:raise ValueError('invalid issue type')
    if not isinstance(x.get('uncertainties'),list) or any(not nonempty(v) for v in x['uncertainties']):raise ValueError('invalid uncertainty')


import hashlib
import json
from pathlib import Path


CONFIG_PATH = 'preparation/prompts/image_annotation_v2.json'

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def description(value):
    if value is None: return None
    result = dict(value)
    for key in ('objects', 'text_regions', 'observability_issues'):
        if isinstance(result.get(key),str): result[key] = json.loads(result[key])
    return pa.array([result],type=DESCRIPTION)[0].as_py()

def provenance(row, *, run_id=None, config_id=None, config_path=None):
    return dict(run_id=run_id, config_id=config_id, config_path=config_path,
        source_file=row.get('source_file'), source_row=row.get('source_row'),
        record_sha256=identity(row), details_json=canonical(row))


def assessment(record, release_id, *, source_file=None, source_row=None, run_id=None):
    review=record.get('concept_review') or {}
    published=record.get('publication_status')=='reviewed'
    if published and review.get('decision')!='keep':
        raise ValueError('Publication contradicts concept review')
    metadata=record.get('image_metadata') or {}
    observation={k:v for k,v in record.items() if k not in ('concept_review','visual_support')}
    return dict(assessment_id=identity([release_id,record['concept'],record['sha256']]),
        concept=record['concept'],image_id=record.get('image_id'),
        review_status=review.get('decision') or 'unknown',published=published,
        release_ids=[release_id],identity_relation=review.get('concept_relation'),reason=review.get('reason'),
        visual_support=record.get('visual_support'),description=description(metadata.get('description')),
        human_reviewed=record.get('human_reviewed',False),
        provenance=provenance({'source_file':source_file,'source_row':source_row},run_id=run_id),
        review_json=canonical(review),observation_json=canonical(observation))

def assessment_record(image, item):
    """Project one concept assessment into the business visual-publication contract."""
    record=json.loads(item['observation_json'])
    record.update(sha256=image['sha256'],concept=item['concept'],
        publication_status='reviewed' if item['published'] else 'not_published',
        concept_review=json.loads(item['review_json']), visual_support=item['visual_support'])
    return record

def merged_records(old, new, key):
    result={r[key]:r for r in old or []}
    for row in new or []:
        previous=result.get(row[key])
        if previous is not None and previous!=row:raise ValueError('Conflicting image attribute: '+str(row[key]))
        result[row[key]]=row
    return [result[k] for k in sorted(result)]

def project_image(row):
    """Search lists are projections; callers never update them independently."""
    result=dict(row)
    for name in ('descriptions','concept_matches','concept_assessments'):
        result[name]=result.get(name) or []
    assessments=result['concept_assessments']
    result['concepts']=sorted(set(result.get('concepts') or []) |
        {r['concept'] for r in result['concept_matches']} | {r['concept'] for r in assessments})
    result['published_concepts']=sorted({r['concept'] for r in assessments if r['published']})
    result['release_ids']=sorted({rid for r in assessments for rid in r['release_ids']})
    return result

def merge_curation(previous, incoming):
    out=dict(previous)
    for field,key in [('descriptions','annotation_id'),('concept_matches','annotation_id'),('concept_assessments','assessment_id')]:
        out[field]=merged_records(previous.get(field),incoming.get(field),key)
    return project_image(out)

def latest_images(root, target_uri=IMAGES_URI):
    from demiflow.lance.registry import Catalog
    refs=[r for r in Catalog(root).registered() if r.resolve(root)==str(Path(root)/target_uri)]
    if not refs:raise ValueError('Curated image table is not registered')
    return max(refs,key=lambda r:r.lance_version)



def clean_image_delivery(row, raw, source_ref):
    """在 preparation 出口统一来源和公开可用状态；下游无需重读审核/来源 JSON。"""
    from collect.materials import generation_origin
    assessments = []
    for item in row.get('concept_assessments') or []:
        observation = json.loads(item.get('observation_json') or '{}')
        source = observation.get('source') or {}
        origins = [generation_origin(raw), generation_origin(source),
                   (observation.get('bytes') or {}).get('generation_origin')]
        origin = next((v for v in origins if v in {'generated', 'synthetic', 'ai_generated'}),
                      next((v for v in origins if v and v != 'not_verified'), 'not_verified'))
        # 实际原始表绑定是来源依据；URL 缺失不等于来源不明，不伪造外部来源。
        observation['source'] = {**source, 'sources': raw.get('sources') or [],
                                 'generation_origin': origin, 'dataset_ref': source_ref.to_dict()}
        error = None
        if origin in {'generated', 'synthetic', 'ai_generated'}:
            error = 'Known generated image is not a knowledge reference'
        elif item.get('published'):
            review = json.loads(item.get('review_json') or '{}')
            support = item.get('visual_support') or {}
            if item.get('review_status') != 'keep' or review.get('decision') != 'keep':
                error = 'Publication contradicts concept review'
            elif not (support.get('supports') or '').strip() or not (support.get('region') or '').strip():
                error = 'Published image lacks visual support or region'
        assessments.append({**item, 'observation_json': canonical(observation),
                            **({'published': False, 'review_status': 'reject', 'reason': error} if error else {})})
    return project_image({**row, 'concept_assessments': assessments})

def write_curation(root, rows, *, source_ref, target_uri=None, write_mode='merge'):
    """写图片审核结果并绑定原图版本；merge 合并主键，append/overwrite 直接追加或覆盖。"""
    if write_mode not in {'merge', 'append', 'overwrite'}:
        raise ValueError('write_mode must be merge, append or overwrite')
    from demiflow.lance.transaction import registered_table_edit
    from demiflow.lance.refs import DatasetRef
    import lance
    # 目标表和模式独立配置；所有模式均不改变原始图片字节表。
    target_uri = str((Path(root) / (target_uri or IMAGES_URI)).resolve().relative_to(Path(root).resolve()))
    source_ref = source_ref if isinstance(source_ref,DatasetRef) else DatasetRef.from_dict(source_ref)
    if source_ref.schema_name != 'raw_images':
        raise ValueError('Curation requires a fixed raw image DatasetRef')
    source=source_ref.open(root)
    binding=canonical(source_ref.to_dict())
    grouped={}
    for row in rows:
        row={**row,**pa.Table.from_pylist([row],schema=IMAGE_CURATION).to_pylist()[0]}
        key=row['sha256']
        if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('Invalid image SHA')
        grouped[key]=merge_curation(grouped.get(key,{'sha256':key,'concepts':[]}),row)
    if not grouped and write_mode == 'merge':return latest_images(root, target_uri)
    keys=', '.join("'"+k+"'" for k in grouped)
    raw_rows = {r['sha256']: r for r in source.to_table(
        columns=['sha256', 'sources'], filter=f'sha256 IN ({keys})').to_pylist()} if grouped else {}
    if set(raw_rows) != set(grouped):raise ValueError('Curated images absent from the fixed raw source')
    with registered_table_edit(root,target_uri,schema_name='curated_images',schema_version='v1') as ds:
        existing={r['sha256']:r for r in ds.to_table(filter=f'sha256 IN ({keys})').to_pylist()} if ds and write_mode == 'merge' else {}
        changed=[]
        for key,row in grouped.items():
            prior=existing.get(key,{'sha256':key,'concepts':[]})
            # 同一来源版本下先规范化新旧记录，再比较同一 assessment，保证重复导出幂等。
            prior = clean_image_delivery(prior, raw_rows[key], source_ref)
            row = clean_image_delivery(row, raw_rows[key], source_ref)
            merged=merge_curation(prior,row)
            merged['source_refs']=sorted(set(prior.get('source_refs') or [])|{binding})
            if merged!=existing.get(key):changed.append(merged)
        uri=str(Path(root)/target_uri)
        if changed or write_mode != 'merge':
            table=pa.Table.from_pylist(changed,schema=IMAGES)
            if write_mode != 'merge':lance.write_dataset(table,uri,mode=write_mode)
            elif ds is None:lance.write_dataset(table,uri)
            else:ds.merge_insert('sha256').when_matched_update_all().when_not_matched_insert_all().execute(table)
        if (ds is None or write_mode == 'overwrite') and changed:
            current=lance.dataset(uri)
            for col,kind in [('sha256','BTREE'),('concepts','LABEL_LIST'),('published_concepts','LABEL_LIST'),('release_ids','LABEL_LIST')]:
                current.create_scalar_index(col,kind)
                current=lance.dataset(uri)
    return latest_images(root, target_uri)


from collect.assets import (
    AssetCorrupted, AssetError, AssetMissing, AssetReadError,
    default_reader,
)


def bytes_unchanged(path, sha256) -> bool:
    """冻结字节复核：路径提示或湖内读取成功且内容 SHA 一致。

    供 observed/断言类校验使用；缺失、损坏、读失败均返回 False，
    不抛错（调用方据此标记 changed_or_missing）。
    """
    try:
        data, _source = asset_bytes(path, sha256)
        return data is not None
    except (AssetMissing, AssetCorrupted, AssetReadError, AssetError, OSError, ValueError):
        return False


def asset_resolution(path, sha256, ext=None):
    """The historical path is provenance only. Authoritative pixels live in Lance."""
    return default_reader().resolve(sha256, ext)


def asset_bytes(path, sha256, ext=None):
    """读取并验证字节；返回 (bytes, source 身份 dict)。"""
    resolution = asset_resolution(path, sha256, ext)
    if resolution.status == "missing":
        raise AssetMissing(f"asset bytes not found: {sha256}")
    if resolution.status == "corrupt":
        raise AssetCorrupted(
            f"Image bytes changed: expected {sha256}, got {resolution.actual_sha256}")
    if resolution.status == "read_error":
        raise AssetReadError(resolution.error)
    return resolution.data, resolution.source


def asset_file(path, sha256, ext=None):
    """Rebuild a disposable file for tools that require a local path."""
    suffix = ext or (Path(path).suffix.lstrip('.').lower() if path else None)
    return default_reader().materialize(sha256, suffix or 'bin')


import struct
from PIL import ImageOps


def oriented_pixels(source):
    # Pixel decoding failures must still fail; only optional metadata can fall back.
    source.load()
    try:
        return ImageOps.exif_transpose(source), None
    except (SyntaxError, ValueError, struct.error) as error:
        return source.copy(), {'operation': 'exif_transpose',
            'status': 'invalid_metadata_kept_stored_orientation',
            'error': f'{type(error).__name__}: {error}'}


def inspect_lake_image(record):
    """Inspect only the authoritative Lance bytes of a collected source image."""
    import io
    from collect.materials import generation_origin
    from PIL import Image
    sha = record.get('sha256')
    if not sha:
        return {'status': 'missing_expected_hash'}
    result = asset_resolution(record.get('path'), sha)
    if result.status != 'ok':
        return {'status': {'missing':'not_local', 'corrupt':'hash_mismatch',
                           'read_error':'read_error'}[result.status],
                'sha256':sha, 'error':result.error, 'asset_source':result.source}
    try:
        with Image.open(io.BytesIO(result.data)) as image:
            image.load()
            oriented, warning = oriented_pixels(image)
            width, height = oriented.size
            resolution = {'width':width, 'height':height, 'stored_width':image.width,
                'stored_height':image.height, 'megapixels':round(width*height/1e6,6),
                'aspect_ratio':round(width/height,6), 'metadata_warning':warning,
                'orientation_basis':'stored' if warning else 'exif_transposed'}
            return {'status':'verified_bytes', 'path':record.get('path'), 'sha256':sha,
                    'dimensions':list(image.size), 'format':image.format,
                    'resolution':resolution, 'byte_size':len(result.data),
                    'asset_source':result.source, 'knowledge_support':'not_reviewed',
                    'generation_origin':generation_origin(record)}
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        return {'status':'decode_error', 'sha256':sha, 'error':str(error)}


import base64, io, json
from PIL import Image
from demiflow.execution.artifacts import digest
from preparation.operaters.identity import material_id


def pixels(images,max_edge=1536):
    urls=[];roles=[]
    for item in images:
        # 路径只是定位提示；统一按 sha 经资产解析读取（文件或湖内 Lance Blob）。
        raw,_origin=asset_bytes(item['bytes'].get('path'),item['record']['sha256'])
        if digest(raw)!=item['record']['sha256']:raise ValueError('Image changed before model call')
        with Image.open(io.BytesIO(raw)) as source:
            oriented,metadata_warning=oriented_pixels(source)
            pic=oriented.convert('RGBA')
            background=Image.new('RGBA',pic.size,'white');background.alpha_composite(pic)
            pic=background.convert('RGB');pic.thumbnail((max_edge,max_edge))
            out=io.BytesIO();pic.save(out,format='JPEG',quality=90)
        urls.append('data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode())
        roles.append({'image_id':item['image_id'],'original_sha256':digest(raw),'input_sha256':digest(out.getvalue()),
                      'max_edge':max_edge,'frame':0,'dimensions':list(pic.size)})
        if metadata_warning:roles[-1]['metadata_warning']=metadata_warning
    return urls,roles

class SelectAvailableImages:
    """All source-associated images, independent of text-only identity decisions."""
    def __call__(self,row):
        images=[];pending=[];duplicates=[];seen={}
        for material in row['cleaned_materials']:
            if material['kind'] not in {'legacy_images','qid_images'}:continue
            m=dict(material);m['material_id']=material_id(m);m['image_id']='I'+m['material_id'][1:]
            sha=m['record'].get('sha256');status=m.get('bytes',{}).get('status')
            if status!='verified_bytes':
                pending.append({'material_id':m['material_id'],'status':status or 'not_checked','reason':'Byte availability, not relevance rejection'});continue
            if sha in seen:
                duplicates.append({'material_id':m['material_id'],'representative_image_id':seen[sha],'sha256':sha});continue
            seen[sha]=m['image_id'];images.append(m)
        # Prefer complementary observable views for first batches; no image is discarded by this order.
        images.sort(key=lambda m:(json.dumps(((m['record'].get('preannotation') or {}).get('description') or {}).get('view_tags',[])),m['image_id']))
        return {**row,'available_images':images,'image_material_scope':{'pending':pending,'exact_duplicates':duplicates,
                'selection':'All byte-verified associated images; metadata-only identity rejection not treated as pixel review'}}

class BatchImageSelection:
    def __init__(self,batch_size=4,identity_definitions=None,neutral=False,visual_publication=False):
        if batch_size<1:raise ValueError('image batch size must be positive')
        self.batch_size=batch_size
        self.identity_definitions=identity_definitions or {}
        self.neutral=neutral
        self.visual_publication=visual_publication
    def __call__(self,row):
        if row.get('blocked'):return []
        out=[]
        for i in range(0,len(row['available_images']),self.batch_size):
            images=row['available_images'][i:i+self.batch_size];urls,roles=pixels(images)
            out.append({'case_id':row['case_id'],'batch_id':f"{row['case_id']}:images:{i}",
                'image_prompt':{'concept':row['identity']['target_label'],'identity_context':{k:row['identity'].get(k) for k in ['reason','identity_groups']},'selection_protocol':'image-relevance-v2','image_ids':[m['image_id'] for m in images],
                    'metadata':[{'image_id':m['image_id'],'source_caption':m['record'].get('caption'),'source_title':m['record'].get('title'),
                                 'preannotation':m['record'].get('preannotation')} for m in images]},
                'pixel_images':urls,'pixel_roles':roles})
            if self.neutral:
                # Concept records describe the identity; image captions and previous
                # model acceptance/rejection are not independent visual evidence.
                definition=self.identity_definitions.get(row.get('concept_ref'))
                records=[{k:m['record'][k] for k in ['name','aliases','qid','scientific_name','description','knowledge_intro'] if k in m['record']}
                         for m in row.get('cleaned_materials',[]) if m['kind'] in {'legacy_concepts','qid_concepts'}]
                out[-1]['image_prompt']['identity_context']=({'definition':definition} if definition else {'concept_records':records})
                out[-1]['image_prompt'].pop('metadata',None)
            if self.visual_publication:
                index = {m['image_id']: index_metadata(m) for m in images}
                out[-1]['image_index'] = index  # Not sent to either reviewer.
                out[-1]['image_prompt'].update(selection_protocol=VISUAL_PROTOCOL,
                    metadata_required_ids=[iid for iid, item in index.items() if item['status']=='missing'])
        return out

class ApplyImageSelection:
    def __call__(self,row):
        wanted=row['image_prompt']['image_ids'];result=row.get('prompt_result') or {};items=result.get('images',[])
        decisions=[]
        for iid in wanted:
            matches=[x for x in items if isinstance(x,dict) and x.get('image_id')==iid] if isinstance(items,list) else []
            valid=(not row.get('prompt_error') and len(matches)==1 and matches[0].get('relation') in {'direct','background','unrelated','uncertain'}
                   and matches[0].get('observability') in {'usable','limited','unusable'}
                   and all(isinstance(matches[0].get(k),str) and matches[0][k].strip() for k in ['reason','visible_information'])
                   # Empty/null supplementary limitations do not invalidate an otherwise
                   # complete observation. Preserve them; never fabricate an assurance.
                   and ('limitations' in matches[0] or row['image_prompt'].get('selection_protocol') == 'concept-visual-publication/1')
                   and (matches[0].get('limitations') is None or isinstance(matches[0]['limitations'],str)))
            if valid and row['image_prompt'].get('selection_protocol') in {'image-relevance-v2', 'concept-visual-publication/1'}:
                item=matches[0];relation=item.get('concept_relation');decision=item.get('decision')
                expected={'target':'direct','related_activity':'background','text_only':'unrelated','namesake':'unrelated','unrelated':'unrelated','uncertain':'uncertain'}
                valid=(relation in expected and item['relation']==expected[relation]
                       and decision in {'keep','exclude','pending'}
                       and ((relation in {'text_only','namesake','unrelated'} and decision=='exclude')
                            or (relation=='uncertain' and decision=='pending')
                            or (relation in {'target','related_activity'} and decision==('pending' if item['observability']=='unusable' else 'keep'))))
            if valid and row['image_prompt'].get('selection_protocol') == 'concept-visual-publication/1':
                valid = valid_visual_result(matches[0], row['image_prompt'])
                if valid and 'limitations' not in matches[0]:
                    # V2 has an explicit support-scoped limitation. Do not require
                    # the model to duplicate it at the legacy top level.
                    matches = [{**matches[0], 'limitations':(matches[0].get('visual_support') or {}).get('limitations')}]
            decisions.append({**matches[0],'protocol_valid':True} if valid else {'image_id':iid,'relation':'uncertain',
                'observability':'unknown','decision':'pending','concept_relation':'uncertain','reason':'Missing/invalid image selection response','protocol_valid':False})
        return {'case_id':row['case_id'],'image_decisions':decisions,
                'image_selection_calls':[{'call':row.get('prompt_call'),'error':row.get('prompt_error'),'result':result,'pixel_roles':row['pixel_roles']}]}

def merge_image_decisions(a,b):
    return b if a is None else {'case_id':b['case_id'],'image_decisions':a['image_decisions']+b['image_decisions'],
                                'image_selection_calls':a['image_selection_calls']+b['image_selection_calls']}

class SelectRelatedMaterials:
    def __call__(self,row):
        decisions={x['unit_id']:x for x in row.get('block_decisions',[])}
        passages=[u for u in row.get('source_units',[]) if decisions.get(u['unit_id'],{}).get('decision')=='selected']
        images=[];ids={x['image_id']:x for x in row.get('image_decisions',[])}
        for image in row.get('available_images',[]):
            d=ids.get(image['image_id'],{})
            if d.get('protocol_valid') and d.get('decision','keep')=='keep' and d.get('relation')!='unrelated' and d.get('observability') in {'usable','limited'}:
                images.append({**image,'selection_review':d})
        return {**row,'material_pack':{'passages':passages,'images':images,'duplicates':row.get('image_material_scope',{}).get('exact_duplicates',[]),
            'omissions':[{'source_id':u['source_id'],'decision':decisions.get(u['unit_id'],{}),'reason':'not_selected_for_joint_input'}
                         for u in row.get('source_units',[]) if u not in passages],
            'image_gaps':row.get('image_material_scope',{}).get('pending',[]),
            'excluded_images':[d for d in ids.values() if d.get('decision')=='exclude'],
            'pending_images':[d for d in ids.values() if d.get('decision')=='pending']}}


from demiflow.execution.artifacts import digest, immutable

from collect.assets import AssetCorrupted, AssetMissing

VISUAL_PROTOCOL = 'concept-visual-publication/1'


def description_valid(value):
    try:
        validate_description(value)
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def index_metadata(image):
    """Reuse SHA-bound labels without promoting them to reviewed knowledge."""
    annotation = image.get('record', {}).get('preannotation') or {}
    sha = image.get('bytes', {}).get('sha256') or image.get('record', {}).get('sha256')
    technical = {k:image['bytes'][k] for k in ('resolution','byte_size','format','dimensions')
                 if k in image.get('bytes', {})}
    if annotation.get('sha256') == sha and description_valid(annotation.get('description')):
        return {**technical, 'status': 'machine_index_only', 'description': annotation['description'],
                'provenance': {k: annotation[k] for k in
                    ('sha256', 'dataset_ref', 'config_id', 'model', 'status', 'record_sha256') if k in annotation}}
    return {**technical, 'status': 'missing', 'description': None}


class ReuseImageAnnotations:
    """Read SHA-bound machine descriptions from one fixed Lance table version."""
    def __init__(self, dataset_ref=None, config_id=None):
        self.config_id = config_id
        self.ref = dataset_ref
        self.dataset = None
        self.cache = {}
        if dataset_ref:
            from demiflow.lance.refs import DatasetRef
            from project import resolve_root
            ref = DatasetRef.from_dict(dataset_ref)
            if ref.schema_name != 'curated_images':
                raise ValueError('Expected a curated_images DatasetRef')
            self.dataset = ref.open(resolve_root())

    def __call__(self, row):
        sha = row.get('sha256') or row.get('byte_details', {}).get('sha256')
        if not sha or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            return {**row, 'preannotation': None, 'image_index_status': 'missing_sha256'}
        existing = row.get('preannotation')
        saved = {'sha256': sha, 'status': 'missing', 'description': None}
        if (existing and existing.get('sha256') == sha and description_valid(existing.get('description'))
                and (self.config_id is None or existing.get('config_id') == self.config_id)):
            saved = dict(existing)
        elif self.dataset is not None:
            if sha not in self.cache:
                records = self.dataset.to_table(columns=['sha256','descriptions'], filter=f"sha256 = '{sha}'").to_pylist()
                if len(records) > 1: raise ValueError('Duplicate annotation SHA: ' + sha)
                self.cache[sha] = records[0] if records else None
            record = self.cache[sha]
            if record:
                descriptions = [r for r in record.get('descriptions') or []
                                if r['status'] == 'done' and description_valid(r.get('description'))
                                and (self.config_id is None or r['config_id'] == self.config_id)]
                if len(descriptions) > 1:
                    raise ValueError('Multiple valid descriptions require an explicit annotation selection')
                if descriptions:
                    annotation = descriptions[0]
                    saved.update(status=annotation['status'], description=annotation['description'],
                                 dataset_ref=self.ref, config_id=annotation['config_id'],
                                 author_type='model', human_reviewed=False)
        saved.pop('record_sha256', None)
        saved['record_sha256'] = digest(saved)
        return {**row, 'preannotation': saved, 'image_index_status':
                'available' if description_valid(saved.get('description')) else 'missing'}


def valid_visual_result(item, prompt):
    metadata = item.get('image_metadata')
    if item['image_id'] in prompt.get('metadata_required_ids', []) and not description_valid(metadata):
        return False
    if metadata is not None and not description_valid(metadata):
        return False
    support = item.get('visual_support')
    if item.get('decision') != 'keep':
        return support is None
    return (isinstance(support, dict)
            and all(isinstance(support.get(k), str) and support[k].strip() for k in ('supports', 'region'))
            and isinstance(support.get('limitations'), str))


def confirmed_visual_review(row, primary, review):
    if row['image_prompt'].get('selection_protocol') != VISUAL_PROTOCOL:
        return None  # Old keep decisions cannot acquire the new publication status.
    roles = {r['image_id']: r for r in row.get('pixel_roles', [])}
    sha = roles.get(review['image_id'], {}).get('original_sha256')
    if not sha:
        return None
    metadata = row.get('image_index', {}).get(review['image_id'], {'status': 'missing'})
    if metadata.get('status') == 'missing':
        metadata = {**metadata, 'status': 'machine_index_only', 'description': review['image_metadata'],
                    'provenance': {'stage': 'independent_visual_review', 'sha256': sha}}
    return {'schema': VISUAL_PROTOCOL, 'status': 'reviewed', 'sha256': sha,
            'metadata': metadata, 'support': review['visual_support'],
            'identity_reason': review['reason'], 'visible_information': review['visible_information'],
            'primary_support': primary['visual_support'],
            'review_calls': row['primary_selection']['image_selection_calls'] +
                [{'call': row.get('prompt_call'), 'error': row.get('prompt_error'), 'pixel_roles': row.get('pixel_roles', [])}],
            'review_note': 'Two models accepted concept identity; support is the independent reviewer observation, not human certification.'}


class FinalizeVisualMaterials:
    """Release only explicitly reviewed, pixel-bound visual records."""
    def __call__(self, row):
        visuals, issues, seen = [], [], set()
        for image in row.get('material_pack', {}).get('images', []):
            decision = image.get('selection_review', {})
            publication = decision.get('visual_publication') or {}
            info = image.get('bytes', {})
            if (publication.get('schema') != VISUAL_PROTOCOL or publication.get('status') != 'reviewed'
                    or publication.get('sha256') != info.get('sha256')
                    or decision.get('decision') != 'keep' or not decision.get('protocol_valid')):
                issues.append({'image_id': image['image_id'], 'reason': 'No explicit V2 visual publication review'})
                continue
            pass
            try:
                _data, _origin = asset_bytes(info.get('path'), info['sha256'])
            except AssetMissing:
                issues.append({'image_id': image['image_id'], 'reason': 'Published visual bytes missing'})
                continue
            except AssetCorrupted:
                raise ValueError('Visual publication pixels changed')
            if info.get('generation_origin', image.get('record', {}).get('generation_origin')) in {'generated', 'synthetic', 'ai_generated'}:
                issues.append({'image_id': image['image_id'], 'reason': 'Known generated image is not a knowledge reference'})
                continue
            if info['sha256'] in seen:
                continue
            seen.add(info['sha256'])
            visuals.append({'image_id': image['image_id'], 'concept': row['identity']['target_label'],
                            'image': image, 'publication': publication})
        return {**row, 'visual_materials': visuals, 'visual_publication_issues': issues}


class ExportVisualMetadata:
    """Derived per-concept/image metadata, including unapproved candidates.

    Machine descriptions remain indexing hints. Publication status is separate.
    This never writes the authoritative dataset meta or the legacy annotation DB.
    """
    def __call__(self, row):
        from preparation.operaters.identity import material_id
        decisions = {d['image_id']:d for d in row.get('image_decisions', [])}
        publications = {v['image_id']:v['publication'] for v in row.get('visual_materials', [])}
        seen = set()
        for image in row.get('cleaned_materials', []):
            if image['kind'] not in {'legacy_images','qid_images'}:
                continue
            iid = 'I'+material_id(image)[1:]
            record, info = image['record'], image.get('bytes', {})
            sha = info.get('sha256') or record.get('sha256')
            if (sha or iid) in seen:
                continue
            seen.add(sha or iid)
            decision = decisions.get(iid, {})
            publication = publications.get(iid)
            metadata = index_metadata(image)
            if metadata['status'] == 'missing':
                # Even an excluded image may have useful machine indexing labels.
                for stage, value in [('independent_visual_review', decision.get('independent_review', decision)),
                                     ('primary_visual_review', decision.get('primary_review', decision))]:
                    if value.get('protocol_valid') and description_valid(value.get('image_metadata')):
                        metadata = {**metadata, 'status':'machine_index_only',
                                    'description':value['image_metadata'],
                                    'provenance':{'sha256':sha,'stage':stage}}
                        break
            yield {'schema':'visual-image-meta/1', 'pipeline_version':'V2',
                   'concept':row['identity']['target_label'], 'image_id':iid, 'sha256':sha,
                   'path':info.get('path'), 'byte_status':info.get('status'),
                   'resolution':info.get('resolution'), 'byte_size':info.get('byte_size'),
                   'format':info.get('format'), 'image_metadata':metadata,
                   'source':{k:record[k] for k in ('url','landing_url','content_url','license','author','sources') if record.get(k)},
                   'concept_review':decision, 'publication_status':'reviewed' if publication else 'not_published',
                   'visual_support':publication.get('support') if publication else None,
                   'human_reviewed':False, 'task_roles':None}


IMAGE_FILTER_DEFAULTS = {
    'image_review_model': 'gemma-4-31b-it',
    'image_review_base_url': 'http://127.0.0.1:8001/v1',
    'image_review_service': 'borrow',
    'image_identity_definitions': {},
    'image_filter_output_tokens': 8192,
    'image_review_concurrency': 2,
}
IMAGE_FILTER_POLICY = 'v2-qwen38-gemma31-visual-publication-1'


class RecordPrimaryImageSelection:
    def __call__(self, row):
        return {**row, 'primary_selection': ApplyImageSelection()(row)}


class PrepareImageReview:
    """Same pixels, IDs and neutral payload; primary decisions never enter the prompt."""
    def __call__(self, row):
        return {**{k: row[k] for k in ['case_id', 'batch_id', 'image_prompt', 'pixel_images', 'pixel_roles']},
                'image_index': row.get('image_index', {}),
                'primary_selection': row['primary_selection'],
                'review_required': any(d.get('protocol_valid') and d.get('decision') == 'keep'
                                       for d in row['primary_selection']['image_decisions'])}


class ApplyConfirmedImageSelection:
    def __call__(self, row):
        first = row['primary_selection']
        second = ApplyImageSelection()(row) if row['review_required'] else None
        by_id = {d['image_id']: d for d in second['image_decisions']} if second else {}
        decisions = []
        for primary in first['image_decisions']:
            if not primary.get('protocol_valid') or primary.get('decision') != 'keep':
                decisions.append(primary)
                continue
            review = by_id.get(primary['image_id'], {})
            if review.get('protocol_valid') and review.get('decision') == 'keep':
                final = dict(review)
                publication = confirmed_visual_review(row, primary, review)
                if publication:
                    final['visual_publication'] = publication
            else:
                final = {'image_id': primary['image_id'], 'decision': 'pending',
                         'concept_relation': 'uncertain', 'relation': 'uncertain',
                         'observability': review.get('observability', 'unknown'),
                         'protocol_valid': bool(review.get('protocol_valid')),
                         'reason': 'Independent image review disagreed or was unavailable; not selected for joint extraction.',
                         'visible_information': review.get('visible_information', ''),
                         'limitations': 'Identity/relevance remains unresolved.'}
            decisions.append({**final, 'primary_review': primary, 'independent_review': review})
        return {'case_id': row['case_id'], 'batch_id': row['batch_id'],
                'image_decisions': decisions,
                'image_selection_calls': first['image_selection_calls'] + (second['image_selection_calls'] if second else []),
                'image_filter_policy': IMAGE_FILTER_POLICY}


from preparation.operaters.runfiles import run_records, prompt_store
from preparation.prompts import material_prompt_pack, prompt_execution_options, save_prompt_config


def image_prompt_config(run, config, *, review=False):
    if config['model'] != 'qwen3.8-27b' or config['image_review_model'] != 'gemma-4-31b-it':
        raise ValueError('This image selection policy was validated for Qwen3.8 + Gemma31 only')
    cfg = {**config, 'temperature': 0, 'max_output_tokens': config['image_filter_output_tokens']}
    if review:
        cfg.update(model=config['image_review_model'], base_url=config['image_review_base_url'], local_model_comparison=True)
    pack, text = material_prompt_pack(cfg)
    options = prompt_execution_options(run, cfg)
    # 同一运行复用调用日志；请求上限由各 map_prompt_async 节点独立声明。
    options['lance_journal'] = prompt_store(run, 'calls')
    save_prompt_config(Path(run) / ('image_review' if review else 'image_primary'), text, options)
    return pack, options


def review_needed(requests, record, version):
    if record is not None:
        if record['fingerprint'] != version:
            raise ValueError('Image review inputs changed; use a new run')
        return False
    return next(requests.iter_rows(), None) is not None


def save_image_filter_policy(run, config):
    run_records(run).put('image_filter_policy', {
        'policy': IMAGE_FILTER_POLICY, 'primary_model': config['model'],
        'review_model': config['image_review_model'], 'batch_size': config.get('image_batch_size', 4),
        'identity_definitions': config['image_identity_definitions'], 'neutral_input': True})


from preparation.operaters.runfiles import run_records


class PrepareVisualInput:
    """Accept concept/images rows, including historical knowledge exports.

    Article text and old identity/filter decisions are deliberately not evidence.
    The new pixel review must establish the requested concept association.
    """
    def __init__(self, run, dataset, source, annotation_ref=None, annotation_config_id=None):
        self.run, self.dataset, self.source = Path(run), Path(dataset), source
        self.index = ReuseImageAnnotations(annotation_ref, annotation_config_id)

    def __call__(self, row):
        concept = row.get('concept')
        if not isinstance(concept, str) or not concept.strip() or not isinstance(row.get('images'), list):
            raise ValueError('Visual input requires concept and images[]')
        materials = []
        for image in row['images']:
            record = dict(image.get('record', image))
            prior = image.get('bytes', {})
            expected = record.get('sha256') or prior.get('sha256')
            if prior.get('sha256') and expected != prior['sha256']:
                raise ValueError('Conflicting source image hashes')
            record['sha256'] = expected
            checked = inspect_lake_image({**record, 'path':prior.get('path') or record.get('path')})
            # Known generated provenance must survive the byte verifier.
            origins = [prior.get('generation_origin'), record.get('generation_origin')]
            checked['generation_origin'] = next((x for x in origins if x in {'generated','synthetic','ai_generated'}),
                                                 next((x for x in origins if x), 'not_verified'))
            if checked.get('status') == 'verified_bytes':
                # 观察证据绑定内容身份（SHA + 来源），不依赖可消失的文件路径。
                run_records(self.run).put('observed_asset/' + checked['sha256'],
                          {'path': str(checked.get('path')), 'actual_sha256': checked.get('sha256'),
                           'byte_size': checked.get('byte_size'), 'asset_source': checked.get('asset_source'),
                           'status': 'verified_bytes'})
            indexed = self.index({**record, 'byte_details': checked})
            materials.append({'kind': image.get('kind', 'legacy_images'),
                              'record': indexed, 'bytes': checked,
                              'provenance': image.get('provenance') or self.source})
        # Optional concept metadata disambiguates names; image captions are excluded.
        definition = row.get('concept_record')
        if definition:
            materials.append({'kind':'legacy_concepts', 'record':definition, 'provenance':self.source})
        return {'case_id':'visual_'+digest([concept, materials])[:20],
                'concept_ref':'legacy:'+concept, 'identity':{'target_label':concept},
                'cleaned_materials':materials, 'source_units':[],
                'input_provenance':self.source, 'blocked':None}


def visual_pack(row):
    return {'pipeline_version':'V2', 'publication_kind':'visual_materials',
            'concept':row['identity']['target_label'], 'case_id':row['case_id'],
            'status':'visual_only', 'status_reason':'Article branch not executed',
            'knowledge':[], 'images':[m for m in row['cleaned_materials']
                                      if m['kind'] in {'legacy_images','qid_images'}],
            'visual_materials':row.get('visual_materials', []),
            'visual_publication_issues':row.get('visual_publication_issues', []),
            'audit':{'image_decisions':row.get('image_decisions', []),
                     'image_material_scope':row.get('image_material_scope', {}),
                     'source':row.get('input_provenance')}}


def merge_visual_packs(a, b):
    if a is None:
        return b
    return {**a, 'images':a['images']+b['images'],
            'visual_materials':list({v['publication']['sha256']:v for v in
                                    a['visual_materials']+b['visual_materials']}.values()),
            'visual_publication_issues':a['visual_publication_issues']+b['visual_publication_issues'],
            'audit':{'batches':a['audit'].get('batches', [a['audit']])+[b['audit']]}}


class ReplayVisualResponse:
    """Revalidate saved responses; never request missing independent reviews."""
    def __init__(self, reviews):
        self.reviews = {r['batch_id']:r for r in reviews}
        if len(self.reviews) != len(reviews):
            raise ValueError('Duplicate recorded review batch')

    def __call__(self, original):
        row = PrepareImageReview()(RecordPrimaryImageSelection()(original))
        review = self.reviews.get(row['batch_id'])
        if review:
            if review['image_prompt'] != row['image_prompt'] or review['pixel_roles'] != row['pixel_roles']:
                raise ValueError('Saved review does not match the original prompt/pixels')
            row.update({k:review.get(k) for k in ('prompt_result','prompt_error','prompt_call')})
        elif row['review_required']:
            row['prompt_error'] = 'No recorded independent response; replay cannot generate one'
        return ApplyConfirmedImageSelection()(row)


def read_visual_input(ref, root, concepts=None):
    """Read entity inputs with native Dataset operators and an explicit concept scope."""
    from collect.material_schema import IMAGE_METADATA
    from collect.materials import generation_origin
    from preparation.operaters.article import article_record
    from preparation.operaters.results import from_stage_row
    if ref.schema_name == 'pipeline_stage_rows':
        return data.read_lance(ref.resolve(root),version=ref.lance_version).map(from_stage_row)
    if ref.schema_name == 'articles':
        predicate="article_kind = 'knowledge'"
        if concepts:
            predicate += ' AND concept IN ('+', '.join("'"+c.replace("'","''")+"'" for c in concepts)+')'
        return data.read_lance(ref.resolve(root),version=ref.lance_version,filter=predicate).map(article_record).filter(lambda r:bool(r.get('images')))
    if ref.schema_name != 'raw_images':raise ValueError('Unsupported visual input entity')
    if not concepts or any(not isinstance(c,str) or not c.strip() for c in concepts):
        raise ValueError('Image entity visual input requires an explicit concepts list')
    selected=set(concepts)
    predicate=' OR '.join("array_contains(concepts, '"+c.replace("'","''")+"')" for c in sorted(selected))
    columns=['sha256','ext','byte_size','storage_mode',*IMAGE_METADATA.names]
    def expand(row):
        record={**row,'generation_origin':generation_origin(row)}
        for concept in sorted(set(row.get('concepts') or []) & selected):
            yield {'concept':concept,'images':[{'kind':'legacy_images','record':record}]}
    def combine(a,b):
        return {'concept':b['concept'],'images':(a['images'] if a else [])+b['images']}
    return (data.read_lance(ref.resolve(root),version=ref.lance_version,columns=columns,filter=predicate)
        .flat_map(expand).reduce_by_key('concept',combine))


from contextlib import contextmanager
import os, signal, sys, time
import httpx
from project import PROJECT_ROOT as ROOT
from demiflow.execution.artifacts import run_lock
from demiflow.execution.processes import matching, command, wait_until, spawn, stop_owned

def ready(port,model):
    try:
        with httpx.Client(timeout=3,trust_env=False) as c:
            r=c.get(f'http://127.0.0.1:{port}/v1/models');r.raise_for_status()
            return any(x['id']==model for x in r.json()['data'])
    except (httpx.HTTPError,ValueError,KeyError):return False

@contextmanager
def image_review_service(run, config, *, needed=True):
    if not needed:
        yield
        return
    model = config['image_review_model']
    if model != 'gemma-4-31b-it' or config['image_review_base_url'].rstrip('/') != 'http://127.0.0.1:8001/v1':
        raise ValueError('Review service requires the tested local Gemma31 endpoint')
    if ready(8001, model):
        yield  # Externally managed healthy service: leave it untouched.
        return
    if config['image_review_service'] != 'borrow':
        raise RuntimeError('Start Gemma31 on 8001, or configure image_review_service=borrow')
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            client.get('http://127.0.0.1:8001/v1/models')
    except httpx.ConnectError:
        pass
    else:
        raise RuntimeError('Port 8001 is occupied; will not replace another service')
    logdir = Path(run).parent / '_demiflow' / Path(run).name / 'image_review_service'
    logdir.mkdir(parents=True, exist_ok=True)
    def event(status, **details):
        row = {'time': time.time(), 'status': status, **details}
        from preparation.operaters.runfiles import run_records
        run_records(run).put('service_event/' + str(time.time_ns()), row)
        print(status, flush=True)
    # Shared lock protects GPU lending across formal runs.
    with run_lock(ROOT / '_demiflow/model_service'):
        servers = matching('vllm.entrypoints.cli.main serve ' + str(ROOT.parent / 'models/Qwen3.8-27B'))
        if len(servers) != 1 or not ready(8000, 'qwen3.8-27b'):
            raise RuntimeError('Expected the original healthy Qwen service')
        pid = servers[0]; original = command(pid)
        started = Path(f'/proc/{pid}/stat').read_text().split()[21]
        owned = None; stopped = False
        event('before_borrow', original_command=original)
        try:
            if command(pid) != original or Path(f'/proc/{pid}/stat').read_text().split()[21] != started:
                raise RuntimeError('Original service identity changed')
            os.kill(pid, signal.SIGTERM); stopped = True
            wait_until(lambda: not command(pid), 180, 'original service stop')
            cmd = [sys.executable, '-m', 'vllm.entrypoints.cli.main', 'serve', str(ROOT.parent / 'models/gemma-4-31B-it'),
                   '--served-model-name', model, '--host', '127.0.0.1', '--port', '8001', '--tensor-parallel-size', '2',
                   '--gpu-memory-utilization', '0.90', '--max-model-len', str(config.get('local_review_context_tokens', 32768)), '--max-num-seqs', str(config.get('local_review_max_num_seqs', 2)),
                   '--limit-mm-per-prompt', json.dumps({'image': config.get('image_batch_size', 4)})]
            if config.get('local_review_enforce_eager', True):
                cmd += ['--enforce-eager']  # 旧行为默认保留；r5 起显式关闭以启用 CUDA 图
            if config.get('local_review_enable_thinking', False):
                # Keep Gemma's thought channel out of published article text.
                # This is opt-in for final-review diagnostics, not image filtering.
                cmd += ['--reasoning-parser', 'gemma4']
            event('starting_review_service', command=cmd)
            owned = spawn(cmd, logdir / 'gemma.log', cwd=ROOT)
            wait_until(lambda: ready(8001, model), 600, 'Gemma startup', owned)
            event('review_service_ready')
            yield
        finally:
            stop_owned(owned)
            if stopped:
                event('restoring_qwen')
                restored = spawn(original, logdir / 'restore_qwen.log', cwd=ROOT)
                wait_until(lambda: ready(8000, 'qwen3.8-27b'), 600, 'Qwen restoration', restored)
            if not ready(8000, 'qwen3.8-27b'):
                raise RuntimeError('Original Qwen service failed to recover')
            event('original_resources_restored')
