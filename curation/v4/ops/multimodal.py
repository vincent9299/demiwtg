"""Concept materials -> joint knowledge -> cited-source verification.

Row/flat-map/reduce actors only. Native demiflow owns execution and journals.
"""
import base64,io,json
from pathlib import Path
from PIL import Image,ImageOps
from curation.v4.contracts import digest
from .knowledge_stages import material_id
from .source_blocks import pack_whole


def pixels(images,max_edge=1536):
    urls=[];roles=[]
    for item in images:
        raw=Path(item['bytes']['path']).read_bytes()
        if digest(raw)!=item['record']['sha256']:raise ValueError('Image changed before model call')
        with Image.open(io.BytesIO(raw)) as source:
            pic=ImageOps.exif_transpose(source).convert('RGBA')
            background=Image.new('RGBA',pic.size,'white');background.alpha_composite(pic)
            pic=background.convert('RGB');pic.thumbnail((max_edge,max_edge))
            out=io.BytesIO();pic.save(out,format='JPEG',quality=90)
        urls.append('data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode())
        roles.append({'image_id':item['image_id'],'original_sha256':digest(raw),'input_sha256':digest(out.getvalue()),
                      'max_edge':max_edge,'frame':0,'dimensions':list(pic.size)})
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
    def __init__(self,batch_size=4,identity_definitions=None,neutral=False):
        if batch_size<1:raise ValueError('image batch size must be positive')
        self.batch_size=batch_size
        self.identity_definitions=identity_definitions or {}
        self.neutral=neutral
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
                   and 'limitations' in matches[0]
                   and (matches[0]['limitations'] is None or isinstance(matches[0]['limitations'],str)))
            if valid and row['image_prompt'].get('selection_protocol')=='image-relevance-v2':
                item=matches[0];relation=item.get('concept_relation');decision=item.get('decision')
                expected={'target':'direct','related_activity':'background','text_only':'unrelated','namesake':'unrelated','unrelated':'unrelated','uncertain':'uncertain'}
                valid=(relation in expected and item['relation']==expected[relation]
                       and decision in {'keep','exclude','pending'}
                       and ((relation in {'text_only','namesake','unrelated'} and decision=='exclude')
                            or (relation=='uncertain' and decision=='pending')
                            or (relation in {'target','related_activity'} and decision==('pending' if item['observability']=='unusable' else 'keep'))))
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


class BatchJointMaterials:
    """Cartesian text/image groups ensures every selected material appears; no total cap."""
    def __init__(self,text_chars=2500,image_batch_size=4):
        if text_chars<1 or image_batch_size<1:raise ValueError('positive batch targets required')
        self.text_chars=text_chars;self.image_batch_size=image_batch_size
    def __call__(self,row):
        if row.get('blocked'):return []
        pack=row['material_pack'];ps=pack['passages'];ims=pack['images']
        if not ps and not ims:return []
        text_groups=list(pack_whole(ps,self.text_chars)) or [[]]
        image_groups=[ims[i:i+self.image_batch_size] for i in range(0,len(ims),self.image_batch_size)] or [[]]
        out=[]
        for i,pg in enumerate(text_groups):
            for j,ig in enumerate(image_groups):
                urls,roles=pixels(ig)
                payload={'concept':row['identity']['target_label'],
                    'passages':[{k:p.get(k) for k in ['source_id','text','sections','source_family','reference_notes','context_before','context_after']} for p in pg],
                    'image_ids':[m['image_id'] for m in ig],
                    'image_selection':[m['selection_review'] for m in ig],
                    'scope':'Current input group only; cross-group candidates merged later'}
                out.append({'case_id':row['case_id'],'batch_index':len(out),'batch_id':f"{row['case_id']}:joint:{i}:{j}",
                            'joint_prompt':payload,'pixel_images':urls,'pixel_roles':roles})
        return out


