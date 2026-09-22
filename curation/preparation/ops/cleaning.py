"""Conservative source-preserving cleaning; no archived cleaners or model rewrites.

Offsets address frozen downloaded text (or the explicitly recorded wiki section
serialization). Rendered text maps to whole source blocks, not invented character
alignment. Unrecognized markup is retained and flagged.
"""
import re
from html.parser import HTMLParser
from curation.preparation.contracts import digest

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
        from curation.preparation.ops.source_markup import spans as markup_spans
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
            render=RenderHTML();render.feed(fragment);render.close()
            text=''.join(render.parts).strip();links=render.links;images=render.images
            if kind.startswith('h') and kind[1:].isdigit():kind='heading'
            elif kind in {'ul','ol'}:kind='list'
            elif kind=='figure':kind='figure'
            elif kind in {'header','aside'}:warnings.append('auxiliary_html_retained')
        else:
            markup=None
            if '{{' in s or '[[' in s or re.search(r'<(?:ref|math|sup|sub)[ >]',s):
                from curation.preparation.ops.source_markup import render
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
    from curation.preparation.ops.body_text import finish_body
    return finish_body(raw,result,classifier=classifier)


def clean_materials(bundle):
    result=[];seen={}
    for m in bundle['materials']:
        if m['kind']=='clean_docs':raise ValueError('Historical clean_docs is not an input')
        if m['kind'] not in {'legacy_docs','wiki_pages'}:result.append(m);continue
        raw=raw_text(m)
        clean=clean_document(raw,title=m['record'].get('title'),source_sections=m['record'].get('sections'))
        clean['source_locator']={'provenance':m['provenance'],
            'document':{k:v for k,v in m.get('document',{}).items() if k!='text'},
            'basis':'wiki_sections_serialized_title_text' if m['kind']=='wiki_pages' else 'downloaded_document_text'}
        if m['kind']=='wiki_pages':
            clean['source_locator']['sections']=[{'index':i,'title':s.get('title','')} for i,s in enumerate(m['record'].get('sections',[]))]
        if not raw:clean['status']='unavailable';clean['warnings'].append('missing_source_text')
        h=digest(clean['text'].encode())
        if clean['text'] and h in seen:clean['duplicate_of']=seen[h]
        elif clean['text']:seen[h]=len(result)
        result.append({**m,'cleaning':clean})
    return result
