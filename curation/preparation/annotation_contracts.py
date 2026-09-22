"""Image description validation shared by current visual business operators."""

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
