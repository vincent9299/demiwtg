"""Separate unmarked auxiliary panels using labels AND bounded structural evidence.

Does not treat all text after a matching keyword as boilerplate. Source blocks
and links remain available; a following prose paragraph stops each scan.
"""
import re

REFERENCE_LABELS={'参考资料','参考文献','references','bibliography','sources'}
GALLERY_LABELS={'词条图册更多图册','词条图册','图片库','图册','photo gallery','image gallery','gallery'}


def link_only(block):
    raw=block['raw_text'].strip()
    return bool(block.get('links') and re.fullmatch(r'\[[^\]\n]*\]\([^\n]+\)',raw))


def reference_entry(block):
    text=block['text'].strip()
    # A bare link is not enough: require recognizable citation structure.
    return bool(re.search(r'引用日期|\b(?:ISBN|doi|retrieved|accessed)\b',text,re.I)
                or (block.get('links') and re.search(r'\b(?:18|19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b',text)))


def separate_auxiliary_sections(blocks):
    for i,b in enumerate(blocks):
        if b['decision']!='keep':continue
        label=b['text'].strip().casefold()
        if label in GALLERY_LABELS:
            panel=[]
            for item in blocks[i+1:]:
                if not item['raw_text'].strip():continue
                if item['decision']!='keep' or item['kind']=='heading':break
                if link_only(item) and re.match(r'^\d+\s+\S',item['text']):panel.append(item)
                else:break
            if len(panel)>=2:
                for item in [b]+panel:
                    item.update(decision='exclude',reason='gallery_index_separated',content_role='gallery_index',
                                boundary_anchor=b['block_id'])
        elif label in REFERENCE_LABELS:
            panel=[];confirmed=False
            for item in blocks[i+1:]:
                if not item['raw_text'].strip():continue
                if item['decision']!='keep' or item['kind']=='heading':break
                if reference_entry(item):panel.append(item);confirmed=True
                elif re.fullmatch(r'(?:\*\s*)?\d+\.?',item['text'].strip()) or (not item['text'].strip() and item.get('links')):
                    panel.append(item)
                else:break
            if confirmed:
                for item in [b]+panel:
                    item.update(decision='exclude',reason='reference_list_separated',content_role='reference',
                                boundary_anchor=b['block_id'])
        # A short image count is media metadata only when adjacent to a figure.
        if b['decision']=='keep' and re.fullmatch(r'.{1,60}[（(]\d+\s*(?:张|幅|images?|photos?)[）)]',b['text'].strip(),re.I):
            previous=next((x for x in reversed(blocks[:i]) if x['raw_text'].strip()),None)
            if previous and previous['kind']=='figure':
                b.update(decision='exclude',reason='adjacent_image_count_separated',content_role='image_caption',
                         related_image_block_id=previous['block_id'])
