"""Mechanical checks do not certify semantic truth or absence of real conflict."""
def require(ok,message):
    if not ok:raise ValueError(message)

def identity(result,materials,previews=None):
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

def knowledge(result,passages):
    sources={p['source_id']:p['text'] for p in passages};facts=result.get('facts')
    require(isinstance(facts,list) and len(facts)<=6,'facts must be a list of at most six')
    require(len({f['fact_id'] for f in facts})==len(facts),'duplicate fact IDs')
    for f in facts:
        require(isinstance(f.get('statement'),str) and bool(f['statement'].strip()),'empty knowledge statement')
        for key in ('conditions','exceptions'):
            require(isinstance(f.get(key),list) and all(isinstance(x,str) for x in f[key]),'conditions/exceptions must be string lists')
        require(isinstance(f.get('evidence'),list) and bool(f['evidence']),'source evidence required')
        for e in f['evidence']:
            require(e.get('source_id') in sources,'unknown evidence source')
            require(isinstance(e.get('quote'),str) and len(e['quote'].strip())>=3 and e['quote'] in sources[e['source_id']],'quote does not match input source')
    conflicts=result.get('unresolved_conflicts')
    require(isinstance(conflicts,list),'conflict dispositions required')
    for c in conflicts:
        require(bool(c.get('source_ids')) and set(c['source_ids'])<=set(sources),'conflict cites unknown/missing sources')
        require(isinstance(c.get('issue'),str) and isinstance(c.get('needed_evidence'),str),'conflict explanation missing')
    require(isinstance(result.get('coverage_note'),str),'coverage scope required')
    return result

def evidence(result,images,facts):
    wanted={(i['image_id'],f['fact_id']) for i in images for f in facts}
    rows=result.get('support');captions=result.get('images')
    require(isinstance(rows,list) and isinstance(captions,list),'evidence/captions must be lists')
    actual=[(r['image_id'],r['fact_id']) for r in rows]
    require(len(actual)==len(set(actual)) and set(actual)==wanted,'image-fact coverage must be exact')
    require(len(captions)==len(images) and {c['image_id'] for c in captions}=={i['image_id'] for i in images},'image caption IDs must be exact')
    for c in captions:require(isinstance(c.get('caption'),str),'caption missing')
    for r in rows:
        require(r.get('status') in ('full','partial','none','unobservable'),'invalid support status')
        require(all(isinstance(r.get(k),str) for k in ('region','supports','limitations')),'support explanation missing')
        if r['status'] in {'full','partial'}:
            require(all(r[k].strip() for k in ('region','supports','limitations')),'positive image support needs region, scope and limits')
    return result
