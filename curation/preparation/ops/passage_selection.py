"""Select complete source blocks across sections, with exact omitted ranges."""
from collections import OrderedDict


def reference_notes(cleaning, blocks):
    definitions={r['reference_id']:r for b in cleaning['blocks'] for r in b.get('references',[])
                 if r.get('reference_id') and (r.get('content') or r.get('parameters'))}
    result={}
    for b in blocks:
        for r in b.get('references',[]):
            key=r.get('reference_id')
            if key:result[key]=definitions.get(key,r)
    return list(result.values())


def note_chars(notes):
    return sum(len(r.get('raw_text','')) for r in notes)


def select_passages(cleaning, budget):
    text=cleaning['text'];sections=OrderedDict()
    blocks=[b for b in cleaning['blocks'] if b.get('decision')=='keep' and 'clean_start' in b
            and b.get('kind')!='heading' and not b['text'].startswith('[Source ')]
    for b in blocks:
        sections.setdefault(tuple(b.get('section',[])),[]).append(b)
    # Round robin gives every represented section an opportunity. Whole blocks
    # preserve sentences, tables, and necessary conditions; oversized blocks wait.
    selected=[];remaining=budget
    queues=list(sections.values())
    while any(queues):
        for queue in queues:
            if not queue:continue
            b=queue.pop(0);cost=b['clean_end']-b['clean_start']+note_chars(reference_notes(cleaning,[b]))+2
            if cost<=remaining:selected.append(b);remaining-=cost
    selected.sort(key=lambda b:b['clean_start'])
    ranges=[]
    for b in selected:
        if ranges and b['clean_start']-ranges[-1][1]<=2:
            ranges[-1]=(ranges[-1][0],b['clean_end'])
        else:ranges.append((b['clean_start'],b['clean_end']))
    # Separators in merged runs count against the same input budget.
    while sum(e-a for a,e in ranges)>budget and ranges:
        selected.pop();ranges=[]
        for b in selected:
            if ranges and b['clean_start']-ranges[-1][1]<=2:ranges[-1]=(ranges[-1][0],b['clean_end'])
            else:ranges.append((b['clean_start'],b['clean_end']))
    omitted=[];cursor=0
    for a,e in ranges:
        if text[cursor:a].strip():omitted.append((cursor,a))
        cursor=e
    if text[cursor:].strip():omitted.append((cursor,len(text)))
    return ranges,omitted


def document_priority(material, relation='same_identity'):
    """Observable citation coverage, not a certification of publisher reliability."""
    refs=set()
    for b in material.get('cleaning',{}).get('blocks',[]):
        if b.get('decision')!='keep':continue
        refs.update(r['reference_id'] for r in b.get('references',[]) if r.get('reference_id'))
        refs.update(x['target'] for x in b.get('links',[]) if '#cite_note' in x.get('target','') or '#cite_ref' in x.get('target',''))
    from urllib.parse import urlsplit
    host=urlsplit(material.get('record',{}).get('url') or material.get('source_family','')).hostname or ''
    encyclopedia=host.endswith('.wikipedia.org') or host in {'baike.baidu.com','baike.so.com'}
    return {'direct_subject':relation=='same_identity','traceable_citations':len(refs),
            'encyclopedia_page':encyclopedia,
            'citation_band':min(len(refs),3),
            'scope':'Citation links and encyclopedia genre are selection clues only, not truth or independence approval. Other pages remain available for later exploration.'}
