"""Document parsing, body extraction, quality rules and source-preserving repair."""
import re
import mwparserfromhell as mw
from demiflow.execution.artifacts import digest

PARSER_VERSION=mw.__version__
LAYOUT={'other uses','other uses of','about','redirect','redirect-distinguish','distinguish','hatnote',
        'good article','featured article','short description','cs1 config','reflist','notelist',
        'commons category','commonscat','sister bar','authority control','portal bar','clear','toc limit',
        'use mdy dates','use dmy dates','use american english','use british english','pp-semi-indef',
        'cosmology','cosmology topics','earth\'s location','fundamental interactions','nature nav',
        'defaultsort','spoken wikipedia','main','see also','further','cat main'}


def markup_spans(raw):
    """Line blocks except when a syntactic node crosses the line boundary."""
    protected=[];cursor=0
    for node in mw.parse(raw).nodes:
        end=cursor+len(str(node))
        if type(node).__name__ not in {'Text','Heading'} and '\n' in str(node):protected.append((cursor,end))
        cursor=end
    ends=[];j=0
    for match in re.finditer('\n',raw):
        end=match.end()
        while j<len(protected) and protected[j][1]<=end:j+=1
        if j==len(protected) or not protected[j][0]<end<protected[j][1]:ends.append(end)
    if not ends or ends[-1]!=len(raw):ends.append(len(raw))
    start=0
    for end in ends:
        yield start,end
        start=end


def render(fragment):
    result={'references':[],'links':[],'images':[],'unsupported':[],'templates':[]}
    def code(value):return ''.join(node(n) for n in mw.parse(str(value)).nodes)
    def node(n):
        kind=type(n).__name__
        if kind=='Text':return str(n)
        if kind=='Heading':return code(n.title).strip()
        if kind=='Comment':return ''
        if kind=='HTMLEntity':return n.normalize()
        if kind=='Wikilink':
            target=str(n.title);label=str(n.text or n.title)
            if target.lower().startswith(('file:','image:','文件:','图像:')):
                result['images'].append({'target':target,'label':label,'kind':'source_image'})
                parts=label.split('|');return code(parts[-1]) if len(parts)>1 else ''
            if target.lower().startswith(('category:','分类:')):
                result['links'].append({'target':target,'kind':'source_category'});return ''
            result['links'].append({'target':target,'kind':'wiki_link'});return code(n.text or n.title)
        if kind=='ExternalLink':
            result['links'].append({'target':str(n.url),'kind':'external_reference'});return code(n.title) if n.title else str(n.url)
        if kind=='Tag':
            tag=str(n.tag).lower();attrs={str(a.name):str(a.value or '') for a in n.attributes}
            if tag=='ref':
                name=attrs.get('name');rid='R'+digest({'name':name,'group':attrs.get('group','')} if name else str(n))[:12]
                result['references'].append({'reference_id':rid,'name':name,'raw_text':str(n),'content':str(n.contents or '')})
                return '['+rid+']'
            if tag in {'references','gallery'}:
                result['references'].append({'raw_text':str(n),'kind':tag});return ''
            if tag in {'script','style'}:return ''
            if tag in {'br','hr'}:return '\n'
            if tag in {'sup','sub'}:return ('^' if tag=='sup' else '_')+'('+code(n.contents)+')'
            if tag in {'math','chem','ce'}:return str(n.contents)
            if tag in {'b','i','strong','em','span','div','p','small','big','blockquote','poem','center','s','u'}:
                return code(n.contents) if n.contents is not None else ''
            result['unsupported'].append(str(n));return str(n)
        if kind!='Template':
            result['unsupported'].append(str(n));return str(n)
        name=str(n.name).strip().replace('_',' ').casefold()
        params={str(p.name).strip():str(p.value).strip() for p in n.params}
        result['templates'].append({'name':name,'parameters':params,'raw_text':str(n)})
        if name in {'other uses','other uses of','about','redirect','redirect-distinguish','distinguish','hatnote','main','see also','further','cat main'}:
            return '[Source '+name+': '+ '; '.join(k+'='+code(v) for k,v in params.items())+']'
        if name=='short description' and set(params)<={'1'}:return code(params.get('1',''))
        if name in LAYOUT or name.startswith(('pp-','use ')):return ''
        if name in {'nowrap','nobr','small','smaller','big','larger','center','c','lang'}:
            if name=='lang':return code(params.get('2',''))
            if set(params)<={'1'}:return code(params.get('1',''))
        if name in {'val','value'} and set(params)<={'1','2','e','u','ul'} and '1' in params:
            text=code(params['1'])
            if '2' in params:text+=' ± '+code(params['2'])
            if 'e' in params:text+=' × 10^('+code(params['e'])+')'
            return text+(' '+code(params.get('u',params.get('ul',''))) if 'u' in params or 'ul' in params else '')
        if name in {'convert','cvt'} and set(params)<={'1','2'} and '1' in params and '2' in params:
            return code(params['1'])+' '+code(params['2'])
        if name=='infobox':
            lines=[]
            if params.get('title'):lines.append(code(params['title']))
            supported={'title','image','caption'}
            for key in params:
                if re.fullmatch(r'label\d+',key):
                    index=key[5:];supported.update({key,'data'+index})
                    if 'data'+index in params:lines.append(code(params[key])+': '+code(params['data'+index]))
            if set(params)-supported:
                result['unsupported'].append(str(n))
            if params.get('image'):result['images'].append({'target':params['image'],'caption':params.get('caption',''),'kind':'infobox_image'})
            if params.get('caption'):lines.append('Image caption: '+code(params['caption']))
            return '\n'.join(lines)
        if name in {'citation needed','cn','clarify','clarification needed','dubious','failed verification'}:
            return '['+name+']'
        if name.startswith(('cite ','citation')):
            rid='R'+digest(str(n))[:12];result['references'].append({'reference_id':rid,'raw_text':str(n),'parameters':params});return '['+rid+']'
        result['unsupported'].append(str(n));return str(n)
    result['text']=code(fragment)
    return result


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


