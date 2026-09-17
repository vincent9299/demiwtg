"""Experimental, source-preserving quality filters. No concept names or models.

Gopher-style metrics use jieba for Chinese and regex words for Latin scripts;
these are declared adaptations, not a reproduction of FineWeb2's calibrated recipe.
"""
import copy
import re
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


def units(s):
    if len(re.findall(r'[\u4e00-\u9fff]',s))>len(s)*.15:
        import jieba
        return [w for w in jieba.lcut(s) if any(c.isalnum() for c in w)]
    return re.findall(r"[\w]+(?:['’-][\w]+)?",s.lower())


def repetition(text):
    out={}
    for name,parts in [('line',[x for x in text.splitlines() if x.strip()]),('paragraph',[x for x in re.split(r'\n\s*\n',text) if x.strip()])]:
        seen=set();dup=[]
        for x in parts:
            if x in seen:dup.append(x)
            seen.add(x)
        out[name+'_duplicate_fraction']=len(dup)/max(1,len(parts))
        out[name+'_duplicate_char_fraction']=sum(map(len,dup))/max(1,len(text))
    ws=units(text);total=max(1,sum(map(len,ws)))
    for n in range(2,11):
        grams=[tuple(ws[i:i+n]) for i in range(len(ws)-n+1)];counts=Counter(grams)
        if n<=4:
            top=counts.most_common(1)
            frac=(sum(map(len,top[0][0]))*top[0][1]/total) if top else 0
        else:
            seen=set();positions=set()
            for i,g in enumerate(grams):
                if g in seen:positions.update(range(i,i+n))
                seen.add(g)
            frac=sum(len(ws[i]) for i in positions)/total
        out[f'ngram_{n}']=frac
    return out


def gopher_failures(text, english_defaults=False):
    ws=units(text);lines=[x for x in text.splitlines() if x.strip()];n=max(1,len(ws));reasons=[]
    if english_defaults:
        if not 50<=len(ws)<=100000:reasons.append('word_count_outside_50_100000')
        if not 3<=sum(map(len,ws))/n<=10:reasons.append('mean_word_length_outside_3_10')
        if len(STOP & set(ws))<2:reasons.append('fewer_than_two_english_stopwords')
    if text.count('#')/n>.1:reasons.append('hash_symbol_ratio')
    if (text.count('...')+text.count('…'))/n>.1:reasons.append('ellipsis_ratio')
    if sum(x.rstrip().endswith(('...','…')) for x in lines)/max(1,len(lines))>.3:reasons.append('truncated_line_ratio')
    if sum(x.lstrip().startswith(('•','-')) for x in lines)/max(1,len(lines))>.9:reasons.append('bullet_line_ratio')
    if sum(any(c.isalpha() for c in w) for w in ws)/n<.8:reasons.append('low_alphabetic_word_fraction')
    m=repetition(text)
    for k,v in m.items():
        threshold=.2 if k.endswith('char_fraction') else .3
        if k.startswith('ngram_'):threshold={2:.2,3:.18,4:.16,5:.15,6:.14,7:.13,8:.12,9:.11,10:.1}[int(k.split('_')[-1])]
        if v>threshold:reasons.append(k)
    return reasons,m


class QualityBranches:
    """Compare filters independently on exactly the same baseline cleaned document."""
    def __init__(self, *, compare_metrics=True):
        self.compare_metrics = compare_metrics

    def __call__(self,row):
        text=row['clean_text'];raw=row['raw_text'];blocks=copy.deepcopy(row['clean_blocks'])
        literal,metrics=gopher_failures(text,True) if self.compare_metrics else ([], {})
        adapted,_=gopher_failures(text) if self.compare_metrics else ([], {})
        branches={'baseline':text,'gopher_english_thresholds': '' if literal else text,
                  'gopher_multilingual_signals': '' if adapted else text}
        # Counterfactual C4-style line punctuation rule; not full C4 implementation.
        branches['c4_terminal_punctuation']='\n'.join(x for x in text.splitlines() if re.search(r'[.!?。！？]["”’\']?$',x.strip()))
        # A shared '/item/' route is also used by encyclopedias; it is not a product signal.
        retail=len(COMMERCE.findall(row.get('title','')))>=2 or bool(re.search(r'/products?/|/offer/|/selloffer/',row.get('url',''),re.I))
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
        branches['block_quality']=rule_text
        dedup=copy.deepcopy(blocks)
        for b in dedup:
            if b['decision']!='keep' or b['kind']=='heading':continue
            key=(tuple(b.get('section',[])),normalized(b['text']))
            if key in seen:b.update(decision='exclude',reason='normalized_duplicate_same_section',duplicate_of=seen[key])
            else:seen[key]=b['block_id']
        branches['paragraph_dedup']='\n\n'.join(b['text'] for b in dedup if b['decision']=='keep')
        return {**row,'variants':branches,'quality_blocks':blocks,'dedup_blocks':dedup,
                'filter_diagnostics':{'english_threshold_failures':literal,'multilingual_signal_failures':adapted,'repetition':metrics,'transaction_page_signal':retail,'document_reason':document_reason}}


