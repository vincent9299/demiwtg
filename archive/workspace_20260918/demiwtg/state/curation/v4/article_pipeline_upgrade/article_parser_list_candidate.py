"""Plain article extraction/review: model chooses content, code owns provenance."""
import copy
import json
import re
from pathlib import Path
from .paragraph_pipeline import SourceCatalog

MARK = re.compile(r'【(资料|图)([1-9][0-9]*)】')
INTERNAL = re.compile(r'(?<![A-Za-z0-9])[IUMBPK][0-9a-f]{12,}(?![A-Za-z0-9])')
DONE = '【完成】'
EMPTY = '【无可保留知识】'


def identity_scope(concept, definitions, context=None):
    scope = definitions.get(concept, definitions.get('legacy:' + concept, concept))
    context = context or {}
    # Explicitly configured identity wins over potentially broad legacy aliases.
    if scope == concept and context.get('aliases'):
        scope += '；原始概念记录别名：' + '、'.join(context['aliases'])
    if context.get('background_titles'):
        scope += ('。身份范围限制：以下原文对象仅是相关背景，不是与目标等同的对象：'
                  + '、'.join(context['background_titles'])
                  + '。只提取它们帮助理解目标的部分，不能把这些背景对象的定义改名当成目标定义。'
                    '此范围标记不是事实证据，知识仍须来自原文或真实图片。')
    return scope


def source_text(p):
    sections = p.get('sections') or []
    if isinstance(sections, str): sections = [sections]
    parts = []
    if p.get('title'): parts.append('标题：' + str(p['title']))
    if sections: parts.append('章节：' + ' / '.join(sections))
    for key, label in [('context_before', '前文'), ('text', '原文'), ('context_after', '后文')]:
        value = p.get(key)
        if isinstance(value, list): value = '\n'.join(x if isinstance(x,str) else x['text'] for x in value)
        if value: parts.append(label + '：' + str(value))
    return '\n'.join(parts)


def render_input(concept, scope, sources, image_ids, drafts=None):
    parts = ['目标概念：' + concept, '身份范围：' + scope]
    if drafts is not None:
        parts += ['以下是待检查的提取稿，不是事实证据：', *drafts]
        if image_ids:
            parts.append('仅依据图片写出的旧描述已省略；其采用的真实图片仍全部附后，请直接看图决定是否采用及怎样说明。')
    parts.append('以下是原文资料：')
    for n, p in enumerate(sources, 1):
        parts.append(f'【资料{n}】\n{source_text(p)}')
    parts.append('实际图片按以下顺序附后：' + '、'.join(f'【图{n}】' for n in range(1, len(image_ids) + 1)))
    return '\n\n'.join(parts)


class PrepareArticleInput:
    def __init__(self, definitions=None): self.definitions = definitions or {}

    def __call__(self, row):
        joint = row['joint_prompt']
        sources = copy.deepcopy(joint['passages'])
        ids = list(joint['image_ids'])
        if len(ids) != len(set(ids)) or len(ids) != len(row['pixel_images']):
            raise ValueError('Duplicate image IDs or mismatched pixels')
        context = copy.deepcopy(joint.get('scope_context', {}))
        scope = identity_scope(joint['concept'], self.definitions, context)
        return {**row, 'concept': joint['concept'], 'identity_scope': scope, 'scope_context': context,
                'source_catalog': sources, 'article_image_ids': ids,
                'article_input': render_input(joint['concept'], scope, sources, ids)}


