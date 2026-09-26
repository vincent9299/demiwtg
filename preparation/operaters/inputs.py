"""Business row operators and source readers; no Dataset scheduling or stage lookup."""
import itertools
import json
from pathlib import Path
from demiflow.execution.artifacts import digest, immutable, snapshot
from preparation.operaters.documents import clean_document



def merge_document(acc,row):
    base=acc or {**row,'concept_refs':list(row['concept_refs'])}
    if not row.get('source_qid') and row.get('mapped_concept_ref'):
        base['concept_refs']=sorted(set(base['concept_refs']+[row['mapped_concept_ref']]))
    base.pop('mapped_concept_ref',None)
    base['association_status']=('ambiguous_mapping' if len(base['concept_refs'])>1 and base['format']=='wiki_sections'
                                else 'source_only' if base['concept_refs'] else 'unassociated')
    return base


async def read_document(doc):
    try:
        if doc.get('content_status') != 'available':
            raise ValueError('Document body unavailable: '+str(doc.get('content_status')))
        if doc['format']=='wiki_sections':
            text='\n\n'.join((s.get('title','')+'\n'+s.get('text','')).strip() for s in doc['sections'])
        else:
            text=doc.get('text')
            if text is None:raise ValueError('Available document has no body')
        raw=text.encode()
        if doc.get('content_sha256') and digest(raw)!=doc['content_sha256']:
            raise ValueError('Document content hash mismatch')
        return {**doc,'raw_text':text,'raw_sha256':digest(raw),'read_status':'readable','read_error':None}
    except (OSError,ValueError,UnicodeError) as e:
        return {**doc,'raw_text':'','raw_sha256':None,'read_status':'read_error','read_error':str(e)}


async def clean_document_record(doc):
    import asyncio
    c=await asyncio.to_thread(clean_document,doc['raw_text'],title=doc.get('title'),source_sections=doc.get('sections'))
    from preparation.operaters.identity import material_disposition
    return {**doc,'clean_text':c['text'],'clean_blocks':c['blocks'],'clean_counts':c['counts'],
        'clean_status':c['status'] if doc['read_status']=='readable' else 'unavailable',
        'clean_warnings':c['warnings']+([doc['read_error']] if doc['read_error'] else []),
        'clean_extraction':c['extraction'],'source_media':c['media'],'source_supplements':c['supplements'],'clean_version':c['version'],'knowledge_eligibility':material_disposition(c)}


async def check_image(img):
    import asyncio
    from preparation.operaters.images import inspect_lake_image
    result=await asyncio.to_thread(inspect_lake_image,img)
    return {**img,'byte_status':result['status'],'byte_details':result}


def model_input(row):
    """Boundary adapter only: three public schemas stay flat until this step."""
    ref=row['concept_ref'];prefix,value=ref.split(':',1);materials=[]
    for item in row['source_records']:
        materials.append({'kind':item['source'].get('kind') or item['source']['snapshot']['kind'],'record':item['fields'],'provenance':item['source']})
    for item in row.get('materials',[]):
        r=item['material'];source=r['source']
        m={'kind':source.get('kind') or source['snapshot']['kind'],'record':r,'provenance':source}
        if item['material_type']=='documents':
            m['document']={'text':r['raw_text'],'sha256':r['raw_sha256']}
            m['cleaning']={'text':r['clean_text'],'status':r['clean_status'],'blocks':r['clean_blocks'],
                'extraction':r.get('clean_extraction'),'media':r.get('source_media',[]),'supplements':r.get('source_supplements',[]),'counts':r['clean_counts'],'warnings':r['clean_warnings'],'version':r['clean_version'],
                'source_sha256':r['raw_sha256'],'source_locator':source}
        else:m['bytes']=r['byte_details']
        materials.append(m)
    case=digest({'concept_ref':ref,'group_index':row.get('group_index',0),'materials':materials})[:20]
    bundle={'concept_id':'trial-'+digest(ref)[:24],'request':{'kind':'legacy' if prefix=='legacy' else 'qid','value':value},
        'materials':materials,'identity_status':'source_only','knowledge_status':'not_extracted'}
    return {'case_id':case,'concept_ref':ref,'bundle':bundle,'cleaned_materials':materials,
        'input_scope':{'group_index':row.get('group_index',0),'group_last':row.get('group_last',True),
                       'cross_group_integration':'not_implemented','concept_id_contract':'source-bound trial ID; no authority migration'}}

class SelectConcept:
    """Concept row -> same row plus selected and selection_reason."""
    def __init__(self, ids=None, sample_rate=1., seed=42):
        self.ids=None if ids is None else set(ids); self.rate=sample_rate; self.seed=seed
    def __call__(self, c):
        ref=c['concept_ref']; reason='selected'
        if self.ids is not None and ref not in self.ids:reason='id_filter'
        elif self.rate<1 and int(digest({'concept_ref':ref,'seed':self.seed})[:16],16)>=int(self.rate*2**64):reason='concept_sample'
        return {**c,'selected':reason=='selected','selection_reason':reason}