def validate_fact(f,passages,images):
    issues=[]
    if not isinstance(f.get('statement'),str) or not f['statement'].strip():issues.append('missing_statement')
    for key in ['conditions','exceptions','reasons']:
        if not isinstance(f.get(key),list) or not all(isinstance(x,str) for x in f[key]):issues.append('invalid_'+key)
    if f.get('status') not in {'candidate','deferred'}:issues.append('invalid_status')
    ev=f.get('evidence');ie=f.get('image_evidence')
    if not isinstance(ev,list) or not isinstance(ie,list):return issues+['invalid_evidence_lists']
    for e in ev:
        if not isinstance(e,dict) or not isinstance(e.get('source_id'),str) or e.get('source_id') not in passages or not isinstance(e.get('quote'),str) or not e['quote'].strip() or e['quote'] not in passages[e['source_id']]:issues.append('quote_not_in_supplied_source')
    for e in ie:
        if not isinstance(e,dict) or not isinstance(e.get('image_id'),str) or e.get('image_id') not in images or not all(isinstance(e.get(k),str) and e[k].strip() for k in ['region','supports','limitations']):issues.append('invalid_image_evidence')
    expected='multimodal' if ev and ie else 'text' if ev else 'image' if ie else None
    if not expected or f.get('basis')!=expected:issues.append('basis_evidence_mismatch')
    return issues


class ApplyJointExtraction:
    def __call__(self,row):
        result=row.get('prompt_result') or {};items=result.get('facts',[]);facts=[];deferred=[]
        ps={p['source_id']:p['text'] for p in row['joint_prompt']['passages']};ims=set(row['joint_prompt']['image_ids'])
        malformed=not isinstance(items,list) or not isinstance(result.get('conflicts'),list) or not isinstance(result.get('coverage_note'),str)
        if not isinstance(items,list):items=[]
        ids=[f.get('fact_id') for f in items if isinstance(f,dict)];mapping={}
        conflicts=result.get('conflicts',[]);conflicts=conflicts if isinstance(conflicts,list) else []
        conflict_ids={x for c in conflicts if isinstance(c,dict) and isinstance(c.get('fact_ids'),list) for x in c['fact_ids'] if isinstance(x,str)}
        for i,f in enumerate(items):
            if not isinstance(f,dict):continue
            old=f.get('fact_id');new='K'+digest({'batch':row['batch_id'],'index':i,'fact':f})[:16]
            if isinstance(old,str):mapping[old]=new
            issues=validate_fact(f,ps,ims)
            if not isinstance(old,str) or ids.count(old)!=1:issues.append('invalid_or_duplicate_fact_id')
            if row.get('prompt_error') or malformed:issues.append('extraction_protocol_failed')
            if isinstance(old,str) and old in conflict_ids:issues.append('unresolved_input_conflict')
            if f.get('status')=='deferred':issues+=f.get('reasons',[]) or ['model_deferred']
            fact={**f,'fact_id':new,'original_fact_id':old,'statement_mode':'joint_source_summary','truth_status':'not_verified',
                  'source_batch_id':row['batch_id']}
            if issues:deferred.append({'fact':fact,'reasons':issues,'next_action':'Review original source and pixels'})
            else:facts.append(fact)
        clean={k:v for k,v in row.items() if k not in {'prompt_result','prompt_error','prompt_call'}}
        return {**clean,'joint_facts':facts,'joint_deferred':deferred,
            'joint_conflicts':[{'conflict_id':'C'+digest({'batch':row['batch_id'],'conflict':c})[:16],
                               'affected_fact_ids':[mapping[x] for x in c.get('fact_ids',[]) if x in mapping],
                               'issue':c.get('issue'),'needed_evidence':c.get('needed_evidence')} for c in conflicts if isinstance(c,dict)],
            'joint_calls':[{'call':row.get('prompt_call'),'error':row.get('prompt_error'),'result':result,
                            'pixel_roles':row['pixel_roles'],'protocol_failed':malformed}],
            'verify_prompt':{**row['joint_prompt'],'facts':facts+[d['fact'] for d in deferred]}}


