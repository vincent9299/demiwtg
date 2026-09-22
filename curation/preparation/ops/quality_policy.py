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