def source_record(item, source):
    """Convert one decoded object to business fields; never open or scan a file."""
    kind=source['kind'];i=item['row'];r=item['value']
    if 'dataset_ref' not in source:
        raise ValueError('Source requires a fixed Lance DatasetRef')
    if item['source_ref'] != source['dataset_ref']:
        raise ValueError('Read table differs from frozen source')
    path = source['dataset_ref']['relative_uri']
    origin = {'kind':kind, 'dataset_ref':source['dataset_ref'], 'row':i, 'content_sha256':digest(r)}
    if item['error'] or not isinstance(r,dict):raise ValueError('Expected a valid object record')
    sid=digest(origin)
    if kind in {'legacy_concepts','qid_concepts','qid_concepts_base'}:
        value=r.get('name') if kind=='legacy_concepts' else r.get('qid')
        if not isinstance(value,str) or not value:raise ValueError(f'missing concept identity: {path}:{i}')
        ref=('legacy:' if kind=='legacy_concepts' else 'qid:')+value
        return {'concept_ref':ref,'name':r.get('name') or (r.get('zh') or {}).get('title') or (r.get('en') or {}).get('title'),
            'aliases':r.get('aliases',[]),'qid':r.get('qid'),'identity_status':'source_only',
            'source_records':[{'fields':r,'source':origin}],
            'page_refs':[{'lang':lang,'page_id':str(r[lang]['page_id']),'mapped_concept_ref':ref} for lang in ('en','zh') if isinstance(r.get(lang),dict) and r[lang].get('page_id') is not None]}
    elif kind in {'legacy_docs','wiki_pages'}:
        refs=['legacy:'+x for x in (r.get('concepts') or r.get('instances') or [])] if kind=='legacy_docs' else ['qid:'+r['qid']] if r.get('qid') else []
        return {**r,'doc_id':r.get('document_id') or sid,'concept_refs':refs,'format':'wiki_sections' if kind=='wiki_pages' else 'saved_text',
            'source_qid':r.get('qid'),'sections':r.get('sections',[]),'lang':r.get('lang'),
            'page_id':str(r['page_id']) if r.get('page_id') is not None else None,'source':origin,
            'association_status':'source_only' if refs else 'unassociated'}
    elif kind in {'legacy_images','qid_images'}:
        refs=['qid:'+r['qid']] if kind=='qid_images' and r.get('qid') else ['legacy:'+x for x in (r.get('concepts') or r.get('instances') or [])]
        return {**r,'image_id':r['sha256'],'path':r.get('path') or r.get('blob_path'),'concept_refs':refs,'source':origin}
    else:return {**r,'source':origin,'source_type':kind}

class DecodeMaterial:
    """Decoded object + source locator -> concept identity and page references."""
    def __init__(self,source=None):self.source=source
    def __call__(self,row):return source_record(row,self.source or row['source'])


from demiflow.lance.registry import Catalog
from collect.concepts import resolve_concept_release
from collect.material_schema import IMAGES_URI,DOCUMENTS_URI


class SourceUnavailable(ValueError):
    pass


def resolve_source(dataset, kind, spec=None):
    """读取显式固定源；未指定时保留原有采集源选择方式。"""
    root=Path(dataset)
    if spec is not None:
        from demiflow.lance.refs import DatasetRef
        from demiflow.lance.storage import schema_hash
        import lance
        if type(spec.get('version')) is not int or spec['version'] < 1:
            raise ValueError('Specify a positive fixed source version')
        path = (root / spec['uri']).resolve()
        ds = lance.dataset(str(path), version=spec['version'])
        relative = str(path.relative_to(root.resolve()))
        # 采集行的来源证据使用 DatasetRef，保留实际表与版本，不查发布别名。
        schema = 'master_concepts' if kind == 'legacy_concepts' else 'raw_images' if kind == 'legacy_images' else 'raw_documents'
        ref = DatasetRef(relative.removesuffix('.lance'), relative, ds.version, schema, 'v1', schema_hash(ds.schema), ds.count_rows())
        return ref, {'kind': kind, 'dataset_ref': ref.to_dict()}
    if kind=='legacy_concepts':
        master=resolve_concept_release(root);ref=master['dataset_ref']
        source={'kind':kind,'dataset_ref':ref.to_dict(),'master_release':master['release_id']}
    else:
        uri={'legacy_docs':DOCUMENTS_URI,'wiki_pages':DOCUMENTS_URI,'legacy_images':IMAGES_URI,
             'qid_concepts':DOCUMENTS_URI}[kind]
        matches=[r for r in Catalog(root).registered() if r.resolve(root)==str(root/uri)]
        if not matches:raise SourceUnavailable('Required source is not registered: '+uri)
        ref=max(matches,key=lambda r:r.lance_version);source={'kind':kind,'dataset_ref':ref.to_dict()}
    ref.open(root)
    return ref,source




class DecodeSourceRow:
    def __init__(self,source):self.source=source
    def __call__(self,row):
        kind=self.source['kind']
        if kind == 'qid_concepts':
            language = row.get('language')
            value = {'qid': row['qid']}
            if language in ('en', 'zh'):
                value[language] = {'page_id': row['page_id'], 'title': row['title']}
            error = None
        elif kind == 'legacy_concepts':
            try:value,error=json.loads(row['raw_payload']),None
            except (ValueError,TypeError) as exc:value,error=None,str(exc)
            if kind=='legacy_concepts' and isinstance(value,dict):
                value.update({k:row[k] for k in ('name','aliases','carriers') if k in row})
        else:
            value=dict(row);error=None
            if kind in ('legacy_docs','wiki_pages'):
                value['lang']=row.get('language')
                value['url']=row.get('source_identity')
        return {'row':row.get('source_row',0)+1,'value':value,'error':error,
                'source_ref':self.source['dataset_ref'],'source':self.source}