def parse_article(text, sources, image_ids, *, final=False):
    """Parse a deliberately small Markdown convention; never repair unknown IDs."""
    issues = []
    if not isinstance(text, str) or not text.strip(): return [], ['empty_output'], None
    text = text.strip()
    if final:
        # Ordinary Markdown subheadings carry no factual claim. Accept them as
        # sections instead of misreading an isolated heading as uncited prose.
        text = re.sub(r'(?m)^#{3,6}\s+', '## ', text)
        # A missing introductory heading carries no semantic information. Keep
        # all authored prose under a neutral title; evidence and completion
        # checks below still reject unsupported or unfinished content.
        if not text.startswith(('## ', EMPTY)):
            text = '## 概述\n\n' + text
        # A source number used in prose still refers to the supplied catalog.
        # Keep draft parsing unchanged so frozen extraction remains reusable.
        text = re.sub(r'(?<!【)资料\s*([1-9][0-9]*)', r'【资料\1】', text)
        # A colon-introduced Markdown list and its trailing source-only line
        # form one explicitly cited block. Do not spread that citation to other
        # independent paragraphs in the section.
        text = re.sub(r'([:：])\n[ \t]*\n(?=[ \t]*(?:[-*+]|[0-9]+[.)])\s)', r'\1\n', text)
        text = re.sub(r'\n[ \t]*\n((?:【资料[1-9][0-9]*】[ \t]*)+)(?=\n|$)', r'\1', text)

    if not text.endswith(DONE): issues.append('missing_completion_marker')
    text = text.removesuffix(DONE).strip()
    if INTERNAL.search(text): issues.append('internal_id_in_prose')
    if any(int(n)>len(image_ids) for n in re.findall(r'图\s*([1-9][0-9]*)', MARK.sub('', text))):
        issues.append('unmarked_image_number')
    if re.search(r'https?://|```', text): issues.append('unexpected_url_or_code_fence')
    if text.startswith(EMPTY):
        reason = text[len(EMPTY):].strip()
        if MARK.search(reason) or '## ' in reason or DONE in reason or EMPTY in reason:
            issues.append('content_after_empty_declaration')
        return [], issues + ([] if reason else ['missing_empty_reason']), reason
    # A section made entirely of image descriptions is already visual knowledge.
    # Treat each authored description as an image-supported paragraph; do not ask
    # the model to duplicate it as separate prose before placing the same image.
    sections = re.split(r'(?m)(?=^## )', text)
    for i, section in enumerate(sections):
        lines = section.splitlines()
        body = [line.strip() for line in lines[1:] if line.strip()]
        descriptions = [re.fullmatch(r'【图([1-9][0-9]*)】\s*(.+)', line) for line in body]
        if (lines and lines[0].startswith('## ') and body and all(descriptions)
                and all(len(MARK.findall(line)) == 1 for line in body)):
            sections[i] = lines[0] + '\n\n' + '\n\n'.join(
                m[2] + f'【图{m[1]}】' for m in descriptions) + '\n\n'
    text = ''.join(sections)
    topics = []; current = None; buffer = []; seen_images = set()

    def flush():
        nonlocal buffer
        if not buffer: return
        paragraph = '\n'.join(buffer).strip(); buffer = []
        if current is None: issues.append('text_before_topic'); return
        # Ordinary prose may say 图2 rather than 【图2】. Normalize only
        # declared input numbers, before cross-batch/final renumbering.
        inline_starts = set(); offset = 0
        def normalize_figure(match):
            nonlocal offset
            if int(match[1]) > len(image_ids): return match[0]
            value = f'【图{match[1]}】'
            inline_starts.add(match.start() + offset)
            offset += len(value) - len(match[0])
            return value
        paragraph=re.sub(r'(?<!【)图\s*([1-9][0-9]*)',normalize_figure,paragraph)
        refs = list(dict.fromkeys((kind, int(n)) for kind, n in MARK.findall(paragraph)))
        for kind, n in refs:
            if n > (len(sources) if kind == '资料' else len(image_ids)):
                issues.append('unknown_' + kind + '_' + str(n))
        def readable_marker(match):
            # Keep an inline figure reference; trailing marks are evidence only.
            # Its number is remapped across draft groups and again on publication.
            if match[1]=='图':
                following = MARK.sub('', paragraph[match.end():]).strip(' \t\n。.!！?？;；,，、:：)]）】')
                explicit = match.start() in inline_starts or re.search(r'(?:见|如|同|对照|参考)\s*$', paragraph[:match.start()])
                if following or explicit: return match[0]
            elif final:
                before = MARK.sub('', paragraph[:match.start()]).rstrip()
                after = paragraph[match.end():].lstrip()
                # Preserve a source used as a sentence constituent, while
                # removing ordinary trailing evidence marks. Adjacent marks
                # share one readable noun; all original references stay below.
                explicit = re.search(r'(?:根据|据|参见|见|参照|参考|按照|按|在|由|对照|引自)\s*$', before)
                leading = not before
                predicate = re.match(r'(?:中|所|提出|指出|认为|列出|记载|显示|说明|描述|给出|解释|采用|将|把|的)', after)
                if not after.startswith('【') and (explicit or (after and (leading or predicate)
                        and after[0] not in '。.!！?？;；,，、:：)]）】')):
                    return '所引资料'
            return ''
        clean = MARK.sub(readable_marker, paragraph).strip()
        if not clean: issues.append('empty_paragraph')
        if re.search(r'【[^】]*】', MARK.sub('',clean)): issues.append('unknown_marker')
        current['paragraphs'].append({'text': clean, 'refs': refs})

    for line in text.splitlines():
        line = line.strip()
        if line.startswith('## '):
            flush()
            current = {'title': line[3:].strip(), 'paragraphs': [], 'images': []}
            if not current['title'] or MARK.search(current['title']) or re.search(r'图\s*[0-9]+',current['title']): issues.append('invalid_title')
            topics.append(current)
        elif final and re.fullmatch(r'【图[1-9][0-9]*】', line):
            # Selecting a real image requires no generated caption. The marker
            # becomes a program-numbered figure label at publication.
            flush()
            n = int(MARK.fullmatch(line)[2])
            if current is None: issues.append('image_without_topic'); continue
            if n > len(image_ids): issues.append('unknown_image_' + str(n)); continue
            if n in seen_images: issues.append('duplicate_image'); continue
            seen_images.add(n)
            current['paragraphs'].append({'text': line, 'refs': [('图', n)]})
            current['images'].append({'number': n, 'caption': '', 'limitations': '',
                                      'paragraph_index': len(current['paragraphs']) - 1})
        elif re.match(r'^【图[1-9][0-9]*】', line) and len(MARK.findall(line)) == 1:
            flush()
            match = MARK.match(line); n = int(match[2]); caption = line[match.end():].strip()
            if current is None or not current['paragraphs']: issues.append('image_without_body'); continue
            if n > len(image_ids): issues.append('unknown_image_' + str(n)); continue
            if n in seen_images: issues.append('duplicate_image'); continue
            if MARK.search(caption) or '【' in caption: issues.append('marker_in_caption')
            caption=re.sub(r'图\s*([1-9][0-9]*)',lambda m:f'【图{m[1]}】' if int(m[1])<=len(image_ids) else m[0],caption)
            seen_images.add(n)
            current['images'].append({'number': n, 'caption': caption, 'limitations': '', 'paragraph_index': len(current['paragraphs']) - 1})
        elif line.startswith('局限：'):
            if current is None or not current['images']: issues.append('limitation_without_image')
            else: current['images'][-1]['limitations'] = line[len('局限：'):].strip()
        elif not line: flush()
        else: buffer.append(line)
    flush()
    for t in topics:
        if not t['paragraphs']: issues.append('topic_without_body')
        # A standalone image placed after a paragraph is also an explicit
        # model-declared association; it need not repeat the same image number.
        for im in t['images']:
            refs=t['paragraphs'][im['paragraph_index']]['refs']
            if ('图',im['number']) not in refs:refs.append(('图',im['number']))
            for kind,n in MARK.findall(im['caption']):
                if (kind,int(n)) not in refs:refs.append((kind,int(n)))
        for pi,p in enumerate(t['paragraphs']):
            if not p['refs']:issues.append('paragraph_without_evidence')
            for kind,n in p['refs']:
                if kind == '图' and n not in seen_images and n <= len(image_ids):
                    # A cited image is already a model selection. Restore its pixels
                    # at the cited paragraph, without inventing or recycling captions.
                    t['images'].append({'number':n,'caption':'','limitations':'','paragraph_index':pi})
                    seen_images.add(n)
    if not topics: issues.append('no_topics')
    return topics, sorted(set(issues)), None


