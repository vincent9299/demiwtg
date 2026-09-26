"""Material eligibility and explicit source identity decisions."""

def material_disposition(cleaning):
    warnings=set(cleaning.get('warnings',[]))
    reasons=[];actions=[]
    if cleaning.get('status')=='unavailable' or not cleaning.get('text','').strip():
        reasons.append('no_usable_body');actions.append('reacquire_source')
    routes={'possible_truncated_snippet':'fetch_full_document',
            'access_restriction_or_challenge_marker':'check_access_or_reacquire',
            'residual_navigation_requires_review':'separate_article_body',
            'unexpanded_wiki_template_retained':'parse_source_markup',
            'mixed_html_markup_retained':'parse_source_markup',
            'malformed_html_tail_retained':'repair_or_reacquire',
            'auxiliary_html_retained':'separate_article_body',
            'mixed_script_language_review':'review_language_and_readability',
            'partial_body_context_requires_review':'parse_source_markup'}
    for warning,action in routes.items():
        if warning in warnings:reasons.append(warning);actions.append(action)
    # Unknown cleaning warnings are not silently certified by a new ruleset.
    remaining=warnings-set(routes)-{'access_error_page_no_body','no_body_blocks','partial_markup_deferred'}
    if remaining:reasons.extend(sorted(remaining));actions.append('review_cleaning')
    if cleaning.get('status')=='needs_review' and not reasons:
        reasons.append('cleaning_needs_review');actions.append('review_cleaning')
    return {'status':'pending' if reasons else 'eligible_for_identity',
            'reasons':reasons,'next_actions':sorted(set(actions)),
            'scope':'Processing eligibility only; identity and factual reliability are not certified.'}

def defer_invalid_identity_quotes(result, previews):
    """Keep invalid model evidence visible and isolate that material, not its peers."""
    import copy
    out=copy.deepcopy(result);by_id={p['material_id']:p for p in previews};issues=list(out.get('protocol_issues',[]))
    for r in out.get('material_reviews',[]):
        p=by_id.get(r.get('material_id'))
        if p is None:continue  # global schema/coverage check still rejects this
        texts=[p.get('title') or '',p.get('caption') or '',p.get('text_preview') or '']+[s['text'] for s in p.get('text_spans',[])]
        q=r.get('quote');accepted=r['material_id'] in out.get('accepted_material_ids',[])
        invalid=not isinstance(q,str) or (accepted and not q.strip()) or (isinstance(q,str) and not any(q in t for t in texts))
        if not invalid:continue
        issues.append({'material_id':r['material_id'],'reason':'identity_quote_not_verbatim_in_supplied_input','original_review':copy.deepcopy(r)})
        out['accepted_material_ids']=[i for i in out['accepted_material_ids'] if i!=r['material_id']]
        out['rejected_materials']=[x for x in out['rejected_materials'] if x['material_id']!=r['material_id']]+[{'material_id':r['material_id'],'reason':'invalid quoted basis; pending identity evidence'}]
        r.update(relation='uncertain',quote='',reason='Original model quote invalid; decision withheld, not a confirmed mismatch.')
    if issues and out.get('status')=='resolved' and not out['accepted_material_ids']:
        out.update(status='insufficient',reason='No accepted material retains valid quoted identity evidence.')
    out['protocol_issues']=issues
    return out

def complete_identity_membership(result, previews):
    """Complete an omitted redundant list entry only from a complete explicit review.

    Missing/duplicate per-material reviews, unknown IDs and contradictory listed
    decisions still go through the strict checks. Original model output remains
    in the saved response; this repair cannot infer an unseen material's identity.
    """
    import copy
    out=copy.deepcopy(result);reviews=out.get('material_reviews')
    if not isinstance(reviews,list) or not isinstance(out.get('accepted_material_ids'),list) or not isinstance(out.get('rejected_materials'),list):return out
    wanted={p['material_id'] for p in previews}
    if not all(isinstance(r,dict) for r in reviews):return out
    if len(reviews)!=len(wanted) or {r.get('material_id') for r in reviews}!=wanted:return out
    present=set(out['accepted_material_ids'])|{r['material_id'] for r in out['rejected_materials']}
    issues=list(out.get('protocol_issues',[]))
    for r in reviews:
        if r['material_id'] in present:continue
        if r.get('relation') not in {'same_identity','related_context','different_identity','uncertain','unreadable'}:continue
        accept=out.get('status')=='resolved' and r['relation'] in {'same_identity','related_context'}
        if accept:out['accepted_material_ids'].append(r['material_id'])
        else:out['rejected_materials'].append({'material_id':r['material_id'],'reason':r.get('reason','pending identity review')})
        issues.append({'material_id':r['material_id'],'reason':'list_membership_completed_from_explicit_material_review',
                       'decision':'accept' if accept else 'reject','original_review':copy.deepcopy(r)})
    out['protocol_issues']=issues
    return out