class SelectSourceRecords:
    def __init__(self, kind, ids=None, sample_rate=1., seed=42, *, enabled=True):
        self.kind=kind
        self.select=SelectConcept(ids,sample_rate,seed)
        self.enabled=enabled

    def __call__(self, row):
        # Preserve decode failures for audit, not as usable materials.
        value=row.get('value')
        if not self.enabled or row.get('error') or not isinstance(value,dict):return True
        if self.kind=='legacy_concepts':
            if not isinstance(value.get('name'),str) or not value['name']:return True
            refs=['legacy:'+value['name']]
        elif self.kind in {'qid_concepts','qid_concepts_base'}:
            # Keep all QID page mappings: early removal could conceal ambiguous pages.
            return True
        elif self.kind in {'legacy_docs','legacy_images'}:
            names=value.get('concepts') or value.get('instances') or []
            if not isinstance(names,list) or any(not isinstance(x,str) for x in names):return True
            refs=['legacy:'+x for x in names]
            if not refs:return True  # Unassociated records remain visible, never invent a link.
        else:return True  # Wiki page mapping still requires the downstream join.
        return any(self.select({'concept_ref':ref})['selected'] for ref in refs)


from project import PROJECT_ROOT as ROOT
from demiflow.execution.artifacts import digest, file_record, immutable, read, run_lock
from preparation.operaters.runfiles import rows


def accepted(review):
    return (isinstance(review, dict) and review.get("decision") == "accept"
            and review.get("reviewer_kind") in {"assistant", "human", "independent"}
            and all(review.get(k) for k in ("reviewer", "reason", "evidence", "scope")))


def asset_review_ok(review, role):
    if (role == 'reference' and review.get('publisher') in {'PublishArticle', 'PublishVisualMaterials'}
            and review.get('publication_status') == 'reviewed' and review.get('evidence')
            and review.get('support_scope')):
        return True
    if not accepted(review):
        return False
    if role in {"reference", "edit_source"}:
        # Unknown production history needs no certificate. Preserve explicit
        # negative findings in legacy reviews; acceptance does not certify origin.
        return (review.get("non_generated") is not False and review.get("identity_checked") is True
                and bool(review.get("support_scope")))
    return True


def attach_identity(item):
    fingerprint = digest(item)
    return {**item, "item_id": ("K" if item["kind"] == "text" else "V") + fingerprint[:24],
            "fingerprint": fingerprint, "review": {"decision": "pending"}}


def source_entries(run):
    """Read final publication; intermediate pools only resolve cited source bytes."""
    records, identity = iter_material_rows(run)
    for row in records:
        delivered = material_record({**row, '_knowledge_run': identity.get('run'),
                                      '_knowledge_sha256': digest(row), '_article_source': identity})
        yield from delivered['materials']


def eligible(item):
    if item.get('publication', {}).get('adapter') == 'visual-publication/1':
        return (item['publication'].get('status') == 'published' and item['kind']=='image'
                and bool(item.get('asset')) and asset_review_ok(item.get('review', {}), 'reference'))
    if item.get('publication', {}).get('adapter') == 'final-publication/2':
        return (item['publication'].get('status') == 'published'
                and item.get('review', {}).get('publisher') == 'PublishArticle'
                and bool(item.get('references') if item['kind'] == 'text' else item.get('asset')))
    if item["kind"] == "text":
        return accepted(item["review"]) and bool(item["references"] and item["sources"])
    return item["upstream_status"] == "keep_candidate" and asset_review_ok(item["review"], "reference")


from demiflow.execution.artifacts import digest