class CompareDuplicates:
    """MinHash LSH candidate recall + exact Jaccard verification; no semantic model."""
    def __call__(self,batch):
        from datasketch import MinHash,MinHashLSH
        rows=batch['documents'];lsh=MinHashLSH(num_perm=112,params=(14,8));signatures={};shingles={};pairs=[]
        for r in rows:
            text=r['variants']['paragraph_dedup']
            # Citation URL wrappers are markup, not lexical evidence; normalize only the dedup view.
            match_text=re.sub(r'\d+\(https?://[^\s)]+\)', '', text)
            ws=units(match_text);ss={' '.join(ws[i:i+5]) for i in range(len(ws)-4)}
            if not ss:continue
            m=MinHash(num_perm=112,seed=42)
            for s in sorted(ss):m.update(s.encode())
            signatures[r['doc_id']]=m;shingles[r['doc_id']]=ss;lsh.insert(r['doc_id'],m)
        ids=list(signatures)
        for i,a in enumerate(ids):
            for b in ids[i+1:]:
                sa,sb=shingles[a],shingles[b];j=len(sa&sb)/len(sa|sb);contain=len(sa&sb)/min(len(sa),len(sb));hit=b in lsh.query(signatures[a])
                if hit or j>=.5 or contain>=.8:
                    pairs.append({'left':a,'right':b,'lsh_candidate':hit,'jaccard':j,'containment':contain,'near_duplicate':j>=.75,'action':'review_unique_passages_before_collapsing'})
        # Conservative automatic removal only for exact whole normalized body duplicates.
        # Paragraph duplication is an index of all source occurrences, not random deletion.
        seen={};paragraphs={}
        for r in rows:
            text=r['variants']['paragraph_dedup'];key=normalized(text)
            r['variants']['document_dedup']=text
            if key:
                if key in seen:r['variants']['document_dedup']='';r['duplicate_document_of']=seen[key]
                else:seen[key]=r['doc_id']
            for b in r['dedup_blocks']:
                if b['decision']=='keep' and b['kind']!='heading' and len(b['text'])>=25:
                    paragraphs.setdefault(normalized(b['text']),[]).append({'doc_id':r['doc_id'],'block_id':b['block_id'],'section':b.get('section',[])})
        # Separate aggressive branch to measure what a document-level .75 gate loses.
        by_id={r['doc_id']:r for r in rows}
        for r in rows:r['variants']['minhash_drop_document']=r['variants']['paragraph_dedup']
        for pair in pairs:
            if pair['lsh_candidate'] and pair['near_duplicate']:
                candidates=[by_id[pair['left']],by_id[pair['right']]]
                drop=min(candidates,key=lambda r:len(r['variants']['paragraph_dedup']))
                drop['variants']['minhash_drop_document']=''
                pair['aggressive_branch_dropped']=drop['doc_id']
        paragraph_candidates=[]
        indexed=[]
        for r in rows:
            for b in r['dedup_blocks']:
                if b['decision']!='keep' or b['kind']=='heading' or len(b['text'])<80:continue
                t=re.sub(r'\d+\(https?://[^\s)]+\)', '', b['text'])
                ws=units(t);ss={' '.join(ws[i:i+3]) for i in range(len(ws)-2)}
                if ss:indexed.append((r['doc_id'],b,ss))
        for i,(doc,b,ss) in enumerate(indexed):
            for other,c,tt in indexed[i+1:]:
                if doc==other:continue
                j=len(ss&tt)/len(ss|tt);coverage=len(ss&tt)/min(len(ss),len(tt))
                if j>=.8 or coverage>=.9:
                    paragraph_candidates.append({'left_document':doc,'left_block':b['block_id'],'right_document':other,'right_block':c['block_id'],'jaccard':j,'containment':coverage,'left_text':b['text'],'right_text':c['text'],'action':'retain_unique_detail_and_all_sources; no automatic fuzzy deletion'})
        # Shared paragraph storage: preserve each document occurrence and heading context.
        # Only URL citation markup and whitespace are ignored. Fuzzy-only matches stay separate.
        shared={}
        for r in rows:
            for b in r['dedup_blocks']:
                if b['decision']!='keep' or b['kind']=='heading' or not b['text'].strip():continue
                display_text=re.sub(r'\d+\(https?://[^\s)]+\)', '', b['text'])
                key=re.sub(r'\s+', '', display_text)
                item=shared.setdefault(key,{'text':display_text,'sources':[]})
                item['sources'].append({'doc_id':r['doc_id'],'block_id':b['block_id'],'section':b.get('section',[]),'raw_start':b['raw_start'],'raw_end':b['raw_end'],'url':r['url']})
        return {'documents':rows,'shared_passages':list(shared.values()),'paragraph_near_pairs':paragraph_candidates,'near_duplicate_pairs':pairs,'repeated_paragraphs':[{'text':t,'occurrences':v} for t,v in paragraphs.items() if len(v)>1],
                'method':{'num_perm':112,'bands':14,'rows':8,'ngram':5,'seed':42,'jaccard_review_threshold':.75,'short_texts':'not indexed if fewer than 5 tokens','scope':'Experimental tokenization; no claim of full FineWeb reproduction'}}