from importlib.metadata import version


def normalize(text):
    return re.sub(r'\s+', '', text)


def finish_body(raw, result, classifier=None):
    blocks = result['blocks']
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


from html.parser import HTMLParser

VERSION='body-extraction/6'
NAV=re.compile(r'^(?:登录|注册|搜寻|搜索|首页|跳转到主要内容|跳转到内容|返回首页|主菜单|导航|使用者帐号选单|成为会员|关于我们|联系我们|隐私政策|服务条款|登录以继续|sign in|sign up|log in|login with facebook|join fanpop|privacy policy|terms of (?:use|service)|all rights reserved|skip to (?:main )?content|forgot your password or username\??|keep me signed in|returning user\s+new user|fanpop is currently available only for logged in users)[。.!！:]?$',re.I)
LAYOUT_TEMPLATE=re.compile(r'^\{\{(?:pp-[\w-]+|use (?:american|british) english|use mdy dates|use dmy dates|good article|featured article)(?:\|[^{}]*)?\}\}$',re.I)
QUALITY=re.compile(r'citation needed|需要更多来源|来源请求|可靠来源|clarification needed',re.I)
MD_LINK=re.compile(r'(!?)\[([^\]\n]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)')
WIKI_LINK=re.compile(r'\[\[([^\[\]\n]+)\]\]')
URL=re.compile(r'https?://[^\s<>"\]]+')


def raw_text(material):
    r=material['record']
    if material['kind']=='legacy_docs':return material.get('document',{}).get('text','')
    if material['kind']=='wiki_pages':
        return '\n\n'.join((s.get('title','')+'\n'+s.get('text','')).strip() for s in r.get('sections',[]))
    return ''


class RenderHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.links=[];self.images=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in {'script','style'}:self.hidden+=1
        if tag in {'p','div','tr','li','br','h1','h2','h3','h4','h5','h6'}:self.parts.append('\n')
        if tag in {'td','th'}:self.parts.append('\t')
        if tag=='a' and a.get('href'):self.links.append({'target':a['href'],'kind':'html_link'})
        if tag=='img':self.images.append({'target':a.get('src'),'alt':a.get('alt'),'kind':'source_image'})
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
        if tag in {'p','div','tr','li','table','h1','h2','h3','h4','h5','h6'}:self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


class HTMLBlocks(HTMLParser):
    capture={'p','h1','h2','h3','h4','h5','h6','table','ul','ol','figure','pre','blockquote',
             'nav','footer','header','form','script','style','aside','title'}
    exclude={'nav','footer','form','script','style'}
    void={'br','hr','img','input','meta','link','wbr','source','area','base','embed','param','track','col'}
    def __init__(self,text):
        super().__init__(convert_charrefs=False);self.text=text;self.starts=[0]
        self.starts.extend(m.end() for m in re.finditer('\n',text))
        self.active=None;self.depth=0;self.blocks=[]
    def source_offset(self):
        line,col=self.getpos();return self.starts[line-1]+col
    def handle_starttag(self,tag,attrs):
        start=self.source_offset()
        if self.active:
            if tag not in self.void:self.depth+=1
        elif tag in self.capture:
            self.active=(start,tag);self.depth=1
        elif tag=='img':self.blocks.append((start,start+len(self.get_starttag_text()),'figure',False))
    def handle_startendtag(self,tag,attrs):
        if not self.active and tag=='img':
            start=self.source_offset();self.blocks.append((start,start+len(self.get_starttag_text()),'figure',False))
    def handle_endtag(self,tag):
        if self.active and tag not in self.void:
            self.depth-=1
            if self.depth<=0:
                start,kind=self.active;end=self.text.find('>',self.source_offset())+1
                self.blocks.append((start,end,kind,kind in self.exclude));self.active=None
    def handle_data(self,data):
        if not self.active and data.strip():
            start=self.source_offset();self.blocks.append((start,start+len(data),'text',False))
    def handle_entityref(self,name):
        if not self.active:
            start=self.source_offset();self.blocks.append((start,start+len(name)+2,'text',False))
    def handle_charref(self,name):
        if not self.active:
            start=self.source_offset();self.blocks.append((start,start+len(name)+3,'text',False))