def require(ok,message):
    if not ok:raise ValueError(message)

def validate_identity(result,materials,previews=None):
    require(result.get('status') in ('resolved','ambiguous','insufficient'),'invalid identity status')
    require(isinstance(result.get('target_label'),str) and isinstance(result.get('reason'),str),'identity explanation missing')
    accepted=result.get('accepted_material_ids');rejected=result.get('rejected_materials')
    require(isinstance(accepted,list) and isinstance(rejected,list),'identity lists required')
    ids=accepted+[r['material_id'] for r in rejected]
    require(len(ids)==len(set(ids)) and set(ids)=={m['material_id'] for m in materials},'identity decisions must cover inputs exactly once')
    require(result['status']=='resolved' or not accepted,'unresolved identity cannot accept materials')
    if previews is not None:
        reviews=result.get('material_reviews')
        require(isinstance(reviews,list),'per-material identity reviews required')
        require(len(reviews)==len(previews) and {r.get('material_id') for r in reviews}=={p['material_id'] for p in previews},'identity reviews must cover inputs once')
        by_id={p['material_id']:p for p in previews}
        for r in reviews:
            p=by_id[r['material_id']]
            require(r.get('relation') in {'same_identity','related_context','different_identity','uncertain','unreadable'},'invalid identity relation')
            require(r.get('basis') in {'text','metadata'} and bool(r.get('reason')),'identity basis required')
            require(r['material_id'] not in accepted or r['relation'] in {'same_identity','related_context'},'cannot accept unrelated/uncertain/unreadable material')
            if p['kind'] in {'legacy_images','qid_images'}:require(r['basis']=='metadata','identity stage has no image pixels')
            supplied=[p.get('title') or '',p.get('caption') or '',p.get('text_preview') or '']+[s['text'] for s in p.get('text_spans',[])]
            quote=r.get('quote')
            require(isinstance(quote,str) and (bool(quote.strip()) or r['material_id'] not in accepted),'accepted identity needs quoted basis')
            require(any(quote in text for text in supplied),'identity quote outside supplied preview')
    return result


import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote, parse_qs, urlencode
from demiflow.execution.artifacts import digest
from preparation.prompts import load_instruction
SYSTEM = load_instruction('system')
IDENTITY = load_instruction('identity')

def material_id(m):return 'M'+digest({'kind':m['kind'],'record':m['record'],'provenance':m['provenance']})[:12]


def text_of(m):
    if 'cleaning' in m:return m['cleaning']['text']
    r=m['record']
    if m['kind']=='legacy_docs':return m.get('document',{}).get('text','')
    if m['kind']=='wiki_pages':return '\n\n'.join((s.get('title','')+'\n'+s.get('text','')).strip() for s in r.get('sections',[]))
    return ''

def family(m):
    r=m['record'];url=r.get('url')
    if not url and m['kind']=='wiki_pages':url=f'https://{r.get("lang","en")}.wikipedia.org/wiki/{r.get("title","")}'
    if url:
        u=urlsplit(url);host=u.netloc.lower();path=unquote(u.path).replace('_',' ');query=parse_qs(u.query)
        if host.endswith('.wikipedia.org') or host.endswith('.wikisource.org'):
            host=host.replace('.m.wikipedia.org','.wikipedia.org').replace('.m.wikisource.org','.wikisource.org')
            if path=='/w/index.php' and query.get('title'):path='/wiki/'+query['title'][0].replace('_',' ')
            path=re.sub(r'^/(?:zh(?:-(?:hans|hant|cn|tw|hk|mo|sg|my))?)/','/wiki/',path)
            if path.startswith('/wiki/'):return urlunsplit(('https',host,path,'',''))
        query={k:v for k,v in query.items() if not k.startswith('utm_') and k not in {'fbclid','gclid'}}
        return urlunsplit((u.scheme,host,path,urlencode(query,doseq=True),''))
    return m['material_id']