def material_record(row):
    """One final KB row -> explicit eligible items and rejected-delivery reasons."""
    run = Path(row['_knowledge_run']) if row.get('_knowledge_run') else None
    concept = row['concept']
    result = {'concept': concept, 'materials': [], 'delivery_issues': [],
              'publication_status': row.get('status'), 'knowledge_run': str(run) if run is not None else None,
              'publication_source': row.get('_article_source') or row.get('_visual_sources')}
    # V2 visual publication is independent of text/article success. Legacy rows
    # have no visual_materials and retain their original publication boundary.
    for visual in row.get('visual_materials', []):
        publication = visual.get('publication', {})
        image = visual.get('image', {})
        support = publication.get('support', {})
        if (publication.get('schema') != 'concept-visual-publication/1'
                or publication.get('status') != 'reviewed' or visual.get('concept') != concept
                or publication.get('sha256') != image.get('bytes', {}).get('sha256')
                or not support.get('supports') or not support.get('region')):
            result['delivery_issues'].append('Invalid independent visual publication')
            continue
        asset = source_asset(image)
        if not asset:
            result['delivery_issues'].append('Independent visual pixels/provenance unavailable')
            continue
        metadata = publication.get('metadata', {})
        asset['image_metadata'] = metadata
        asset['description'] = (metadata.get('description') or {}).get('caption') or publication.get('visible_information', '')
        item = attach_identity({'kind':'image', 'concept':concept, 'image_id':visual['image_id'],
            'asset':asset, 'upstream_status':'published', 'upstream_run':str(run),
            'visual_support':support, 'image_metadata':metadata,
            'selection_review':image.get('selection_review', {}),
            'publication':{'status':'published', 'adapter':'visual-publication/1',
                'knowledge_sha256':row['_knowledge_sha256']}})
        item['review'] = {'decision':'accept', 'reviewer_kind':'upstream_model',
            'reviewer':'knowledge.visual_review', 'publisher':'PublishVisualMaterials',
            'publication_status':'reviewed', 'sha256':asset['sha256'],
            'reason':publication['identity_reason'], 'scope':support['supports'],
            'support_scope':support['supports'], 'region':support['region'],
            'limitations':support.get('limitations', ''), 'evidence':[digest(publication)]}
        result['materials'].append(item)
    if row.get('publication_kind') == 'visual_materials':
        if row.get('knowledge'):
            raise ValueError('Visual-only publication cannot assert article knowledge')
        return result
    if row.get('status') != 'reviewed' or row.get('audit', {}).get('preflight_error'):
        result['delivery_issues'].append('Final knowledge not published: ' + str(row.get('status_reason') or row.get('status')))
        return result
    # Neither membership in this pool nor a keep decision grants eligibility.
    passages = {p['source_id']: p for p in row.get('published_passages', [])}
    images = {}
    for raw in row.get('published_images', []):
        iid = raw.get('image_id') or raw.get('record', {}).get('image_id')
        if iid:
            images.setdefault(iid, raw)
    selected = set(row.get('audit', {}).get('selected_image_ids', []))
    for ti, topic in enumerate(row.get('knowledge', [])):
        texts = topic.get('content', {}).get('paragraphs', [])
        placements = topic.get('content', {}).get('images', [])
        for pi, paragraph in enumerate(texts):
            refs = [r for r in topic.get('references', []) if pi in r.get('paragraph_indices', [])]
            ids = {s for r in refs for s in r.get('source_ids', []) if 'text' in r.get('kinds', [])}
            missing = sorted(ids - passages.keys())
            # Visual-only paragraphs are allowed only with their final published figure.
            figures = [p for p in placements if p.get('paragraph_index') == pi and p['image_id'] in selected]
            if not paragraph.strip() or not refs or missing or (not ids and not figures):
                result['delivery_issues'].append({'position': [ti, pi], 'reason': 'Missing cited source context or final visual evidence', 'missing_source_ids': missing})
                continue
            item = attach_identity({'kind': 'text', 'concept': concept, 'case_id': row.get('case_id', concept),
                'text': paragraph, 'title': topic['title'], 'position': [ti, pi], 'references': refs,
                'sources': [passages[s] for s in sorted(ids)], 'upstream_status': 'reviewed',
                'upstream_run': str(run), 'publication': {'status': 'published', 'adapter': 'final-publication/2',
                    'knowledge_sha256': row['_knowledge_sha256'], 'visual_dependencies': [p['image_id'] for p in figures]}})
            item['review'] = material_review(topic['title'], row)
            result['materials'].append(item)
        for placement in placements:
            iid = placement['image_id']
            if any(m.get('image_id') == iid for m in result['materials'] if m['kind']=='image'):
                continue  # Keep the richer independent visual record once.
            image = images.get(iid)
            if iid not in selected or not image:
                result['delivery_issues'].append({'image_id': iid, 'reason': 'Figure lacks final selection or resolvable source'})
                continue
            asset = source_asset(image)
            if not asset:
                result['delivery_issues'].append({'image_id': iid, 'reason': 'Final figure bytes/provenance unavailable'})
                continue
            pi = placement.get('paragraph_index')
            support = texts[pi] if type(pi) is int and 0 <= pi < len(texts) else ''
            item = attach_identity({'kind': 'image', 'concept': concept, 'image_id': iid,
                'asset': asset, 'upstream_status': 'published', 'upstream_run': str(run),
                'position': [ti, pi], 'placement': placement, 'selection_review': image.get('selection_review', {}),
                'publication': {'status': 'published', 'adapter': 'final-publication/2', 'knowledge_sha256': row['_knowledge_sha256']}})
            item['review'] = {**material_review(support, row), 'support_scope': support,
                              'limitations': placement.get('limitations'), 'sha256': asset['sha256']}
            result['materials'].append(item)
    # A text reference to unavailable pixels must not become text-only evidence.
    delivered_images = {i['image_id'] for i in result['materials'] if i['kind'] == 'image'}
    result['materials'] = [i for i in result['materials'] if not set(i['publication'].get('visual_dependencies', [])) - delivered_images]
    return result


def material_review(scope, row):
    return {'decision': 'accept', 'reviewer_kind': 'upstream_model', 'reviewer': 'knowledge.final_review',
            'reason': 'Final article explicitly publishes this item; not human certification',
            'scope': scope, 'evidence': [row['_knowledge_sha256']], 'publisher': 'PublishArticle',
            'publication_status': 'reviewed'}


def source_asset(image):
    info, record = image.get('bytes', {}), image.get('record', {})
    path, sha = info.get('path'), info.get('sha256')
    source = {k: record[k] for k in ('landing_url', 'content_url', 'url', 'license', 'author', 'source') if record.get(k)}
    if record.get('sources'):
        source['records'] = record['sources']
    if not sha or not source:
        return None
    from preparation.operaters.images import asset_resolution
    resolution = asset_resolution(path, sha)
    if resolution.status == 'missing':
        return None
    if resolution.status == 'corrupt':
        # 冻结字节与内容身份不符是交付错误，不能静默降级为无图
        raise ValueError('Frozen image bytes changed: ' + str(path))
    if resolution.status == 'read_error':
        raise ValueError('Image bytes unreadable: ' + str(resolution.error))
    from collect.materials import generation_origin
    origin = generation_origin(record)
    if origin == 'not_verified':
        origin = info.get('generation_origin', origin)
    if origin in {'generated', 'synthetic', 'ai_generated'}:
        return None
    from preparation.operaters.images import index_metadata
    metadata = index_metadata(image)
    return {'path': path, 'sha256': sha, 'source': source, 'origin': origin,
            'asset_source': resolution.source,
            'image_metadata': metadata,
            'description': (metadata.get('description') or {}).get('caption') or record.get('caption', ''), 'image_id': image.get('image_id', record.get('image_id'))}