class ApplyJointVerification:
    def __call__(self,row):
        result=row.get('prompt_result') or {};reviews=result.get('reviews',[]);reviews=reviews if isinstance(reviews,list) else []
        facts=[];deferred=list(row['joint_deferred']);support=[]
        for f in row['joint_facts']:
            matches=[r for r in reviews if isinstance(r,dict) and r.get('fact_id')==f['fact_id']]
            valid=len(matches)==1 and not row.get('prompt_error')
            review=matches[0] if valid else {};pairs=review.get('image_support',[])
            wanted={x['image_id'] for x in f['image_evidence']}
            valid=valid and review.get('status') in {'supported','unsupported','uncertain'} and isinstance(review.get('reason'),str) and bool(review['reason'].strip())
            valid=valid and isinstance(pairs,list) and len(pairs)==len(wanted) and all(isinstance(p,dict) for p in pairs)
            if valid:valid={p.get('image_id') for p in pairs}==wanted and all(p.get('status') in {'full','partial','none','unobservable'} and all(isinstance(p.get(k),str) and p[k].strip() for k in ['region','supports','limitations']) for p in pairs)
            if valid and review['status']=='supported' and (f['evidence'] or any(p['status'] in {'full','partial'} for p in pairs)):
                positive={p['image_id'] for p in pairs if p['status'] in {'full','partial'}}
                accepted=[e for e in f['image_evidence'] if e['image_id'] in positive]
                facts.append({**f,'image_evidence':accepted,
                    'unsupported_image_links':[e for e in f['image_evidence'] if e['image_id'] not in positive],
                    'basis':'multimodal' if f['evidence'] and accepted else 'text' if f['evidence'] else 'image',
                    'citation_review':review})
            else:deferred.append({'fact':f,'reasons':['citation_or_image_support_unverified',review.get('reason','Missing/invalid review')],'next_action':'Review sources/pixels'})
            if valid:support.extend({**p,'fact_id':f['fact_id']} for p in pairs)
        return {'case_id':row['case_id'],'joint_facts':facts,'joint_deferred':deferred,'joint_conflicts':row['joint_conflicts'],
            'processed_joint_batch_ids':[row['batch_id']],
            'joint_support':support,'joint_calls':row['joint_calls'],
            'joint_verification_calls':[{'call':row.get('prompt_call'),'error':row.get('prompt_error'),'result':result,'pixel_roles':row['pixel_roles']}]}


def merge_joint_batches(a,b):
    if a is None:return b
    return {'case_id':b['case_id'],**{k:a[k]+b[k] for k in ['joint_facts','joint_deferred','joint_conflicts','joint_support','joint_calls','joint_verification_calls','processed_joint_batch_ids']}}


def merge_joint_scope(a,b):
    return b if a is None else {'case_id':b['case_id'],'planned_joint_batch_ids':a['planned_joint_batch_ids']+b['planned_joint_batch_ids']}


class PrepareJointMerge:
    def __call__(self,row):
        return {**row,'merge_prompt':{'concept':row['identity']['target_label'],
            'facts':row.get('joint_facts',[])+[d['fact'] for d in row.get('joint_deferred',[])],
            'scope':'Only processed joint groups; unprocessed input groups remain pending'}}