class ApplyArticle:
    def __init__(self, final=False, image_selection_only=False):
        if image_selection_only and not final:
            raise ValueError('Image selection only applies to final review')
        self.final = final
        self.image_selection_only = image_selection_only
    def __call__(self, row):
        out = dict(row)
        if row.get('preflight_error')=='no_extracted_knowledge':
            return {**out,'article_topics':[],'article_text':None,'validation_issues':[],
                    'empty_reason':'联合提取没有可保留知识','article_status':'no_supported_knowledge','article_call':None}
        topics, issues, empty = parse_article(row.get('prompt_result'), row.get('source_catalog', []), row.get('article_image_ids', []), final=self.final)
        if self.image_selection_only:
            for topic in topics:
                if topic['images'] and topic['title'] != '配图':
                    issues.append('image_outside_figure_section')
                for paragraph in topic['paragraphs']:
                    if any(k == '图' for k, _ in paragraph['refs']):
                        marker = MARK.fullmatch(paragraph['text'])
                        if not marker or marker[1] != '图' or len(paragraph['refs']) != 1:
                            issues.append('generated_image_prose_not_allowed')
                if any(im['caption'] or im['limitations'] for im in topic['images']):
                    issues.append('generated_image_prose_not_allowed')
        if row.get('prompt_error'): issues.append('model_call_failed')
        if row.get('preflight_error'): issues.append(row['preflight_error'])
        review_issues=[]
        if not self.final:
            review_issues=[i for i in issues if i in {'paragraph_without_evidence','duplicate_image'}]
            issues=[i for i in issues if i not in review_issues]
            if topics and not any(p['refs'] for t in topics for p in t['paragraphs']):issues.append('no_cited_evidence')
        out.update(article_topics=topics, article_text=row.get('prompt_result'), validation_issues=sorted(set(issues)),
                   review_required_issues=sorted(set(review_issues)), empty_reason=empty,
                   article_status='failed' if issues else 'no_supported_knowledge' if empty else 'reviewed' if self.final else 'draft')
        out['article_call'] = row.get('prompt_call')
        return out