def freeze_material_source(spec):
    """固定结果表路径与版本；历史运行仍按原有来源记录读取。"""
    from demiflow.lance.refs import DatasetRef
    if isinstance(spec, dict):
        if 'uri' in spec:
            # 路径和版本已经是完整读取条件；只校验，不转成 DatasetRef、不打开表计数。
            from project import resolve_root
            if (set(spec) != {'uri', 'version'} or not isinstance(spec['uri'], str) or not spec['uri']
                    or type(spec['version']) is not int or spec['version'] < 1):
                raise ValueError('Material table requires uri and a positive fixed version')
            root = resolve_root().resolve()
            relative = str((root / spec['uri']).resolve().relative_to(root))
            return {'uri': relative, 'version': spec['version']}
        if 'dataset_ref' in spec:
            if set(spec) != {'dataset_ref', 'release_id'}:
                raise ValueError('Expected dataset_ref and release_id')
            DatasetRef.from_dict(spec['dataset_ref'])
            return dict(spec)
        ref = DatasetRef.from_dict(spec)
        if ref.schema_name in {'curated_images', 'articles'}:
            raise ValueError('Entity publication requires an explicit release_id selection')
        return {'dataset_ref': ref.to_dict(), 'release_id': None}
    candidate = Path(spec)
    if candidate.is_absolute() or '/' in str(spec):
        from preparation.operaters.runfiles import stage_ref
        return {'dataset_ref': stage_ref(candidate, 'knowledge_base').to_dict(), 'release_id': None}
    raise ValueError('Specify a material table with uri and version; source aliases are not supported')


def _visual_meta_row(record):
    """Restore the existing publication contract from explicit per-concept release metadata."""
    review = record.get('concept_review') or {}
    if record.get('publication_status') != 'reviewed' or review.get('decision') != 'keep':
        raise ValueError('Published visual row contradicts its saved final review')
    sha, iid = record['sha256'], record.get('image_id') or 'I' + record['sha256'][:12]
    image = {'image_id': iid, 'record': record.get('source') or {},
             'selection_review': review,
             'bytes': {'path': record.get('path'), 'sha256': sha, 'resolution': record.get('resolution')}}
    publication = {'schema': 'concept-visual-publication/1', 'status': 'reviewed', 'sha256': sha,
                   'metadata': record.get('image_metadata') or {}, 'support': record.get('visual_support') or {},
                   'identity_reason': review.get('reason') or '', 'visible_information': review.get('visible_information') or ''}
    return {'concept': record['concept'], 'status': 'reviewed', 'publication_kind': 'visual_materials',
            'knowledge': [], 'curated_images': [image],
            'visual_materials': [{'concept': record['concept'], 'image_id': iid,
                                  'image': image, 'publication': publication}]}


def iter_material_rows(spec, *, concepts=None):
    spec = freeze_material_source(spec)
    from project import resolve_root
    if 'uri' in spec:
        import lance
        ds = lance.dataset(str(resolve_root() / spec['uri']), version=spec['version'])
        # 从实际表结构判断文章/图片结果，无需额外类型登记，也不扫描整表计数。
        fields = set(ds.schema.names)
        kind = ('articles' if {'article_id', 'content', 'review_status'} <= fields
                else 'curated_images' if {'sha256', 'concept_assessments'} <= fields else None)
        if kind is None:
            raise ValueError('Expected a preparation articles or images result table')
        release = None
    else:
        # 已有运行中保存的固定引用按原值读取；不把当前输入转换到这一格式。
        from demiflow.lance.refs import DatasetRef
        ref = DatasetRef.from_dict(spec['dataset_ref'])
        ds, kind, release = ref.open(resolve_root()), ref.schema_name, spec['release_id']
    # 直接指定结果表时按审核状态读取；历史来源有 release 时保持原筛选范围。
    predicate = "array_contains(release_ids, '" + release.replace("'", "''") + "')" if release else None
    selected = set(concepts) if concepts is not None else None
    if selected is not None:
        if any(not isinstance(c, str) or not c.strip() for c in selected):
            raise ValueError('Material concept scope requires nonempty names')
        quoted = ["'" + c.replace("'", "''") + "'" for c in sorted(selected)]
        scope = ("concept IN (" + ','.join(quoted) + ")" if kind == 'articles' and quoted
                 else '(' + ' OR '.join('array_contains(published_concepts, ' + c + ')' for c in quoted) + ')'
                 if kind == 'curated_images' and quoted else 'false')
        if kind in {'articles', 'curated_images'}:
            predicate = '(' + predicate + ') AND ' + scope if predicate else scope
    def gen():
        if kind == 'curated_images':
            from preparation.operaters.images import assessment_record
            for batch in ds.scanner(columns=['sha256', 'concept_assessments'], filter=predicate).to_batches():
                for image in batch.to_pylist():
                    for item in image['concept_assessments'] or []:
                        if item['published'] and (release in item['release_ids'] if release else item['review_status'] == 'keep'):
                            record = assessment_record(image, item)
                            if selected is None or record['concept'] in selected:
                                yield _visual_meta_row(record)
        elif kind == 'articles':
            from preparation.operaters.article import article_record
            reviewed = "review_status = 'reviewed'"
            for batch in ds.scanner(filter=(predicate + ' AND ' + reviewed) if predicate else reviewed).to_batches():
                for row in batch.to_pylist(): yield article_record(row)
        elif kind == 'pipeline_stage_rows':
            for batch in ds.scanner(columns=['payload']).to_batches():
                for value in batch.column(0).to_pylist():
                    row = json.loads(value)
                    if selected is None or row['concept'] in selected:
                        yield row
        else:
            raise ValueError('Unsupported publication entity: ' + kind)
    return gen(), spec


