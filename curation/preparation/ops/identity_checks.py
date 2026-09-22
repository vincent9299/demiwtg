"""Validate identity decisions against the supplied source previews."""

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