def topic_text(topics, source_map=None, image_map=None):
    source_map = source_map or {}; image_map = image_map or {}
    lines = []
    for topic in topics:
        lines.append('## ' + topic['title'])
        for i, p in enumerate(topic['paragraphs']):
            refs = ''.join(f'【{kind}{(source_map if kind == "资料" else image_map).get(n, n)}】' for kind, n in p['refs'])
            body=MARK.sub(lambda m:f'【{m[1]}{(source_map if m[1]=="资料" else image_map).get(int(m[2]),int(m[2]))}】',p['text'])
            lines += [body + refs, '']
            for im in topic['images']:
                if im['paragraph_index'] == i:
                    caption=MARK.sub(lambda m:f'【图{image_map.get(int(m[2]),int(m[2]))}】',im['caption'])
                    lines += [f'【图{image_map.get(im["number"], im["number"])}】' + caption]
                    if im['limitations']: lines.append('局限：' + im['limitations'])
        lines.append('')
    return '\n'.join(lines)


class PrepareFinalReview:
    def __init__(self, definitions=None, counter=None, max_input_tokens=65536):
        self.definitions=definitions or {}; self.counter=counter; self.limit=max_input_tokens

    def __call__(self, group):
        rows = sorted(group.get('drafts', []), key=lambda r: r['batch_id'])
        sources=[]; image_ids=[]; pixels=[]; source_numbers={}; image_numbers={}; drafts=[]; parents=[]; failure=[]
        for row in rows:
            parents.append(row['batch_id'])
            if row['article_status'] == 'failed': failure.append(row['batch_id']); continue
            sm={}; im={}
            wanted_s={n for t in row['article_topics'] for p in t['paragraphs'] for k,n in p['refs'] if k=='资料'}
            wanted_i={n for t in row['article_topics'] for p in t['paragraphs'] for k,n in p['refs'] if k=='图'}
            wanted_i.update(x['number'] for t in row['article_topics'] for x in t['images'])
            for n in sorted(wanted_s):
                p=row['source_catalog'][n-1]; key=p['source_id']
                if key in source_numbers and sources[source_numbers[key]-1] != p: raise ValueError('Conflicting source ID')
                if key not in source_numbers: sources.append(p);source_numbers[key]=len(sources)
                sm[n]=source_numbers[key]
            for n in sorted(wanted_i):
                iid=row['article_image_ids'][n-1]; pixel=row['pixel_images'][n-1]
                if iid in image_numbers and pixels[image_numbers[iid]-1]!=pixel: raise ValueError('Conflicting image ID')
                if iid not in image_numbers: image_ids.append(iid);pixels.append(pixel);image_numbers[iid]=len(image_ids)
                im[n]=image_numbers[iid]
            if row['article_topics']:
                # Image-only prose is a generated visual description too. Do
                # not reintroduce captions merely because extraction put them
                # in paragraphs. Keep every adopted pixel and its mapping.
                text_topics=[]
                for t in row['article_topics']:
                    paragraphs=[p for p in t['paragraphs']
                                if not p['refs'] or any(k=='资料' for k,_ in p['refs'])]
                    if paragraphs: text_topics.append({**t,'paragraphs':paragraphs,'images':[]})
                if text_topics:
                    draft=topic_text(text_topics,sm,im)
                    if row.get('review_required_issues'):
                        draft='此组有待检查的引用或重复问题。仅用已提供的引用资料核对；无依据内容删除。\n'+draft
                    drafts.append(draft)
        contexts=[r.get('scope_context', {}) for r in rows if r['article_status'] != 'failed']
        cited_titles={p.get('title') for p in sources}
        context={'aliases':sorted({a for c in contexts for a in c.get('aliases', [])}),
                 'background_titles':sorted({t for c in contexts for t in c.get('background_titles', []) if t in cited_titles})}
        scope=identity_scope(group['concept'],self.definitions,context)
        out={'concept':group['concept'],'batch_id':group['concept']+':final_review','source_catalog':sources,'article_image_ids':image_ids,
             'pixel_images':pixels,'identity_scope':scope,'scope_context':context,'parent_batches':parents,'failed_batches':failure,
             'draft_review_issues':{r['batch_id']:r['review_required_issues'] for r in rows if r.get('review_required_issues')},
             'article_input':render_input(group['concept'],scope,sources,image_ids,drafts), 'preflight_error':None}
        if failure: out['preflight_error']='failed_extraction_batches'
        elif not drafts and not image_ids: out['preflight_error']='no_extracted_knowledge'
        if self.counter is not None:
            out['input_token_budget']=self.counter(out)
            out['input_token_limit']=self.limit
            if out['input_token_budget']>self.limit: out['preflight_error']='review_capacity_exceeded'
        return out


