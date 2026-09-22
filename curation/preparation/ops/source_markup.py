"""Source-preserving, deliberately bounded MediaWiki interpretation.

Uses mwparserfromhell's syntax tree, never blanket strip_code(). Unknown
semantic templates defer their enclosing block, not the entire document.
"""
import re
import mwparserfromhell as mw
from curation.preparation.contracts import digest

PARSER_VERSION=mw.__version__
LAYOUT={'other uses','other uses of','about','redirect','redirect-distinguish','distinguish','hatnote',
        'good article','featured article','short description','cs1 config','reflist','notelist',
        'commons category','commonscat','sister bar','authority control','portal bar','clear','toc limit',
        'use mdy dates','use dmy dates','use american english','use british english','pp-semi-indef',
        'cosmology','cosmology topics','earth\'s location','fundamental interactions','nature nav',
        'defaultsort','spoken wikipedia','main','see also','further','cat main'}


def spans(raw):
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