def combined_input_records(article_sources, visual_sources=(), *, concepts=None):
    """文章源 + 视觉源 → 按概念组合的发布行（含完整来源标注）。"""
    articles = {}   # concept -> (row, source_identity, row_sha)
    conflicts = []
    for spec in article_sources:
        gen, identity = iter_material_rows(spec, concepts=concepts)
        for row in gen:
            concept = row["concept"]
            row_sha = digest(row)
            prior = articles.get(concept)
            if prior is not None:
                if prior[2] != row_sha:
                    conflicts.append({
                        "concept": concept,
                        "publications": [
                            {"source": prior[1], "content_sha256": prior[2]},
                            {"source": identity, "content_sha256": row_sha},
                        ],
                        "reason": "Multiple distinct article publications for one concept require explicit resolution",
                    })
                    continue
                continue  # identical publication duplicated across sources
            enriched = {**row, "_article_source": identity, "_knowledge_sha256": row_sha}
            articles[concept] = (enriched, identity, row_sha)
    if conflicts:
        raise ValueError("Conflicting article publications: " + json.dumps(conflicts, ensure_ascii=False))

    visuals = {}    # concept -> list[(visual_row, identity)]
    for spec in visual_sources:
        gen, identity = iter_material_rows(spec, concepts=concepts)
        for row in gen:
            concept = row["concept"]
            visuals.setdefault(concept, []).append((row, identity))

    def generate():
        for concept in articles:
            row, identity, _sha = articles[concept]
            merged_visuals = list(row.get("visual_materials", []))
            visual_sources_used = []
            candidate_specs = [identity]
            for visual_row, visual_identity in visuals.get(concept, []):
                merged_visuals.extend(visual_row.get("visual_materials", []))
                visual_sources_used.append(visual_identity)
                if visual_identity not in candidate_specs:
                    candidate_specs.append(visual_identity)
            yield {**row, "visual_materials": merged_visuals,
                   "_visual_sources": visual_sources_used,
                   "_candidate_specs": candidate_specs}
        for concept in visuals:
            if concept in articles:
                continue
            entries = visuals[concept]
            merged_visuals = []
            candidate_specs = []
            for visual_row, visual_identity in entries:
                merged_visuals.extend(visual_row.get("visual_materials", []))
                if visual_identity not in candidate_specs:
                    candidate_specs.append(visual_identity)
            base = entries[0][0]
            yield {**base, "knowledge": [], "publication_kind": "visual_materials",
                   "visual_materials": merged_visuals,
                   "_visual_sources": [identity for _row, identity in entries],
                   "_candidate_specs": candidate_specs,
                   "_knowledge_sha256": digest([r for r, _ in entries])}

    return generate()


__all__ = ["combined_input_records", "iter_material_rows"]


import base64
import io
import re
import struct


from PIL import Image, ImageOps
from preparation.operaters.images import asset_bytes, asset_file
from demiflow.execution.artifacts import digest, read


def pixels(asset):
    """原始字节 + 基础像素指纹。路径只是定位提示，身份是内容 SHA；
    文件缺失时从湖内 Lance Blob 读取（缺失/损坏/读失败分别抛错）。

    MPO 属 JPEG 多帧家族，按 image/jpeg 提供原始字节；其余格式须在
    {JPEG,PNG,WEBP,GIF,MPO} 内，不能解码给模型的格式显式报错。
    """
    data = asset_pixels(asset)
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if im.format not in {"JPEG", "PNG", "WEBP", "GIF", "MPO"}:
            raise ValueError("Unsupported image format")
        mime = "image/jpeg" if im.format in {"JPEG", "MPO"} else Image.MIME[im.format]
        gray = im.convert("L").resize((9, 8))
        samples = gray.tobytes()
        dhash = sum((samples[y * 9 + x] > samples[y * 9 + x + 1]) << (y * 8 + x)
                    for y in range(8) for x in range(8))
    return data, mime, f"{dhash:016x}"


def checked_asset(asset, role):
    if not isinstance(asset, dict) or not asset.get("source"):
        raise ValueError("Image requires provenance")
    data, mime, dhash = pixels(asset)
    review = asset.get("review", {})
    if review.get("sha256") != asset["sha256"] or not asset_review_ok(review, role):
        raise ValueError("Image role lacks a content-bound review: " + role)
    return {**asset, "role": role, "dhash": dhash, "mime": mime}