class ApplyJointMerge:
    def __call__(self,row):
        result=row.get('prompt_result') or {};facts=row.get('joint_facts',[]);deferred=list(row.get('joint_deferred',[]))
        known={f['fact_id']:f for f in facts+[d['fact'] for d in deferred]};kept={f['fact_id']:f for f in facts}
        groups=result.get('duplicate_groups');conflicts=result.get('conflicts');exclusions=result.get('exclusions')
        valid=not row.get('prompt_error') and all(isinstance(x,list) for x in [groups,conflicts,exclusions])
        ignored_singletons=[]
        if valid:
            # A known singleton asserts no equivalence and performs no merge.
            # Preserve it in the audit, but do not let it invalidate other groups.
            valid=all(isinstance(g,list) and len(g)>0 and all(isinstance(x,str) and x in known for x in g) for g in groups)
            if valid:
                ignored_singletons=[g for g in groups if len(g)==1]
                groups=[g for g in groups if len(g)>1]
                flat=[x for g in groups for x in g]
                valid=len(flat)==len(set(flat))
            valid=valid and all(isinstance(c,dict) and isinstance(c.get('fact_ids'),list) and len(c['fact_ids'])>=2 and all(isinstance(x,str) and x in known for x in c['fact_ids']) and c.get('issue') and c.get('needed_evidence') for c in conflicts)
            valid=valid and all(isinstance(e,dict) and e.get('fact_id') in known and e.get('reason') for e in exclusions)
        if not valid:
            deferred += [{'fact':f,'reasons':['cross_batch_merge_unverified'],'next_action':'Review merge response'} for f in facts];kept={};groups=[];conflicts=[];exclusions=[]
        conflict_records=list(row.get('joint_conflicts',[]))
        for c in conflicts:
            conflict_records.append({'conflict_id':'C'+digest(c)[:16],'affected_fact_ids':c['fact_ids'],**c})
            for fid in c['fact_ids']:
                if fid in kept:deferred.append({'fact':kept.pop(fid),'reasons':[c['issue']],'next_action':c['needed_evidence']})
        removed=[]
        for e in exclusions:
            if e['fact_id'] in kept:removed.append({'fact':kept.pop(e['fact_id']),'reason':e['reason']})
        merges=[];support=list(row.get('joint_support',[]))
        for g in groups:
            # Never promote deferred facts or merge explicit condition differences.
            if not all(x in kept for x in g):continue
            ref=kept[g[0]]
            if any(kept[x]['conditions']!=ref['conditions'] or kept[x]['exceptions']!=ref['exceptions'] for x in g):continue
            members=[kept.pop(x) for x in g];ref=dict(ref)
            for key in ['evidence','image_evidence']:
                ref[key]=list({json.dumps(e,sort_keys=True):e for f in members for e in f[key]}.values())
            ref['basis']='multimodal' if ref['evidence'] and ref['image_evidence'] else 'text' if ref['evidence'] else 'image'
            ref['merged_from']=g;ref['merged_candidates']=members;kept[g[0]]=ref;merges.append(g)
            support=[{**p,'fact_id':g[0]} if p['fact_id'] in g else p for p in support]
        planned=row.get('planned_joint_batch_ids',[]);processed=row.get('processed_joint_batch_ids',[])
        unprocessed=[x for x in planned if x not in processed]
        scope={'planned':len(planned),'processed':len(processed),'unprocessed_batch_ids':unprocessed,'limit':row.get('joint_batch_limit')}
        knowledge={'facts':list(kept.values()),'deferred_facts':deferred,'unresolved_conflicts':conflict_records,
                   'source_relations':[{'kind':'duplicate','fact_ids':g} for g in merges],
                   'remaining_knowledge_review':[{'reason':'joint_batch_not_processed','batch_id':x} for x in unprocessed],
                   'coverage_note':f'Joint groups processed {len(processed)}/{len(planned)}; partial scope if any remain. Candidate truth not verified.'}
        reviewed_images={p['image_id'] for c in row.get('joint_verification_calls',[]) if not c.get('error') for p in c['pixel_roles']}
        images=[m for m in row.get('material_pack',{}).get('images',[]) if m['image_id'] in reviewed_images]
        evidence={'status':'machine_reviewed','result':{'images':[{'image_id':m['image_id'],'caption':m['selection_review']['visible_information']} for m in images],
                    'support':support},'note':'Model citation/pixel review, not independent truth approval'}
        if not row.get('joint_calls'):evidence={'status':'not_run','reason':'no_selected_materials'}
        return {**{k:v for k,v in row.items() if k not in {'prompt_result','prompt_call','prompt_error'}},'knowledge':knowledge,
                'image_evidence':evidence,'joint_batch_scope':scope,'joint_merge_review':{'call':row.get('prompt_call'),'result':result,'protocol_valid':valid,'ignored_singleton_groups':ignored_singletons,'excluded':removed,'merged':merges}}
