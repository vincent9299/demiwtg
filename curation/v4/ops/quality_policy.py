import json
"""Business dispositions: retained material is not automatically extraction input."""
import re


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


def quarantine_conflicts(result, previous=None):
    """Never silently forget extraction conflicts; isolate affected statements.

    Explicit fact IDs allow unrelated statements in the same source to continue.
    Missing correspondence conservatively holds statements sharing those sources,
    and records that this is uncertainty about scope, not a proven contradiction.
    This operator cannot adjudicate semantic resolutions automatically.
    """
    from curation.v4.contracts import digest
    conflicts=[];seen=set()
    for conflict in result.get('unresolved_conflicts',[])+(previous or {}).get('unresolved_conflicts',[]):
        key=digest({'source_ids':sorted(conflict['source_ids']),'issue':conflict['issue']})
        if key not in seen:conflicts.append({**conflict,'conflict_id':key});seen.add(key)
    ready=[];deferred=list(result.get('deferred_facts',[]))
    facts=result['facts'];ids={f['fact_id'] for f in facts}
    for fact in facts:
        reasons=[]
        for conflict in conflicts:
            affected=conflict.get('affected_fact_ids')
            explicit=isinstance(affected,list) and bool(affected) and set(affected)<=ids
            if explicit:
                touches=fact['fact_id'] in affected;basis='explicit_fact_correspondence'
            else:
                touches=bool({e['source_id'] for e in fact['evidence']}&set(conflict['source_ids']))
                basis='unresolved_correspondence_shared_source'
            if touches:reasons.append({'conflict_id':conflict['conflict_id'],'basis':basis})
        if reasons:deferred.append({'fact':fact,'reasons':reasons,'next_action':'resolve_conflict_with_source_evidence'})
        else:ready.append(fact)
    return {**result,'facts':ready,'deferred_facts':deferred,'unresolved_conflicts':conflicts}


def image_followups(facts,evidence):
    rows=evidence.get('result',{}).get('support',[])
    return [{'fact_id':f['fact_id'],'next_action':'find_or_verify_image_support',
             'reason':'no_full_image_support_in_checked_inputs',
             'scope':'Does not reject text knowledge or claim that supporting images do not exist.'}
            for f in facts if not any(r['fact_id']==f['fact_id'] and r['status']=='full' for r in rows)]


def identity_followups(result):
    actions={'different_identity':'reassociate_material','uncertain':'resolve_identity','unreadable':'review_language_or_reacquire'}
    rejected={r['material_id'] for r in result.get('rejected_materials',[])}
    return [{'material_id':r['material_id'],'reason':r['reason'],
             'next_actions':[actions.get(r['relation'],'review_identity_rejection')]}
            for r in result.get('material_reviews',[]) if r['material_id'] in rejected]


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


def defer_unverifiable_facts(result, passages, previous=None):
    """Isolate a failed citation or unchecked number conversion, keeping originals.

    Does not prove semantic entailment. Derived quantities need a separately
    verified calculation; no approximate quote repair or invented replacement.
    """
    import copy
    result=copy.deepcopy(result)
    if not isinstance(result.get('facts'),list):return result
    if len(result['facts'])>6:raise ValueError('facts must be at most six')
    sources={p['source_id']:p['text'] for p in passages}
    prior=(previous or {}).get('deferred_facts',[])
    deferred=list(result.get('deferred_facts',[]))+copy.deepcopy(prior)
    previous_ids={d['fact']['fact_id'] for d in prior}
    number=re.compile(r'(?<![A-Za-z0-9_.])\d+(?:\.\d+)?')
    ready=[]
    for fact in result['facts']:
        if not isinstance(fact,dict) or not isinstance(fact.get('evidence'),list) or not all(isinstance(e,dict) for e in fact['evidence']):
            raise ValueError('invalid fact/evidence structure')
        reasons=[]
        if fact.get('fact_id') in previous_ids:reasons.append('previous_deferred_fact_requires_review')
        for evidence in fact.get('evidence',[]):
            quote=evidence.get('quote');source=sources.get(evidence.get('source_id'))
            if not isinstance(quote,str) or not source or len(quote.strip())<3 or quote not in source:
                reasons.append('quote_not_in_supplied_source')
        quotes=' '.join(e.get('quote','') for e in fact.get('evidence',[]) if isinstance(e.get('quote'),str))
        statement=fact.get('statement','')
        if isinstance(statement,str):
            # A conditional consequence needs a corresponding connective in the quote.
            # This is a narrow omission guard, not a general entailment proof.
            if '否则' in statement and not re.search(r'否则|不然|otherwise|or else|if\b[^.!?]*\bnot\b',quotes,re.I):
                reasons.append('conditional_consequence_not_in_quoted_evidence')
            terms=re.findall(r"(?:word|term) ['\"]?([A-Za-z]+)['\"]? derives\b",quotes,re.I)
            if any(term.casefold() not in statement.casefold() for term in terms):
                reasons.append('source_term_missing_in_etymology_statement')
            # Only a complete matching calendar date licenses the month translation.
            months='January February March April May June July August September October November December'.split()
            translated_dates=[]
            for month,day,year in re.findall(r'\b('+'|'.join(months)+r') (\d{1,2}), (\d{4})\b',quotes):
                date=f'{year}年{months.index(month)+1}月{int(day)}日'
                if date in statement:translated_dates.append(date)
            number_statement=statement
            for date in translated_dates:number_statement=number_statement.replace(date,'')
            missing=set(number.findall(number_statement))-set(number.findall(quotes))
            if missing:reasons.append('number_not_in_quoted_evidence:'+','.join(sorted(missing)))
            if 'billion years' in quotes and re.search(r'(?<!十)亿年',statement):
                reasons.append('unverified_billion_to_yi_conversion')
        if reasons:deferred.append({'fact':fact,'reasons':sorted(set(reasons)),'next_action':'verify_citation_and_quantity_against_source'})
        else:ready.append(fact)
    # Repeated review must not duplicate an identical pending candidate.
    unique={}
    for item in deferred:
        key=json.dumps(item['fact'],ensure_ascii=False,sort_keys=True)
        if key not in unique:unique[key]=item
        else:
            for reason in item.get('reasons',[]):
                if reason not in unique[key]['reasons']:unique[key]['reasons'].append(reason)
    result['facts']=ready;result['deferred_facts']=list(unique.values())
    return result


def retain_extraction_scope(result, passages):
    """A bounded model answer cannot declare the rest of a source out of scope."""
    return {**result,'model_coverage_note':result.get('coverage_note',''),
            'coverage_note':'本次最多6条机器候选，不表示已穷尽提供片段。地域、民族、词源、方法等有条件知识继续保留探索机会；模型范围意见未经审核，不作为排除依据。',
            'remaining_knowledge_review':[{'source_id':p['source_id'],'material_id':p.get('material_id'),
                'next_action':'continue_knowledge_review_in_supplied_passage','start':p['start'],'end':p['end'],
                'reason':'bounded_extraction_does_not_certify_complete_coverage'} for p in passages]}


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
