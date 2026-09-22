"""Prepare pixels and apply image relevance decisions."""
import base64, io, json
from PIL import Image
from curation.preparation.contracts import digest
from curation.preparation.ops.identity import material_id
from curation.preparation.ops.image_pixels import oriented_pixels

def pixels(images,max_edge=1536):
    urls=[];roles=[]
    from curation.preparation.asset_io import asset_bytes
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
                from curation.preparation.ops.visual_materials import VISUAL_PROTOCOL, index_metadata
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
                from curation.preparation.ops.visual_materials import valid_visual_result
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
