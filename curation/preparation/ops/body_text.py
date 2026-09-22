"""Deterministic body admission: extraction, media separation and quality checks.

No generated text. Every retained block addresses the original downloaded source.
An optional callable classifier can return keep/exclude/defer + reason; absent by
 default. It classifies blocks, never supplies replacement text.
"""
import re
from importlib.metadata import version


def normalize(text):
    return re.sub(r'\s+', '', text)


def finish_body(raw, result, classifier=None):
    blocks = result['blocks']
    from curation.preparation.ops.page_sections import separate_auxiliary_sections
    separate_auxiliary_sections(blocks)
    extraction = {'engine': 'structured_source_parser', 'version': result['version']}
    extracted = None
    if result['source_format'] == 'html':
        from trafilatura import extract
        extracted = extract(raw, favor_precision=True, include_comments=False,
                            include_tables=True, include_links=False, include_images=False,
                            deduplicate=False, output_format="xml")
        extraction = {'engine': 'trafilatura', 'version': version('trafilatura'),
                      'favor_precision': True, 'include_tables': True,
                      'status': 'extracted' if extracted else 'no_body_found',
                      'text': extracted}
    if extracted:
        from lxml import etree
        normalized = normalize(''.join(etree.fromstring(extracted.encode()).itertext()))
    else:normalized = ''
    for b in blocks:
        if b['decision'] != 'keep':
            continue
        text = b['text']
        role=block_role(b)
        if role not in {'body_candidate','image_caption'}:
            b.update(decision='exclude',reason=role)
        elif b['kind'] == 'figure':
            b.update(decision='exclude', reason='image_caption_separated')
        elif extracted is not None and b['kind'] != 'heading' and normalize(text) not in normalized:
            # Match whole source blocks; never invent exact DOM/character alignment.
            b.update(decision='defer', reason='outside_extracted_body_or_partial_alignment')
        elif result['source_format'] == 'html' and not extracted and b['kind'] != 'heading':
            b.update(decision='defer', reason='body_extractor_found_no_body')
        elif text.strip() in {'[', ']'}:
            b.update(decision='exclude',reason='orphan_link_delimiter')
        elif re.search(r'!\[[^\]\n]*\]|<img\b',text,re.I):
            b.update(decision='defer',reason='unparsed_media_requires_cleaning')
        elif text.count('\ufffd') >= 3 and text.count('\ufffd') / max(1,len(text)) > .02:
            b.update(decision='defer',reason='decode_corruption')
        elif len(b.get('links',[])) >= 4 and len(re.sub(r'https?://\S+','',text)) < 120:
            b.update(decision='defer',reason='link_directory_requires_review')
        if b['decision']=='keep' and classifier is not None:
            decision=classifier({'text':text,'kind':b['kind'],'section':b.get('section',[])})
            if decision.get('decision') not in {'keep','exclude','defer'} or not decision.get('reason'):
                raise ValueError('Invalid text classifier decision')
            b['classifier_review']=decision
            b.update(decision=decision['decision'],reason=decision['reason'])
    # Do not collapse repeated statements across different section conditions.
    seen={}
    for b in blocks:
        if b['decision']!='keep' or b['kind']=='heading':continue
        key=(tuple(b.get('section',[])),b['kind'],b['text'])
        if key in seen:
            b.update(decision='exclude',reason='exact_duplicate_in_same_section',duplicate_of=seen[key])
        else:seen[key]=b['block_id']
    texts=[];offset=0
    for b in blocks:
        b.pop('clean_start',None);b.pop('clean_end',None)
        if b['decision']=='keep' and b['text']:
            if texts:offset+=2
            b['clean_start']=offset;offset+=len(b['text']);b['clean_end']=offset;texts.append(b['text'])
    result['text']='\n\n'.join(texts)
    result['extraction']=extraction
    result['media']=[{'block_id':b['block_id'],'raw_start':b['raw_start'],'raw_end':b['raw_end'],
                      'images':b['images'],'caption':b['text'],
                      'caption_status':'source_text_not_pixel_verified'} for b in blocks if b['kind']=='figure']
    result['supplements']=[{k:b[k] for k in ('block_id','raw_start','raw_end','raw_text','text','links','reason','content_role','boundary_anchor','related_image_block_id') if k in b}
                           for b in blocks if b.get('content_role') or b['reason']=='reference_section']
    result['counts'].update(output_chars=len(result['text']),
        excluded=sum(b['decision']=='exclude' for b in blocks),
        deferred=sum(b['decision']=='defer' for b in blocks),
        excluded_nonblank=sum(b['decision']=='exclude' and bool(b['raw_text'].strip()) for b in blocks))
    if result['status']!='unavailable' and not any(b['decision']=='keep' and b['kind']!='heading' and b['text'] for b in blocks):
        result['status']='needs_review';result['warnings']=sorted(set(result['warnings']+['no_body_blocks']))
    result['scope']='Program-cleaned source context; no concept relevance or factual verification.'
    return result


def block_role(block):
    """Observable page structure, not a domain/URL truth score.

    External citations inside prose are NOT rejected. References stay available
    via original blocks/reference_notes. A missing heading hierarchy is reported
    conservatively: '目录' alone does not exclude prose after a badly parsed TOC.
    """
    if block.get('kind') == 'figure':
        return 'image_caption'
    headings = {re.sub(r'\s+', ' ', h).strip().casefold() for h in block.get('section', [])}
    roles = {
        'related_section': {'相关星图', '相关推荐', '推荐阅读', '猜你喜欢', '相关文章', 'see also', 'related articles'},
        'reference_section': {'参考', '参考文献', '参考资料', 'references', '外部链接', 'external links'},
        'advertising_section': {'广告', 'advertisement', 'sponsored', 'sponsored content'},
    }
    for role, names in roles.items():
        if headings & names:
            return role
    text = block['text'].strip()
    if text in {'[编辑]', '[編輯]', '编辑', '編輯', '[edit]'}:
        return 'interface_control'
    if text.count('![') >= 2 and len(re.findall(r'\]\(https?://', text)) >= 2:
        return 'multiple_link_cards'
    return 'body_candidate'
