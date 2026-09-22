"""Local source repairs with explicit edits; no generation or semantic filtering."""
import copy
import hashlib
import re
from curation.preparation.ops.cleaning_trials import REFERENCES

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


class ReadDOMFields:
    """Recover only explicit DOM pairs from separately frozen HTML, with XPath provenance."""
    def __call__(self,row):
        from pathlib import Path
        from lxml import html
        from trafilatura.utils import decode_file
        out={**row,'dom_fields':[]}
        if row.get('http_status')!=200 or 'html' not in (row.get('content_type') or ''):return out
        raw=Path(row['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('HTML snapshot changed')
        tree=html.fromstring(decode_file(raw));root=tree.getroottree();pairs=[];seen=set()
        def safe(node):
            for p in [node,*node.iterancestors()]:
                if p.tag in {'nav','header','footer','aside','script','style'}:return False
                if re.search(r'(?:^|[\s_-])(nav|navigation|menu|sidebar|footer|breadcrumb)(?:$|[\s_-])',str(p.get('class',''))+' '+str(p.get('id','')),re.I):return False
            return True
        def add(key,value,reason):
            if not safe(key) or not safe(value):return
            k=' '.join(key.text_content().split());v=' '.join(value.text_content().split())
            if not k or not v or len(k)>50 or len(v)>1000:return
            loc=(root.getpath(key),root.getpath(value))
            if loc in seen:return
            seen.add(loc);pairs.append({'field':k,'value':v,'key_xpath':loc[0],'value_xpath':loc[1],'rule':reason})
        for dt in tree.xpath('//dl/dt'):
            dd=dt.getnext()
            if dd is not None and dd.tag=='dd':add(dt,dd,'adjacent_dt_dd')
        for tr in tree.xpath('//table//tr'):
            cells=tr.xpath('./th|./td')
            if len(cells)==2 and cells[0].tag=='th' and not tr.xpath('.//table'):add(*cells,'table_header_value')
        # Some pages encode fields as adjacent label/value elements instead of dl.
        for el in tree.xpath('//*[@class]'):
            if not re.search(r'(?:^|[-_\s])(?:name|label|key)$',el.get('class','')):continue
            nxt=el.getnext()
            if nxt is not None and re.search(r'(?:^|[-_\s])value$',nxt.get('class','')):
                add(el,nxt,'explicit_label_value_classes')
        out['dom_fields']=pairs
        return out