def _compute_variant_values(data):
    """镜像/缩放/适度中心裁剪的比对指纹；纯内容函数（字节已按 sha 验证）。

    EXIF 元数据损坏（PIL SyntaxError）时退回存储方向——指纹不因可选
    元数据失效。
    """
    values = []
    with Image.open(io.BytesIO(data)) as source:
        try:
            im = ImageOps.exif_transpose(source).convert('L')
        except (SyntaxError, ValueError, struct.error):
            im = source.convert('L')
        # Mirror/resize and moderate centre crops. Not a general duplicate oracle.
        for fraction in (1., .9, .8):
            dx, dy = int(im.width*(1-fraction)/2), int(im.height*(1-fraction)/2)
            crop = im.crop((dx, dy, im.width-dx, im.height-dy))
            for variant in (crop, ImageOps.mirror(crop)):
                samples = variant.resize((9,8)).tobytes()
                values.append(sum((samples[y*9+x] > samples[y*9+x+1]) << (y*8+x)
                                  for y in range(8) for x in range(8)))
    return tuple(values)


_VARIANT_CACHE: dict[str, tuple] = {}


def variant_hashes(asset):
    """缓存键 = 内容 SHA（路径/mtime 不进身份；缓存可重建）。"""
    sha = asset['sha256']
    values = _VARIANT_CACHE.get(sha)
    if values is None:
        data = asset_pixels(asset)
        values = _compute_variant_values(data)
        if len(_VARIANT_CACHE) >= 8192:
            _VARIANT_CACHE.clear()
        _VARIANT_CACHE[sha] = values
    return values


def duplicate(a, b):
    if a.get("sha256") == b.get("sha256"):
        return True
    if a.get("duplicate_group") and a.get("duplicate_group") == b.get("duplicate_group"):
        return True
    if (bool(a.get('dhash') and b.get('dhash')) and
            (int(a['dhash'],16) ^ int(b['dhash'],16)).bit_count() <= 4):
        return True
    if all(x.get('sha256') and (x.get('path') or x.get('blob_ref')) for x in (a,b)):
        return any((x ^ y).bit_count() <= 4 for x in variant_hashes(a) for y in variant_hashes(b))
    return False


def reference_options(materials, targets):
    """Original evidence numbers compatible with all supplied targets."""
    blocked = {n for n, item in enumerate(materials, 1)
               if item['kind'] == 'image'
               and any(duplicate(item['asset'], target) for target in targets)}
    figures = {(item['concept'], item.get('image_id', item['item_id']))
               for n, item in enumerate(materials, 1)
               if item['kind'] == 'image' and n not in blocked}
    eligible, excluded = [], []
    for n, item in enumerate(materials, 1):
        dependencies = {(item['concept'], iid) for iid in
                        item.get('publication', {}).get('visual_dependencies', [])}
        reason = ('target_or_source_near_duplicate' if n in blocked else
                  'required_figure_excluded' if dependencies - figures else None)
        if reason:
            excluded.append({'evidence': n, 'item_id': item['item_id'], 'reason': reason})
        else:
            eligible.append(n)
    return {'evidence': eligible, 'excluded': excluded}


class SplitGuard:
    def __init__(self, registry):
        self.registry = dict(registry) if isinstance(registry, dict) else read(registry)
        if self.registry.get("schema") != "v4-split-registry/1":
            raise ValueError("An explicit split registry is required")
        self.test = self.registry["formal_test"]
        self.images = []
        for asset in self.test.get("images", []):
            _, _, dhash = pixels(asset)
            self.images.append({**asset, "dhash": dhash})

    def check_plan(self, plan, branch):
        if branch == "training" and plan["split"] != "train":
            raise ValueError("Training plans must have split=train")
        if branch == "benchmark" and plan["split"] not in {"development", "test"}:
            raise ValueError("Benchmark plans must have split=development or test")
        if plan["split"] == "test" and self.registry.get("scope") != "frozen_formal_test":
            raise ValueError("Formal export requires a frozen formal-test registry")
        if plan["split"] == "test" and (plan["concept"] not in self.test.get("concepts", []) or
                plan["rule_family"] not in self.test.get("rule_families", [])):
            raise ValueError("Formal-test concept and rule family must be reserved before export")
        if branch == "training":
            if plan["concept"] in self.test.get("concepts", []):
                raise ValueError("Formal-test concept cannot enter training")
            if plan["rule_family"] in self.test.get("rule_families", []):
                raise ValueError("Formal-test rule family cannot enter training")

    def allows_asset(self, asset, training=False):
        if training:
            sources = json.dumps(asset.get("source", {}), ensure_ascii=False)
            if any(url in sources for url in self.test.get("source_urls", [])):
                return False
        return not any(duplicate(asset, other) for other in self.images)

    def allows_item(self, item, training=False):
        if item["kind"] == "image":
            _, _, dhash = pixels(item["asset"])
            if not self.allows_asset({**item["asset"], "dhash": dhash}):
                return False
        if training:
            if item["concept"] in self.test.get("concepts", []):
                return False
            raw_sources = json.dumps(item.get("references", item.get("asset", {}).get("source", {})), ensure_ascii=False)
            if any(url in raw_sources for url in self.test.get("source_urls", [])):
                return False
        text_hash = digest(item.get("text", ""))
        return text_hash not in self.test.get("answer_text_sha256", [])

    def check_instruction(self, instruction, training=False):
        key = digest(instruction.strip())
        if key in self.test.get("answer_text_sha256", []) or (training and key in self.test.get("question_sha256", [])):
            raise ValueError("Formal-test question/answer text leakage")