def clean_document(raw, source_format=None, title=None, source_sections=None, classifier=None):
    is_html=bool(re.search(r'^\s*<(?:!doctype\s+html|html|body|article|main|div|p|table)(?:\s|>)',raw,re.I))
    fmt='html' if is_html else (source_format or ('wikitext' if re.search(r'^={2,6}.+={2,6}\s*$',raw,re.M) or '{{' in raw else 'markdown_or_text'))
    spans=[];warnings=[]
    section_starts={};section_cursor=0
    for section in source_sections or []:
        part=(section.get('title','')+'\n'+section.get('text','')).strip()
        if section.get('title'):section_starts[section_cursor]=section['title']
        section_cursor+=len(part)+2
    if re.search(r'only for logged in users|access denied|verify you are human|请登录后查看|登录后可见|验证码',raw,re.I):
        warnings.append('access_restriction_or_challenge_marker')
    if not is_html and re.search(r'<(?:div|table|script|style)(?:\s|>)',raw,re.I):
        warnings.append('mixed_html_markup_retained')
    if is_html:
        parser=HTMLBlocks(raw);parser.feed(raw);parser.close();spans=parser.blocks
        if parser.active:
            start,tag=parser.active;spans.append((start,len(raw),tag,False));warnings.append('malformed_html_tail_retained')
    else:
        cursor=0
        for cursor,end in markup_spans(raw):
            line=raw[cursor:end];s=line.strip()
            kind='paragraph'
            if cursor in section_starts or re.match(r'^#{1,6}\s|^={2,6}.+={2,6}$',s):kind='heading'
            elif s.startswith('|') or s.startswith('{|') or s=='|}':kind='table'
            elif re.match(r'^(?:[-*+]\s|\d+[.)]\s)',s):kind='list'
            elif s.startswith('![') or s.startswith('[[File:') or s.startswith('[[文件:'):kind='figure'
            elif QUALITY.search(s):kind='quality_note'
            elif s.startswith('{{'):kind='template'
            media=re.search(r'!\[[^\]\n]*\]|\[\[(?:File|Image|文件|图像):',line,re.I)
            if media and media.start()>0 and line[:media.start()].strip():
                split=cursor+media.start()
                if media.start()>0 and line[media.start()-1]=='[':split-=1
                spans.append((cursor,split,kind,False))
                spans.append((split,end,'figure',False))
            else:spans.append((cursor,end,kind,False))
            cursor=end
    blocks=[];texts=[];offset=0;headings=[]
    for start,end,kind,exclude in spans:
        fragment=raw[start:end];s=fragment.strip();links=[];images=[]
        reason='retained';decision='keep'
        if exclude:decision='exclude';reason='html_'+kind
        if not s:decision='exclude';reason='blank'
        if is_html:
            html_renderer=RenderHTML();html_renderer.feed(fragment);html_renderer.close()
            text=''.join(html_renderer.parts).strip();links=html_renderer.links;images=html_renderer.images
            if kind.startswith('h') and kind[1:].isdigit():kind='heading'
            elif kind in {'ul','ol'}:kind='list'
            elif kind=='figure':kind='figure'
            elif kind in {'header','aside'}:warnings.append('auxiliary_html_retained')
        else:
            markup=None
            if '{{' in s or '[[' in s or re.search(r'<(?:ref|math|sup|sub)[ >]',s):
                markup=render(fragment);text=markup['text'].strip()
                links.extend(markup['links']);images.extend(markup['images'])
                if markup['unsupported']:
                    decision='defer';reason='unsupported_semantic_markup'
                    warnings.append('partial_markup_deferred')
            else:text=s
            # Remove only a whole, exact navigation line. Keywords within prose are retained.
            if NAV.fullmatch(re.sub(r'^[-*+]\s+','',s)):
                decision='exclude';reason='exact_navigation_line'
            elif LAYOUT_TEMPLATE.fullmatch(s):decision='exclude';reason='layout_template'
            def md_link(m):
                target={'target':m[3],'label':m[2],'kind':'source_image' if m[1] else 'markdown_link'}
                (images if m[1] else links).append(target)
                return m[2]
            text=MD_LINK.sub(md_link,text)
            text=re.sub(r'(?m)^\s*\[(?:编辑|編輯|edit)\]\s*', '', text)
            def wiki_link(m):
                pieces=m[1].split('|');target=pieces[0]
                item={'target':target,'label':pieces[-1],'kind':'wiki_link'}
                if target.lower().startswith(('file:','image:','文件:','图像:')):
                    images.append(item);return pieces[-1] if len(pieces)>1 else target
                links.append(item);return pieces[-1]
            text=WIKI_LINK.sub(wiki_link,text)
            if '{{' in text and decision=='keep':warnings.append('unexpanded_wiki_template_retained')
            if kind=='heading':text=re.sub(r'^#{1,6}\s+|^=+\s*|\s*=+$','',text)
            for u in URL.findall(s):
                if not any(x['target']==u for x in links+images):links.append({'target':u,'kind':'bare_url'})
        if not is_html and re.search(r'\{\{|\}\}|\[\[|\]\]|</?ref\b',re.sub(r'`[^`]*`','',text)) and decision=='keep':
            decision='defer';reason='unparsed_markup_fragment';warnings.append('partial_markup_deferred')
        if NAV.fullmatch(re.sub(r'^[-*+]\s+','',text.strip())):
            decision='exclude';reason='exact_navigation_line'
        if kind=='heading' and decision=='keep':headings=[text]
        block={'block_id':'B'+digest({'start':start,'end':end,'raw':fragment})[:12],
               'kind':kind,'raw_start':start,'raw_end':end,'raw_text':fragment,
               'text':text,'decision':decision,'reason':reason,'section':list(headings),
               'links':links,'images':images,'alignment':'whole_source_block'}
        if not is_html and markup is not None:
            block['references']=markup['references'];block['templates']=markup['templates'];block['unsupported_markup']=markup['unsupported']
        if decision=='keep' and text:
            if texts:offset+=2
            block['clean_start']=offset;offset+=len(text);block['clean_end']=offset;texts.append(text)
        blocks.append(block)
    # Apply bounded, structural wrapper rules to rendered blocks, then rebuild
    # offsets. Original blocks, links and images survive every exclusion.
    def exclude_block(b,reason):
        b['decision']='exclude';b['reason']=reason
    def plain(t):
        return re.sub(r'[\W_]+','',t).casefold()
    title_parts=re.split(r'--|\s+[-–—|]\s+',title or '')
    title_variants={plain(' '.join(title_parts[:i])) for i in range(1,len(title_parts)+1)}
    title_variants.update(plain(x) for x in re.findall(r'《([^》]+)》',title or ''))
    normalized_title=plain(title_parts[0])
    for i in range(1,len(blocks)):
        if re.fullmatch(r'#{1,6}',blocks[i-1]['text'].strip()) and blocks[i]['text'].strip():blocks[i]['kind']='heading'
    heading=next((i for i,b in enumerate(blocks) if b['kind']=='heading' and any(t and (plain(b['text'])==t or len(t)>20 and plain(b['text']).endswith(t)) for t in title_variants)),None)
    # Require both a known article heading and several UI signatures. Do not
    # infer main text merely from paragraph length or a knowledge keyword.
    if heading is not None and blocks[heading]['kind']=='heading':
        prefix='\n'.join(b['text'] for b in blocks[:heading])
        markers=['用户登录','按内容 按标题 按作者','旧版','Main menu','Toggle the table of contents',
                 'Customise Consent Preferences','Revisit consent button','Save My Preferences',
                 '使用者帐号选单','会员登录','主導覽','Stolen Accounts Recovery','Change Account Password','LoginSign up']
        if not any(len(b['text'])>300 and not b['links'] for b in blocks[:heading]) and (sum(m in prefix for m in markers)>=2 or sum(bool(b['links']) and len(b['text'])<100 for b in blocks[:heading])>=4):
            for b in blocks[:heading]:exclude_block(b,'navigation_before_article_heading')
    if 'baike.baidu.com' in raw and '个人中心' in raw:
        start=next((i for i,b in enumerate(blocks) if b['kind']=='heading'),None)
        if start is not None:
            for b in blocks[:start]:exclude_block(b,'baike_interface_before_title')
            for i in range(start+1,len(blocks)):
                if blocks[i]['text'].strip() in {'词条统计','新手上路'}:
                    for b in blocks[i:]:exclude_block(b,'baike_footer')
                    break
        for b in blocks:
            if b['text'].strip() in {'播报','编辑'} or re.fullmatch(r'播报编辑(?:讨论)?[0-9]*(?:上传视频|收藏赞)?',b['text'].strip()):
                exclude_block(b,'baike_edit_control')
    # Cookie consent has an explicit start/end pair; no open-ended deletion.
    cookie_start=next((i for i,b in enumerate(blocks) if b['text']=='Revisit consent button'),None)
    if cookie_start is not None:
        cookie_end=next((i for i in range(cookie_start+1,len(blocks)) if
            'Save My Preferences' in blocks[i]['text'] and 'Accept All' in blocks[i]['text']),None)
        if cookie_end is not None:
            for b in blocks[cookie_start:cookie_end+1]:exclude_block(b,'cookie_consent_panel')
    # Exact recommendation headings after substantial article text identify
    # an appended site panel, not references or scientific section headings.
    body_chars=0
    for i,b in enumerate(blocks):
        if b['text'].strip().casefold() in {'大家都在看','latest posts','大家都在搜','推荐阅读','更多推荐','you may also like','related posts','related articles','you might also like','您可能也对以下帖子感兴趣','相关诗文','那些年你们错过的热点'} and body_chars>0:
            following=blocks[i+1:i+20]
            if sum(bool(x['links'] or x['images']) for x in following)>=2:
                for tail in blocks[i:]:exclude_block(tail,'appended_recommendation_panel')
                break
        if b['decision']=='keep':body_chars+=len(b['text'])
    # MediaWiki exports have an explicit end-of-chrome marker. Keep the
    # infobox, disambiguation notes and references after it, including short rows.
    joined='\n'.join(b['text'] for b in blocks)
    wiki_ui=('Main menu' in joined and 'View history' in joined) or ('移至侧栏 隐藏' in joined and '查看历史' in joined)
    if wiki_ui or re.search(r'https?://(?:[a-z-]+\.)?(?:wikipedia|wikisource)\.org/',raw):
        boundary=next((i for i,b in enumerate(blocks) if b['text'].strip() in {
            'From Wikipedia, the free encyclopedia','来自维基百科，自由的百科全书',
            '出自维基文库，自由的图书馆','来自维基文库，自由的图书馆','维基百科，自由的百科全书','维基文库，自由的图书馆','出自维基百科，自由个百科全书','De Wikipedia, la enciclopedia libre'}),None)
        if boundary is not None:
            for b in blocks[:boundary+1]:exclude_block(b,'mediawiki_interface_before_body')
        for i,b in enumerate(blocks):
            if re.match(r'^(?:Retrieved from|(?:检索自|取自)[“"]https?://|Obtenido de|This page was last edited on|本页面最后修订于|本页面最后编辑于|此页面最后编辑于)',re.sub(r'^[-*+]\s+','',b['text'].strip())):
                for tail in blocks[i:]:exclude_block(tail,'mediawiki_footer')
                break
    # MediaWiki navigation boxes are identified by their view/discuss/edit
    # controls and table opener. Stop at the next actual article heading.
    for i in range(1,len(blocks)-2):
        labels=[b['text'].strip() for b in blocks[i:i+3]]
        if labels not in [['* 查','* 论','* 编'],['* v','* t','* e']]:continue
        if not blocks[i-1]['text'].lstrip().startswith('|'):continue
        end=next((j for j in range(i+3,len(blocks)) if blocks[j]['kind']=='heading'
                  or blocks[j]['reason']=='mediawiki_footer'),len(blocks))
        for b in blocks[i-1:end]:
            if b['decision']!='defer':exclude_block(b,'mediawiki_navigation_box')
    # Zhihu's duplicated title and comment controls provide bounded regions.
    if 'ZhiHu logo' in joined and normalized_title:
        titles=[i for i,b in enumerate(blocks) if plain(b['text'])==normalized_title]
        if titles:
            for b in blocks[:titles[-1]]:exclude_block(b,'zhihu_interface_before_title')
            for i in range(titles[-1]+1,len(blocks)):
                if re.search(r'赞同\s*\d+.*条评论',blocks[i]['text']):
                    for tail in blocks[i:]:exclude_block(tail,'zhihu_post_controls')
                    break
                if re.fullmatch(r'\d+\s*条评论',blocks[i]['text'].strip()):
                    window=' '.join(b['text'] for b in blocks[i+1:i+6])
                    if '默认' in window and '最新' in window:
                        for tail in blocks[i:]:exclude_block(tail,'zhihu_comments_and_sidebar')
                        break
    # Repeated site menu after the article: match a sequence seen in the
    # removed prefix, not arbitrary repeated prose inside the article.
    if heading is not None:
        prefix=[b['text'].strip() for b in blocks[:heading] if b['text'].strip()]
        suffix=[(i,b['text'].strip()) for i,b in enumerate(blocks) if i>heading and b['text'].strip()]
        signatures={tuple(prefix[i:i+5]) for i in range(max(0,len(prefix)-4))
                    if sum(x.startswith('*') for x in prefix[i:i+5])>=3}
        for j in range(len(suffix)-4):
            if tuple(t for _,t in suffix[j:j+5]) in signatures:
                for b in blocks[suffix[j][0]:]:exclude_block(b,'repeated_site_navigation')
                break
    if 'steemit.com' in raw and 'Stolen Accounts Recovery' in joined:
        for i,b in enumerate(blocks):
            if re.match(r'^(?:\d+ (?:years|months|days) ago in|\$[0-9])',b['text'].strip()) and heading is not None and i>heading:
                for tail in blocks[i:]:exclude_block(tail,'steemit_post_controls_and_comments')
                break
    # A subscription panel has a paired plan heading and savings footnote.
    # Keep the film synopsis, rating, duration and accessibility warning.
    if 'disneyplus.com/commerce/plans' in raw:
        plan_start=next((i for i,b in enumerate(blocks) if b['text'].strip()=='* DISNEY+ ANNUAL'),None)
        plan_end=next((i for i,b in enumerate(blocks) if b['text'].startswith('*Savings compared to 12 months')),None)
        if plan_start is not None and plan_end is not None and plan_start<plan_end:
            for b in blocks[plan_start:plan_end+1]:exclude_block(b,'subscription_panel')
        for b in blocks:
            if b['text'].strip() in {'GET DISNEY+LOG IN','GET DISNEY+','Disney+ Logo'}:
                exclude_block(b,'subscription_control')
    # Sharing controls are metadata, not article content. Require an exact
    # control label or javascript-only link, never remove ordinary link prose.
    for b in blocks:
        if b['text'].strip() in {'分享到：','分享到:','* Share','* Tweet','* Pin','* Email'} or (b['links'] and all(x.get('target','').startswith('javascript:') for x in b['links'])):
            exclude_block(b,'share_or_javascript_control')
    for i,b in enumerate(blocks):
        if b['text'].strip().startswith('Sign Up For ') and 'Newsletter' in b['text'] and i+1<len(blocks) and blocks[i+1]['text'].strip()=='Sign Up':
            exclude_block(b,'newsletter_control');exclude_block(blocks[i+1],'newsletter_control')
        if b['text'].strip().startswith('Previous Post ') and 'Next Post ' in b['text'] and len(b['links'])>=2:exclude_block(b,'adjacent_post_navigation')
    access_error=bool(len(raw)<4000 and re.search(r'^\s*(?:#{1,6}\s*)?Access Denied\s*$',raw,re.M|re.I)
                      and re.search(r'access.*(?:blocked|denied)|blocked for possible abuse',raw,re.I))
    if access_error:
        for b in blocks:exclude_block(b,'access_error_page')
        warnings.append('access_error_page_no_body')
    texts=[];offset=0
    for b in blocks:
        b.pop('clean_start',None);b.pop('clean_end',None)
        if b['decision']=='keep' and b['text']:
            if texts:offset+=2
            b['clean_start']=offset;offset+=len(b['text']);b['clean_end']=offset;texts.append(b['text'])
    text='\n\n'.join(texts)
    # Warnings about removed UI/template blocks must not keep clean body stuck.
    if not access_error and not ('Username:' in text and 'Password:' in text) and not re.search(r'only for logged in users|access denied|verify you are human|请登录后查看|登录后可见|验证码',text,re.I):
        warnings=[w for w in warnings if w!='access_restriction_or_challenge_marker']
    if '{{' not in re.sub(r'`[^`]*`','',text):warnings=[w for w in warnings if w!='unexpanded_wiki_template_retained']
    if len(text)<500 and re.search(r'(?:\.\.\.|…)(?:[\s]*$|\s*(?:详情|More|Read more))',text):warnings.append('possible_truncated_snippet')
    if 'partial_markup_deferred' in warnings and len(text)<300:
        warnings.append('partial_body_context_requires_review')
    # A mixed-script, unidentified-language short page is sent for language
    # review, not labelled garbage or permanently excluded by an English test.
    words=re.findall(r'\b[A-Za-z]{2,}\b',text.lower())
    cjk=len(re.findall(r'[\u4e00-\u9fff]',text))
    common={'the','a','an','is','are','and','of','in','to','for','with','this','that','it','by','from'}
    if 150<len(text)<1200 and len(words)>35 and cjk<10 and sum(w in common for w in words)<=max(1,len(words)//50):
        warnings.append('mixed_script_language_review')
    residual={'移至侧栏 隐藏','查看历史','Main menu','Toggle the table of contents','Jump to content','Customise Consent Preferences','大家都在搜','登录/注册'}
    if any(b['decision']=='keep' and b['text'].strip() in residual for b in blocks):warnings.append('residual_navigation_requires_review')
    kept='\n'.join(b['text'] for b in blocks if b['decision']=='keep')
    ui_signals=['进入词条全站搜索','首页 PATREON','🔥 热搜','Create a Free Account','Site Notice',
                'Wishlist','Add To Cart','加入潮流粉丝俱乐部','Menú principal','移至侧栏 囥起來',
                'Join Fanpop','HomeHome','ServicesServices','新手上路','订阅词条']
    if any(x in kept for x in ui_signals):warnings.append('residual_navigation_requires_review')
    if 'not yet available in your area' in kept:
        warnings.append('access_restriction_or_challenge_marker')
    prose=[b for b in blocks if b['decision']=='keep' and b['kind'] in {'paragraph','text','p','list','table','blockquote','pre'} and b['text']]
    if not prose:warnings.append('no_body_blocks')
    result = {'version':VERSION,'source_format':fmt,'source_sha256':digest(raw.encode()),
            'text':text,'blocks':blocks,'warnings':sorted(set(warnings)),
            'status':'unavailable' if access_error else 'needs_review' if set(warnings)-{'partial_markup_deferred'} or not text else 'cleaned_candidate',
            'counts':{'input_chars':len(raw),'output_chars':len(text),'blocks':len(blocks),
                      'excluded':sum(b['decision']=='exclude' for b in blocks),
                      'deferred':sum(b['decision']=='defer' for b in blocks),
                      'excluded_nonblank':sum(b['decision']=='exclude' and bool(b['raw_text'].strip()) for b in blocks)},
            'scope':'Conservative structural cleaning, not identity or factual verification.'}
    return finish_body(raw,result,classifier=classifier)


import copy
import unicodedata
from collections import Counter

STOP = set('the be to of and that have with'.split())
COMMERCE = re.compile(r'加入购物车|立即购买|月销|包邮|运费|退款|售后|订单|优惠|折扣|配送|发货|批发|采购|专营店|商城|价格|购物|评价|好评|会员|购买|add to cart|buy now|delivery|shipping|wholesale|shop from|choose from our selection|discount', re.I)
CHALLENGE = re.compile(r'confirm you are human|verify you are human|security check|solve a puzzle|access denied|验证码|人机验证', re.I)
CONTROL = re.compile(r'^(?:立即打开|查看全部|相关视频|目录|分享|收藏|确认|切换|数量|请选择|登录|注册|Begin|Home|Menu|Sign in|Sign up|Read more|相关推荐|猜你喜欢|參見|参见|參考來源|参考来源)$',re.I)
POLICY = re.compile(r'^(?:隐私政策|服务条款|使用条款|cookie policy|privacy policy|terms of (?:use|service)|accept all cookies)$',re.I)
REFERENCES = {'參見','参见','參考來源','参考来源','參考資料','参考资料','references','see also','external links'}


def normalized(s):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC',s)).strip()


class FilterSourceQuality:
    """Apply the source-preserving production quality rules."""
    def __call__(self,row):
        text=row['clean_text'];raw=row['raw_text'];blocks=copy.deepcopy(row['clean_blocks'])
        # A shared '/item/' route is also used by encyclopedias; it is not a product signal.
        retail=len(COMMERCE.findall((row.get('title') or '')))>=2 or bool(re.search(r'/products?/|/offer/|/selloffer/',(row.get('url') or ''),re.I))
        retail=retail or (len(COMMERCE.findall(raw))>=6 and len(COMMERCE.findall(text))>=10)
        document_reason=''
        if CHALLENGE.search(text) and len(text)<2000:document_reason='access_challenge_not_article'
        if 'mixed_script_language_review' in row['clean_warnings']:document_reason='unidentified_language_requires_review'
        if 'possible_truncated_snippet' in row['clean_warnings']:document_reason='truncated_capture_requires_full_page'
        seen={}
        for b in blocks:
            if b['decision']!='keep':continue
            t=b['text'].strip();reason='';hits=len(COMMERCE.findall(t))
            if document_reason:reason=document_reason
            elif any(s.casefold() in REFERENCES for s in b.get('section',[])):reason='reference_or_related_section'
            elif POLICY.fullmatch(t) or CONTROL.fullmatch(t):reason='interface_or_policy_control'
            elif re.search(r'本站.*(?:不提供任何保证|不承担任何法律责任)|all rights reserved|版权所有|免责声明|权利声明',t,re.I):reason='legal_boilerplate'
            elif re.search(r'^(?:\d+[.、]\s*)?(?:点击|发送给).*(?:分享|好友|朋友圈|空间)',t):reason='sharing_instructions'
            elif re.fullmatch(r'https?://\S+',t):reason='standalone_url'
            elif CHALLENGE.search(t):reason='access_challenge'
            elif '[广告]' in t or re.match(r'^(广告|advertisement|sponsored)\b',t,re.I):reason='explicit_advertisement'
            elif hits>=2 or (retail and hits):reason='transaction_or_marketing_block'
            elif retail and b['kind']!='heading' and not re.search(r'[。！？.!?]',t):reason='catalog_fragment_without_explanation'
            elif re.fullmatch(r'[\W\d_]+',t):reason='symbols_or_numeric_control'
            elif len(b.get('links',[]))>=4 and len(t)<150:reason='dense_link_directory'
            elif re.match(r'^\d+[.、]\s*\d+[^。！？]{0,30}$',t):reason='table_of_contents_entry'
            if reason:b.update(decision='exclude',reason=reason)
        # Remove empty headings without dropping compact lists/tables under real headings.
        for i,b in enumerate(blocks):
            if b['decision']!='keep' or b['kind']!='heading':continue
            subsequent=[]
            for x in blocks[i+1:]:
                if x['kind']=='heading':break
                if x['decision']=='keep' and x['text'].strip():subsequent.append(x)
            if not subsequent:b.update(decision='exclude',reason='heading_without_retained_content')
        retained=[b for b in blocks if b['decision']=='keep' and b['kind']!='heading' and b['text'].strip()]
        # A transaction page needs at least one explanatory block, not just a product title.
        if retail and not any(len(b['text'])>=60 and re.search(r'[。！？.!?]',b['text']) and not COMMERCE.search(b['text']) for b in retained):
            document_reason=document_reason or 'transaction_page_without_explanatory_body'
            for b in blocks:
                if b['decision']=='keep':b.update(decision='exclude',reason=document_reason)
        rule_text='\n\n'.join(b['text'] for b in blocks if b['decision']=='keep')
        dedup=copy.deepcopy(blocks)
        for b in dedup:
            if b['decision']!='keep' or b['kind']=='heading':continue
            key=(tuple(b.get('section',[])),normalized(b['text']))
            if key in seen:b.update(decision='exclude',reason='normalized_duplicate_same_section',duplicate_of=seen[key])
            else:seen[key]=b['block_id']
        return {**row,'quality_blocks':blocks,'dedup_blocks':dedup,
                'filter_diagnostics':{'transaction_page_signal':retail,'document_reason':document_reason}}


import hashlib

CITATION = re.compile(r'(?P<mark>\[?\d+(?:[-–,]\d+)*\]?)\((?P<url>https?://[^\s)]+#(?:cite_note|cite_ref|ref[-_])[^\s)]*)\)')
TAIL = re.compile(r'(?P<marker>详情\s*>>|詳細\s*>>|Read more\s*>>)(?P<tail>[^。！？.!?\n]{0,180})$',re.I)


def repair_piece(text):
    """Return nonoverlapping source-coordinate edits, preserving substantive numbers."""
    edits=[]
    for m in CITATION.finditer(text):
        edits.append({'start':m.start(),'end':m.end(),'removed':m.group(),'replacement':'['+m['mark'].strip('[]')+']','reason':'reference_url_moved_to_citation','url':m['url']})
    m=TAIL.search(text)
    if m and re.search(r'[。！？.!?]\s*$',text[:m.start()]) and len(re.split(r'\s+[-|·]\s+',m['tail'].strip()))>=2:
        edits.append({'start':m.start(),'end':m.end(),'removed':m.group(),'replacement':'','reason':'navigation_after_complete_sentence'})
    out=text
    for e in reversed(sorted(edits,key=lambda e:e['start'])):out=out[:e['start']]+e['replacement']+out[e['end']:]
    return out.strip(),edits


class RepairSourceBlocks:
    def __call__(self,row):
        blocks=copy.deepcopy(row['dedup_blocks']);log=[];unresolved=[];original={b['block_id']:b for b in row['clean_blocks']}
        for b in blocks:
            before=b['text']
            # Inline citations are not evidence that prose is a navigation directory.
            old=original[b['block_id']]
            if (old['reason']=='link_directory_requires_review' and not row['filter_diagnostics']['document_reason']
                    and not any(x.casefold() in REFERENCES for x in b.get('section',[]))):
                links=old.get('links',[]);body=re.sub(r'\d+\(https?://[^\s)]+\)','',before)
                labels=sum(len(x.get('label','')) for x in links)
                if len(body)>=60 and len(re.findall(r'[。！？.!?]',body))>=2 and labels/max(1,len(body))<.5:
                    b.update(decision='keep',reason='prose_with_inline_links_restored')
                    log.append({'block_id':b['block_id'],'action':'restore','reason':b['reason'],'before':before,'after':before})
            # An explicit ad marker delimits the ad; preserve a trailing field label
            # instead of discarding the complete mixed block. No key/value pairing guessed.
            if b['reason']=='explicit_advertisement':
                m=re.search(r'\[广告\]\s*(\S.{0,14})$',before)
                if m and len(m[1].split())<=2 and not re.search(r'[。！？!?]',m[1]):
                    b.update(text=m[1],decision='keep',reason='trailing_field_fragment_preserved')
                    log.append({'block_id':b['block_id'],'action':'trim','reason':'explicit_ad_prefix_only','before':before,'after':m[1],'removed':before[:m.start(1)]})
                    unresolved.append({'block_id':b['block_id'],'reason':'field_label_value_relationship_requires_DOM','text':m[1]})
            if b['decision']!='keep':continue
            repaired,edits=repair_piece(b['text'])
            if edits:
                log.append({'block_id':b['block_id'],'action':'edit','reason':'source_markup_or_navigation_only','before':b['text'],'after':repaired,'edits':edits})
                b['text']=repaired
            # Already-flattened indented rows lack trustworthy field boundaries.
            if re.match(r' {2,}\S',b['raw_text']) and b['kind']=='paragraph':
                unresolved.append({'block_id':b['block_id'],'reason':'indented_flattened_fields_preserved_without_guessing','text':b['text']})
        text='\n\n'.join(b['text'] for b in blocks if b['decision']=='keep' and b['text'].strip())
        return {**row,'repaired_blocks':blocks,'final_text':text,'repairs':log,'unresolved_structure':unresolved}


from preparation.operaters.identity import material_disposition


class FilterDocumentBlocks:
    """clean_document_record row -> repaired clean_text/blocks, with raw offsets and audit.

    No language-specific metric ablations, fuzzy deletion, model calls or refetch.
    """
    def __call__(self, row):
        filtered = FilterSourceQuality()(row)
        repaired = RepairSourceBlocks()(filtered)
        blocks = repaired['repaired_blocks']
        texts, seen = [], {}
        offset = 0
        for block in blocks:
            block.pop('clean_start', None)
            block.pop('clean_end', None)
            if block['decision'] != 'keep' or not block['text'].strip():
                continue
            key = (tuple(block.get('section', [])), normalized(block['text']))
            if block['kind'] != 'heading' and key in seen:
                block.update(decision='exclude', reason='normalized_duplicate_same_section', duplicate_of=seen[key])
                continue
            seen[key] = block['block_id']
            if texts:
                offset += 2
            block['clean_start'] = offset
            offset += len(block['text'])
            block['clean_end'] = offset
            texts.append(block['text'])
        text = '\n\n'.join(texts)
        counts = {**row['clean_counts'], 'output_chars':len(text),
                  'excluded':sum(b['decision']=='exclude' for b in blocks),
                  'excluded_nonblank':sum(b['decision']=='exclude' and bool(b['raw_text'].strip()) for b in blocks),
                  'deferred':sum(b['decision']=='defer' for b in blocks)}
        status = row['clean_status'] if text else 'unavailable'
        audit = {'document_reason':filtered['filter_diagnostics']['document_reason'],
                 'transaction_page_signal':filtered['filter_diagnostics']['transaction_page_signal'],
                 'repairs':repaired['repairs'], 'unresolved_structure':repaired['unresolved_structure']}
        return {**row, 'clean_text':text, 'clean_blocks':blocks, 'clean_counts':counts,
                'clean_status':status, 'clean_version':row['clean_version']+'+block-filter/1',
                'clean_filter':audit,
                'knowledge_eligibility':material_disposition({'text':text, 'status':status, 'warnings':row['clean_warnings']})}
