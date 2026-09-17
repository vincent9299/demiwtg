"""Row operators connecting material routing, conditional integration and final records."""
import copy,json
from collections import defaultdict
from ..contracts import digest
from .material_routing import route_materials
from .paragraphs import SelectRetainedParagraphs
from .paragraph_merge import PrepareParagraphMerge
from .topic_articles import BuildTopicArticle


class RouteConceptMaterials:
    def __init__(self,text_chars=2500,image_batch_size=4):self.text_chars=text_chars;self.image_batch_size=image_batch_size
    def __call__(self,row):
        return route_materials(row,row,row.get('passage_embeddings',{}),packing='nearest_fit',max_chars=self.text_chars,max_images=self.image_batch_size)


class PrepareVerifiedParagraphs:
    def __init__(self,run):self.run=str(run)
    def __call__(self,row):
        return {**row,'run':self.run,'concept':row['joint_prompt']['concept']}


class BuildLocalMergeGroups:
    """Coalesce overlapping positive pairs into disjoint bounded tasks before rewriting."""
    def __init__(self,max_chars=120000,max_images=24):self.max_chars=max_chars;self.max_images=max_images
    def __call__(self,row):
        items={p['paragraph_id']:p for p in row['items']};groups=[];pending=[]
        for review in row.get('review_rows',[]):
            if review['next_action']=='pending':pending.append({'paragraph_ids':review['paragraph_ids'],'reason':'uncertain_relationship'});continue
            if review['next_action']!='local_integration_candidate':continue
            ids=set(review['paragraph_ids'])
            if not ids<=items.keys():raise ValueError('Unknown reviewed paragraph')
            overlap=[g for g in groups if g&ids]
            for g in overlap:ids|=g;groups.remove(g)
            groups.append(ids)
        requests=[]
        for ids in groups:
            wanted=defaultdict(set)
            for pid in ids:wanted[items[pid]['batch_id']].add(items[pid]['block_id'])
            selected=[];consumed=[]
            for source in row['source_rows']:
                bids=wanted.get(source['batch_id'],set())
                if not bids:continue
                topics=[]
                referenced_images={ref['image_id'] for t in source['topics'] for b in t['blocks'] if b['block_id'] in bids for ref in b.get('image_refs',[])}
                for topic in source['topics']:
                    blocks=[b for b in topic['blocks'] if b['block_id'] in bids or
                            (b['type']=='image' and b.get('status')=='candidate' and (bids.intersection(b.get('related_block_ids',[])) or b.get('image_id') in referenced_images))]
                    if not blocks:continue
                    # The merge restores pixel observations and clears old links.
                    consumed.extend({'batch_id':source['batch_id'],'block_id':b['block_id']} for b in blocks)
                    topics.append({**topic,'blocks':blocks})
                relevant_sources={c['source_id'] for t in topics for b in t['blocks'] for c in b.get('citations',[])}
                selected.append({**source,'topics':topics,'joint_prompt':{**source['joint_prompt'],
                    'passages':[p for p in source['joint_prompt']['passages'] if p['source_id'] in relevant_sources]}})
            try:
                request=PrepareParagraphMerge(max_payload_chars=self.max_chars)({'concept':row['concept'],'rows':selected})
                if len(request['pixel_images'])>self.max_images:raise ValueError('pixel capacity')
            except ValueError as e:
                pending.append({'paragraph_ids':sorted(ids),'reason':str(e),'requires_finer_local_group':True});continue
            requests.append({**request,'concept':row['concept'],'input_paragraph_ids':sorted(ids),'consumed_blocks':consumed})
        return {'concept':row['concept'],'requests':requests,'pending':pending,
                'all_paragraph_ids':list(items),'policy':'Only positively reviewed connected candidates; independent content bypasses generation. Oversized tasks remain explicit pending, never truncated.'}


class ApplyLocalIntegration:
    def __call__(self,row):
        consumed=set();new=[];failed=[];deferred=set()
        for local in row.get('local_results',[]):
            if local.get('merge_validation_issues') or local.get('extraction_error') or local.get('verification_error'):
                deferred.update((b['batch_id'],b['block_id']) for b in local['consumed_blocks'])
                failed.append({'batch_id':local['batch_id'],'reason':'local_integration_failed','consumed_blocks':local['consumed_blocks']});continue
            consumed.update((b['batch_id'],b['block_id']) for b in local['consumed_blocks'])
            new.append(local)
        remaining=[]
        for original in row['source_rows']:
            r=copy.deepcopy(original);topics=[]
            for t in r['topics']:
                blocks=[b for b in t['blocks'] if (r['batch_id'],b['block_id']) not in consumed]
                for b in blocks:
                    if (r['batch_id'],b['block_id']) in deferred:
                        b['status']='deferred';b['defer_reason']='local_integration_failed'
                text_ids={b['block_id'] for b in blocks if b['type']=='text'}
                for b in blocks:
                    if b['type']=='image':b['related_block_ids']=[i for i in b.get('related_block_ids',[]) if i in text_ids]
                if blocks:topics.append({**t,'blocks':blocks,**({'topic_review':{'status':'repair','reason':'Content moved to another topic; title and image links need rechecking'}} if len(blocks)!=len(t['blocks']) else {})})
            if topics:remaining.append({**r,'topics':topics})
        return {'concept':row['concept'],'rows':remaining+new,'local_failures':failed}


class SourceCatalog:
    def __call__(self,row):
        from .knowledge_stages import material_id
        records={material_id(m):m['record'] for m in row['cleaned_materials']}
        sources={}
        for p in row['material_pack']['passages']:
            r=records.get(p['material_id'],{})
            sources[p['source_id']]={'title':r.get('title') or r.get('url') or p['source_id'],'url':r.get('url') or r.get('source_url') or ''}
        images={}
        for m in row['material_pack']['images']:
            r=m['record'];images[m['image_id']]={'title':r.get('title') or '图片来源','url':r.get('landing_url') or r.get('content_url') or r.get('url') or ''}
        return {'concept':row['identity']['target_label'],'sources':sources,'image_sources':images}


class FormatTopicArticle:
    def __call__(self,row):
        return BuildTopicArticle({}, {},row['sources'],row['image_sources'])(row)


class FinalKnowledgeRecord:
    def __call__(self,row):
        documents=[m for m in row['cleaned_materials'] if 'cleaning' in m]
        images=[m for m in row['cleaned_materials'] if m['kind'] in {'legacy_images','qid_images'}]
        return {'concept':row['identity']['target_label'],'case_id':row['case_id'],
                'identity':row['identity'],'documents':documents,'images':images,'knowledge':row.get('articles',[]),
                'audit':{'source_selection':row.get('material_pack',{}),'local_integration':row.get('local_audit'),
                         'cross_batch_plan':row.get('cross_batch_plan'),'missing_and_duplicate_images':row.get('image_material_scope')}}