class ResolveIdentity:
    label='identity'
    def __init__(self, run, config):
        self.run = Path(run)
        self.config = config
    async def prepare(self,row):
        bundle=row['bundle'];materials=[];identity=[];ineligible=[]
        for m in row['cleaned_materials']:
            if m['kind'] in ('legacy_concepts','qid_concepts','qid_concepts_base'):identity.append(m['record'])
            elif 'cleaning' in m and material_disposition(m['cleaning'])['status']=='pending':
                ineligible.append({'material_id':material_id(m),'reason':'pending_material_quality','disposition':material_disposition(m['cleaning']),
                                   'warnings':m.get('cleaning',{}).get('warnings',[])})
            elif text_of(m) or m['kind'] in ('legacy_images','qid_images'):
                materials.append({**m,'material_id':material_id(m)})
        docs=sorted([m for m in materials if text_of(m)],key=lambda m:m['material_id'])
        images=[m for m in materials if m['kind'] in ('legacy_images','qid_images')]
        # Spread the identity preview across page families before adding other versions.
        selected=[];seen=set()
        for m in docs:
            f=family(m)
            if f not in seen:selected.append(m);seen.add(f)
        # Fill spare preview slots with other versions after spreading across pages.
        represented={m['material_id'] for m in selected}
        selected.extend(m for m in docs if m['material_id'] not in represented)
        images.sort(key=lambda m:(m.get('bytes',{}).get('status')!='verified_bytes',m['material_id']))
        selected=selected[:self.config['identity_docs']]+images[:self.config.get('identity_images',self.config['identity_docs'])]
        if not selected:return {**row,'identity_ineligible':ineligible,'blocked':{'stage':self.label,'reason':'no_materials_in_scanned_scope'},'identity':{'status':'insufficient'}}
        previews=[{'material_id':m['material_id'],'kind':m['kind'],'title':m['record'].get('title'),
                   'url':m['record'].get('url',m['record'].get('content_url')),
                   'caption':m['record'].get('caption'),'pixels_provided':False,'text_preview':text_of(m)[:500],
                   'text_spans':[{'start':a,'end':min(a+500,len(text_of(m))),'text':text_of(m)[a:a+500]}
                       for a in sorted({0,max(0,len(text_of(m))//2-250),max(0,len(text_of(m))-500)}) if text_of(m)]} for m in selected]
        return {**row,'identity_ineligible':ineligible,'identity_materials':selected,
                'identity_unexamined':[m['material_id'] for m in materials if m not in selected],
                'identity_prompt':{'request':bundle['request'],'source_identity_records':identity,'materials':previews}}

    def apply(self,row,result,call):
        selected=row['identity_materials'];previews=row['identity_prompt']['materials']
        result=defer_invalid_identity_quotes(complete_identity_membership(result,previews),previews)
        validate_identity(result,selected,previews)
        out={**{k:v for k,v in row.items() if k!='identity_prompt'},
             'identity':{**result,'reviewer':'local_model; not human identity adjudication','call':call}}
        if result['status']!='resolved':out['blocked']={'stage':self.label,'reason':result['status'],'detail':result['reason']}
        return out


class _IdentityRow:
    """Native actor: business validation only; map_cached owns row persistence."""
    async def __call__(self,row):
        if row.get('blocked') and self.label!='export':
            return {**row,'skipped_stages':row.get('skipped_stages',[])+[self.label]}
        try:return await self.process(row)
        except (ValueError,KeyError,TypeError) as error:
            return {**row,'blocked':{'stage':self.label,'reason':type(error).__name__,'detail':str(error)}}
    async def aclose(self):pass

class PrepareIdentity(_IdentityRow, ResolveIdentity):
    label='identity_prepare'
    async def process(self,row):return await self.prepare(row)

def apply_prompt_result(op,row):
    """Keep model failures as blocked rows, never drop the concept batch."""
    if row.get('prompt_error'):
        return {**row,'blocked':{'stage':op.label,'reason':row['prompt_error']['type'],**row['prompt_error']}}
    if 'prompt_result' not in row:return row  # e.g. no pixels/facts, explicitly not_run
    clean={k:v for k,v in row.items() if k not in {'prompt_result','prompt_call','prompt_error'}}
    return op.apply(clean,row['prompt_result'],row['prompt_call'])

class ApplyIdentity(_IdentityRow, ResolveIdentity):
    async def process(self,row):return apply_prompt_result(self,row)