def decode_source_url(raw):
    if not isinstance(raw,str):return ''
    if raw.startswith(('https://','http://')):return raw
    if raw.startswith('ippr'):
        value=raw.replace('_z2C$q', ':').replace('_z&e3B', '.').replace('AzdH3F', '/')
        value=value.translate(str.maketrans(dict(zip('0123456789abcdefghijklmnopqrstuvw', '7dgjmoru140852vsnkheb963wtqplifca'))))
        if value.startswith(('https://','http://')): return value
    return ''


def make_reference(kind, entry, iid=None):
    if kind=='text':
        sid=entry['source_id']; record=entry.get('reference', {})
        title=record.get('title') or entry.get('title') or '原文资料'
        raw=record.get('url') or entry.get('url') or entry.get('source_family') or ''
        local=record.get('local_path')
    else:
        sid=iid; record=entry.get('record', entry)
        title=record.get('title') or '图片来源'
        candidates=[record.get(k) for k in ['landing_url','content_url','url']]
        raw=next((u for u in candidates if decode_source_url(u)), next((u for u in candidates if u), ''))
        local=entry.get('bytes', {}).get('path')
    url=decode_source_url(raw)
    out={'title':title,'url':url,'source_ids':[sid],'kinds':[kind]}
    if raw and raw!=url:out['original_url']=raw
    if not url:out['source_note']='原始网页地址缺失'
    if local:out['local_path']=str(Path(local).resolve())
    return out