def tokens(text):
    text = text.lower()
    latin = re.findall(r"[a-z0-9]+", text)
    chinese = re.findall(r"[\u3400-\u9fff]+", text)
    return set(latin + [s[i:i+2] for s in chinese for i in range(max(1, len(s)-1))])


def search_text(item):
    if item["kind"] == "text":
        return item["concept"] + " " + item["text"]
    review = item["review"]
    # Captions rank candidates; eligibility still requires source/pixel review.
    return " ".join(str(x or "") for x in (item["concept"], review.get("support_scope"),
                    item.get("selection_review", {}).get("visible_information")))


def retrieve(items, query, guard, *, excluded=(), training=False, text_limit=3, image_limit=2):
    # Excluding a target also excludes text that requires that figure. Apply
    # before ranking so dependent text cannot reintroduce its content indirectly.
    if excluded:
        options = reference_options(items, excluded)
        allowed = set(options['evidence'])
        role_excluded = options['excluded']
        items = [item for n, item in enumerate(items, 1) if n in allowed]
    else:
        role_excluded = []
    query_tokens = tokens(query)
    candidates = []
    excluded_reasons = list(role_excluded)
    for item in items:
        if not eligible(item):
            continue
        try:
            if not guard.allows_item(item, training):
                excluded_reasons.append({"item_id": item["item_id"], "reason": "formal_test_reservation"})
                continue
            if item["kind"] == "image":
                _, _, dhash = pixels(item["asset"])
                asset = {**item["asset"], "dhash": dhash}
                if any(duplicate(asset, other) for other in excluded):
                    excluded_reasons.append({"item_id": item["item_id"], "reason": "target_or_source_near_duplicate"})
                    continue
            term_set = tokens(search_text(item))
            score = len(query_tokens & term_set) / max(1, len(query_tokens))
            if score:
                candidates.append((score, item))
        except (ValueError, OSError) as error:
            excluded_reasons.append({"item_id": item["item_id"], "reason": str(error)})
    selected = []
    counts = {"text": 0, "image": 0}
    limits = {"text": text_limit, "image": image_limit}
    for score, item in sorted(candidates, key=lambda pair: (-pair[0], pair[1]["item_id"])):
        if counts[item["kind"]] < limits[item["kind"]]:
            selected.append(item)
            counts[item["kind"]] += 1
    return selected, {"method": "lexical_zh_bigrams_en_tokens/1", "query": query,
                      "ranks": [{"item_id": item["item_id"], "score": score} for score, item in candidates],
                      "selected_ids": [i["item_id"] for i in selected], "excluded": excluded_reasons}


def asset_for_item(item):
    return checked_asset({**item["asset"], "review": {**item["review"], "sha256": item["asset"]["sha256"]}}, "reference")


def model_content(instruction, items, edit_source=None, target=None):
    content = [{"type": "text", "text": instruction}]
    mapping = []
    for number, item in enumerate(items, 1):
        if item["kind"] == "text":
            context = [{k: source[k] for k in ("source_id", "text", "context_before", "context_after", "sections",
                                               "title", "url", "raw_source_sha256", "document_sha256") if k in source}
                       for source in item["sources"]]
            payload = {"knowledge": item["text"], "review_scope": item["review"]["scope"],
                       "limitations": item["review"].get("limitations"),
                       "sources": context, "references": item["references"]}
            content.append({"type": "text", "text": f"资料{number}（知识文本及完整原文上下文）\n" + json.dumps(payload, ensure_ascii=False)})
        else:
            asset = asset_for_item(item)
            add_image(content, asset, f"资料{number}：检索参考图；只支持 {item['review']['support_scope']}。"
                      f"限制：{item['review'].get('limitations', '仅限已核验支持范围')}。不能当待编辑原图。"
                      + '\n来源和发布位置（caption只辅助定位，不是事实证明）：' + json.dumps({
                          'source': asset.get('source'), 'origin': asset.get('origin', 'not_verified'),
                          'placement': item.get('placement'), 'publication': item.get('publication')}, ensure_ascii=False))
            mapping.append({"number": number, "item_id": item["item_id"], "role": "reference", "sha256": asset["sha256"], "path": asset.get("path"), "blob_ref": asset.get("blob_ref")})
    if edit_source:
        add_image(content, edit_source, "编辑原图：在这张图上修改；不是知识参考或答案。")
        mapping.append({"role": "edit_source", "sha256": edit_source["sha256"], "path": edit_source.get("path"), "blob_ref": edit_source.get("blob_ref")})
    if target:
        add_image(content, target, "监督目标：本次仅供冻结任务的目标审核，不得进入作答输入。")
        mapping.append({"role": "target", "sha256": target["sha256"], "path": target.get("path"), "blob_ref": target.get("blob_ref")})
    return content, mapping


def add_image(content, asset, label):
    data, mime, _ = pixels(asset)
    content.extend([{"type": "text", "text": label},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}}])


def asset_pixels(asset):
    if asset.get('blob_ref'):
        from demiflow.lance.blobs import BlobRef
        from project import resolve_root
        ref = BlobRef(**asset['blob_ref'])
        if ref.sha256 != asset['sha256']: raise ValueError('Conflicting Blob identity')
        return ref.read(resolve_root())
    return asset_bytes(asset.get('path'), asset['sha256'])[0]