class PublishArticle:
    def __call__(self,row):
        reviewed=row.get('review') or {}; materials=row.get('materials',[])
        images={m['image_id']:m for r in materials for m in r.get('material_pack',{}).get('images',[])}
        sources={}
        for r in materials:
            catalog=SourceCatalog()(r)
            for p in r['material_pack']['passages']:
                sources[p['source_id']]={**p,'reference':catalog['sources'].get(p['source_id'],{})}
        articles=[]; image_numbers={}; used_images=[]
        valid=reviewed.get('article_status')=='reviewed'
        if valid:
            for t in reviewed['article_topics']:
                for im in t['images']:
                    iid=reviewed['article_image_ids'][im['number']-1]
                    image_numbers.setdefault(iid,len(image_numbers)+1)
            for t in reviewed['article_topics']:
                refs={}; paragraphs=[]; placements=[]
                for pi,p in enumerate(t['paragraphs']):
                    text=MARK.sub(lambda m:'图'+str(image_numbers[reviewed['article_image_ids'][int(m[2])-1]]) if m[1]=='图' else '',p['text'])
                    for kind,n in p['refs']:
                        if kind=='资料':
                            original=reviewed['source_catalog'][n-1];sid=original['source_id']
                            ref=make_reference('text',sources.get(sid,original));key=('text',sid)
                        else:
                            iid=reviewed['article_image_ids'][n-1]
                            ref=make_reference('image',images.get(iid,{}),iid);key=('image',iid)
                        refs.setdefault(key,{**ref,'paragraph_indices':[]})['paragraph_indices'].append(pi)
                    paragraphs.append(text)
                for im in t['images']:
                    iid=reviewed['article_image_ids'][im['number']-1]
                    if iid not in images:raise ValueError('Final image outside selected material pool')
                    number=image_numbers.setdefault(iid,len(image_numbers)+1)
                    caption=MARK.sub(lambda m:'图'+str(image_numbers[reviewed['article_image_ids'][int(m[2])-1]]),im['caption'])
                    placements.append({'image_id':iid,'figure_number':number,'caption':caption,
                                       'limitations':im['limitations'],'region':'整体','paragraph_index':im['paragraph_index']})
                    used_images.append(iid)
                    ref=refs.setdefault(('image',iid),{**make_reference('image',images[iid],iid),'paragraph_indices':[]})
                    if im['paragraph_index'] not in ref['paragraph_indices']:ref['paragraph_indices'].append(im['paragraph_index'])
                articles.append({'title':t['title'],'content':{'paragraphs':paragraphs,'images':placements},'references':list(refs.values())})
        status=reviewed.get('article_status','insufficient_materials')
        if reviewed.get('preflight_error')=='no_extracted_knowledge':status='insufficient_materials'
        reason=reviewed.get('empty_reason')
        if reviewed.get('preflight_error'):
            reason={'no_extracted_knowledge':'联合提取没有可保留知识','failed_extraction_batches':'部分提取组失败，尚不能发布完整概念知识',
                    'review_capacity_exceeded':'最终 review 输入超出容量，原材料与提取稿均已保留'}.get(reviewed['preflight_error'],reviewed['preflight_error'])
        if not reason and not reviewed:
            reason='；'.join(dict.fromkeys(r['blocked'].get('detail') or r['blocked'].get('reason','上游未通过') for r in materials if r.get('blocked')))
        from .knowledge_stages import material_id
        unexamined_docs={material_id(m) for r in materials for m in r.get('cleaned_materials',[])
                         if 'cleaning' in m and material_id(m) in r.get('identity_unexamined',[])}
        return {'concept':row['concept'],'case_id':row['concept'],'knowledge':articles,
                'status':status,'status_reason':reason or None,
                'identity':[r.get('identity') for r in materials],
                'documents':[m for r in materials for m in r.get('cleaned_materials',[]) if 'cleaning' in m],
                'images':[m for r in materials for m in r.get('cleaned_materials',[]) if m.get('kind') in {'legacy_images','qid_images'}],
                'audit':{'review_call':reviewed.get('article_call'),'validation_issues':reviewed.get('validation_issues',[]),
                         'preflight_error':reviewed.get('preflight_error'),
                         'draft_review_issues':reviewed.get('draft_review_issues',{}),
                         'parent_batches':reviewed.get('parent_batches',[]),'failed_batches':reviewed.get('failed_batches',[]),
                         'input_token_budget':reviewed.get('input_token_budget'),'selected_image_ids':used_images,
                         'unexamined_identity_document_ids':sorted(unexamined_docs),
                         'material_batch_count':len(materials),'review_is_independent':False}}


class ArticleTokenBudget:
    """Count the actual plain-text template plus Qwen visual tokens before calls."""
    def __init__(self, model_path, config, prompt_name='final_review'):
        from transformers import AutoTokenizer
        from .prompt_config import knowledge_prompt_pack
        from demiflow.operator_llm.client import _response_contract_instruction
        from demiflow.operator_llm import compile_template
        from types import SimpleNamespace
        import yaml
        self.tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True)
        self.pre=json.loads((Path(model_path)/'preprocessor_config.json').read_text())
        if self.pre.get('processor_class')!='Qwen3VLProcessor':raise ValueError('Unsupported image token counter')
        _,text=knowledge_prompt_pack(config)
        spec=yaml.safe_load(text)['prompts'][prompt_name]
        self.template=spec['template']
        self.compiled_template=compile_template(self.template)
        self.system=_response_contract_instruction(SimpleNamespace(response_format='text',response_schema=spec['response_schema']))
        self.cache={};self.definitions=config.get('image_identity_definitions',{})
        self.thinking=config.get('enable_thinking',False);self.effort=config.get('reasoning_effort','low')

    def image_tokens(self,pixel):
        import base64,io,hashlib
        from PIL import Image
        from transformers.models.qwen2_vl.image_processing_qwen2_vl import smart_resize
        key=hashlib.sha256(pixel.encode()).hexdigest()
        if key not in self.cache:
            with Image.open(io.BytesIO(base64.b64decode(pixel.split(',',1)[1]))) as im:w,h=im.size
            factor=self.pre['patch_size']*self.pre['merge_size']
            rh,rw=smart_resize(h,w,factor=factor,min_pixels=self.pre['size']['shortest_edge'],max_pixels=self.pre['size']['longest_edge'])
            self.cache[key]=rh*rw//factor**2
        return self.cache[key]

    def __call__(self,row):
        if 'article_input' in row:
            payload=row['article_input'];pixels=row['pixel_images']
        else:
            from .multimodal import pixels as load_pixels
            payload=render_input(row['concept'],identity_scope(row['concept'],self.definitions,row.get('scope_context')),row['passages'],[m['image_id'] for m in row['images']])
            pixels,_=load_pixels(row['images'])
        # Count exactly the ordered parts used by the request renderer, including
        # each adjacent image label. Do not approximate the multimodal template
        # through string replacement, which misses new renderer conventions.
        from demiflow.operator_llm import render_template, TextPart
        parts=render_template(self.compiled_template,{'payload':payload,'images':pixels})
        user=''.join(part.text if isinstance(part,TextPart) else
                     '<|vision_start|>'+'<|image_pad|>'*self.image_tokens(part.image.uri)+'<|vision_end|>'
                     for part in parts)
        encoded=self.tokenizer.apply_chat_template([{'role':'system','content':self.system},{'role':'user','content':user}],tokenize=True,add_generation_prompt=True,enable_thinking=self.thinking,reasoning_effort=self.effort)
        ids=encoded['input_ids'] if hasattr(encoded,'keys') else encoded
        return len(ids)+256


class GatherConceptMaterials:
    """Reduce material batches to one concept without dropping distinct records."""
    def __call__(self,acc,row):
        if acc is None:return {'concept':row['identity']['target_label'],'materials':[row]}
        acc['materials'].append(row);return acc


def legacy_draft(row, definitions=None):
    """Explicit trial adapter for frozen old joint extraction, never a live fallback."""
    prepared=PrepareArticleInput(definitions)(row)
    sm={p['source_id']:i for i,p in enumerate(prepared['source_catalog'],1)}
    im={iid:i for i,iid in enumerate(prepared['article_image_ids'],1)}
    topics=[]
    for t in row.get('topics',[]):
        topic={'title':t['title'],'paragraphs':[],'images':[]};blocks={}
        for b in t['blocks']:
            if b.get('type')!='text' or b.get('status')!='candidate':continue
            refs=[('资料',sm[c['source_id']]) for c in b.get('citations',[]) if c['source_id'] in sm]
            refs.extend(('图',im[x['image_id']]) for x in b.get('image_refs',[]) if x['image_id'] in im)
            if not refs:continue
            blocks[b['block_id']]=len(topic['paragraphs'])
            text=b['text']
            for iid,n in im.items():text=text.replace(iid,f'【图{n}】')
            topic['paragraphs'].append({'text':text,'refs':list(dict.fromkeys(refs))})
        if not topic['paragraphs']:continue
        for b in t['blocks']:
            if b.get('type')!='image' or b.get('status')!='candidate' or b['image_id'] not in im:continue
            related=[blocks[x] for x in b.get('related_block_ids',[]) if x in blocks]
            topic['images'].append({'number':im[b['image_id']],'caption':b['caption'],'limitations':b.get('limitations',''),'paragraph_index':related[0] if related else len(topic['paragraphs'])-1})
        # Old image_refs did not require a placement. Supply a neutral candidate label,
        # not an upstream image model caption, to keep that evidence visible for review.
        represented={x['number'] for x in topic['images']}
        for pi,p in enumerate(topic['paragraphs']):
            for kind,n in p['refs']:
                if kind=='图' and n not in represented:
                    topic['images'].append({'number':n,'caption':'待核对原始图片','limitations':'','paragraph_index':pi});represented.add(n)
        topics.append(topic)
    prepared.update(article_topics=topics,article_text=topic_text(topics),validation_issues=[],article_status='draft',empty_reason=None,
                    legacy_adapter='Frozen old extraction candidates; not previously reviewed final knowledge')
    return prepared


class PrepareSelectionScope:
    def __init__(self,definitions=None):self.definitions=definitions or {}
    def __call__(self,row):
        prompt=row['block_prompt']
        return {**row,'block_prompt':{**prompt,'identity_scope':identity_scope(prompt['concept'],self.definitions)}}


class EnsureConceptLabel:
    """Keep the requested legacy concept key across identity/batching outcomes."""
    def __call__(self,row):
        identity=row.get('identity',{})
        request=row['bundle']['request']
        label=request['value'] if request['kind']=='legacy' else identity.get('target_label') or request['value']
        labels={'model_target_label':identity['target_label']} if identity.get('target_label') and identity['target_label']!=label else {}
        return {**row,'identity':{**identity,**labels,'target_label':label}}
