"""Article requests, response parsing, final review and source-bound results."""
from demiflow.execution.artifacts import digest


class SourceCatalog:
    def __call__(self,row):
        from preparation.operaters.identity import material_id
        records={material_id(m):m['record'] for m in row['cleaned_materials']}
        sources={}
        for p in row['material_pack']['passages']:
            r=records.get(p['material_id'],{})
            sources[p['source_id']]={'title':r.get('title') or r.get('url') or p['source_id'],'url':r.get('url') or r.get('source_url') or ''}
        images={}
        for m in row['material_pack']['images']:
            r=m['record'];images[m['image_id']]={'title':r.get('title') or '图片来源','url':r.get('landing_url') or r.get('content_url') or r.get('url') or ''}
        return {'concept':row['identity']['target_label'],'sources':sources,'image_sources':images}


import copy
import json
import re
from pathlib import Path

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


def render_input(concept, scope, sources, image_ids, drafts=None, *, draft_outline_only=False):
    parts = ['目标概念：' + concept, '身份范围：' + scope]
    parts.append('以下是原文资料：')
    for n, p in enumerate(sources, 1):
        parts.append(f'【资料{n}】\n{source_text(p)}')
    if drafts is not None:
        label = ('以下是联合提取的主题清单，仅提示覆盖范围；主题名称和关联不是事实证据。'
                 '完整提取稿保存在checkpoint中，本次不提供其生成断言，请直接对照原文审查并写最终知识：'
                 if draft_outline_only else '以下是待检查的提取稿，仅用于检查有用主题是否遗漏；其中每个断言均待核对，不能直接当成最终文章或事实证据：')
        parts += [label, *drafts]
        if image_ids:
            parts.append('仅依据图片写出的旧描述已省略；其采用的真实图片仍全部附后，请直接看图决定是否采用。')
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
        # A comma-separated source list is unambiguous. Expand its explicitly
        # named numbers, then validate every number through the usual path.
        # Do not interpret ranges, missing numbers, or image selections here.
        text = re.sub(r'【资料\s*([1-9][0-9]*(?:\s*[,，、]\s*[1-9][0-9]*)+)\s*】',
                      lambda m: ''.join(f'【资料{n}】' for n in re.findall(r'[0-9]+', m[1])), text)
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
        # Ordinary bracketed titles are prose, not malformed evidence IDs.
        # Reserved source/figure markers still require the declared syntax.
        if re.search(r'【(?:资料|图)[^】]*】', MARK.sub('',clean)): issues.append('unknown_marker')
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


def split_review_record(text, sources, image_ids):
    """Separate a visible review decision from publishable prose; no inference."""
    if not isinstance(text, str):
        return text, '', ['missing_review_record']
    text = text.strip()
    start = re.match(r'## 审查记录[ \t]*\n', text)
    boundaries = list(re.finditer(r'(?m)^## 最终知识[ \t]*$', text))
    if not start or len(boundaries) > 1 or len(re.findall(r'(?m)^## 审查记录[ \t]*$', text)) != 1:
        return None, '', ['invalid_review_record_boundary']
    if boundaries:
        notes_end, body_start = boundaries[0].start(), boundaries[0].end()
    else:
        # A plain review record ends at the first article heading. The extra
        # "final knowledge" wrapper is optional; body validation stays strict.
        end = re.search(r'(?m)^## [^\n]+', text[start.end():])
        if not end:
            return None, '', ['invalid_review_record_boundary']
        notes_end = body_start = start.end() + end.start()
    notes = text[start.end():notes_end].strip()
    body = text[body_start:].strip()
    issues = [] if notes else ['empty_review_record']
    # Audit references name the input catalog, not the selected output images.
    # Expand explicit ascending ranges only inside a reference marker, bounded
    # by the input catalog. This does not assign evidence to knowledge prose.
    def expand_range(match):
        kind, first, last = match.groups()
        first, last = int(first), int(last)
        limit = len(sources) if kind == '资料' else len(image_ids)
        if not first <= last <= limit:
            issues.append('invalid_review_reference_range')
            return match[0]
        return ''.join(f'【{kind}{n}】' for n in range(first, last + 1))
    notes = re.sub(r'【(资料|图)\s*([1-9][0-9]*)\s*[-–]\s*([1-9][0-9]*)\s*】', expand_range, notes)
    notes = re.sub(r'【(资料|图)\s*([1-9][0-9]*(?:\s*[,，、/]\s*[1-9][0-9]*)*)\s*】',
                   lambda m: ''.join(f'【{m[1]}{n}】' for n in re.findall(r'[0-9]+', m[2])), notes)
    notes = re.sub(r'(?<!【)(资料|图)\s*([1-9][0-9]*(?:\s*[,，、/]\s*[1-9][0-9]*)*)',
                   lambda m: ''.join(f'【{m[1]}{n}】' for n in re.findall(r'[0-9]+', m[2])), notes)
    # An immediately adjacent numbered chain explicitly shares its first type.
    # Do not carry that type across prose/punctuation or infer bare references.
    notes = re.sub(r'【(资料|图)([1-9][0-9]*)】((?:【[1-9][0-9]*】)+)',
                   lambda m: ''.join(f'【{m[1]}{n}】' for n in [m[2], *re.findall(r'[0-9]+', m[3])]), notes)
    if DONE in notes:
        issues.append('premature_review_completion')
    for kind, number in MARK.findall(notes):
        if int(number) > (len(sources) if kind == '资料' else len(image_ids)):
            issues.append('unknown_review_reference')
    if re.search(r'【[^】]*】', MARK.sub('', notes)):
        issues.append('unknown_review_marker')
    return body, notes, issues


class ApplyArticle:
    def __init__(self, final=False, image_selection_only=False, review_notes_required=False):
        if image_selection_only and not final:
            raise ValueError('Image selection only applies to final review')
        self.final = final
        self.image_selection_only = image_selection_only
        if review_notes_required and not final:
            raise ValueError('Review records only apply to final review')
        self.review_notes_required = review_notes_required
    def __call__(self, row):
        out = dict(row)
        if row.get('preflight_error')=='no_extracted_knowledge':
            return {**out,'article_topics':[],'article_text':None,'validation_issues':[],
                    'empty_reason':'联合提取没有可保留知识','article_status':'no_supported_knowledge','article_call':None}
        body = row.get('prompt_result')
        record_issues = []
        if self.review_notes_required:
            body, notes, record_issues = split_review_record(body, row.get('source_catalog', []), row.get('article_image_ids', []))
            out['article_review_notes'] = notes
        topics, issues, empty = parse_article(body, row.get('source_catalog', []), row.get('article_image_ids', []), final=self.final)
        issues.extend(record_issues)
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
    def __init__(self, definitions=None, counter=None, max_input_tokens=65536, *, draft_outline_only=False):
        self.definitions=definitions or {}; self.counter=counter; self.limit=max_input_tokens
        self.draft_outline_only=draft_outline_only

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
                    if self.draft_outline_only:
                        outline=[]
                        for t in text_topics:
                            refs=sorted({sm[n] for p in t['paragraphs'] for k,n in p['refs'] if k=='资料'})
                            outline.append('- '+t['title']+' '+''.join(f'【资料{n}】' for n in refs))
                        draft='\n'.join(outline)
                    else:
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
             'article_input':render_input(group['concept'],scope,sources,image_ids,drafts,draft_outline_only=self.draft_outline_only),
             'draft_outline_only':self.draft_outline_only, 'preflight_error':None}
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


class FinalizeArticle:
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
        from preparation.operaters.identity import material_id
        unexamined_docs={material_id(m) for r in materials for m in r.get('cleaned_materials',[])
                         if 'cleaning' in m and material_id(m) in r.get('identity_unexamined',[])}
        review_references = []
        for kind, value in dict.fromkeys(MARK.findall(reviewed.get('article_review_notes') or '')):
            number = int(value)
            catalog = reviewed.get('source_catalog' if kind == '资料' else 'article_image_ids', [])
            if number > len(catalog):
                continue  # Invalid notes remain a failed result, never invent a source.
            if kind == '资料':
                original = catalog[number - 1]
                reference = make_reference('text', sources.get(original['source_id'], original))
            else:
                iid = catalog[number - 1]
                reference = make_reference('image', images.get(iid, {}), iid)
            review_references.append({'input_marker': f'【{kind}{number}】', **reference})
        return {'concept':row['concept'],'case_id':row['concept'],'knowledge':articles,
                'pipeline_version':'V2', 'published_passages':list(sources.values()),
                'published_images':[{**images[iid], 'image_id':iid} for iid in dict.fromkeys(used_images)],
                'visual_materials':list({v['publication']['sha256']:v for r in materials
                                        for v in r.get('visual_materials',[])}.values()),
                'visual_publication_issues':[i for r in materials for i in r.get('visual_publication_issues',[])],
                'status':status,'status_reason':reason or None,
                'identity':[r.get('identity') for r in materials],
                'documents':[m for r in materials for m in r.get('cleaned_materials',[]) if 'cleaning' in m],
                'images':[m for r in materials for m in r.get('cleaned_materials',[]) if m.get('kind') in {'legacy_images','qid_images'}],
                'audit':{'review_call':reviewed.get('article_call'),'validation_issues':reviewed.get('validation_issues',[]),
                         'review_notes':reviewed.get('article_review_notes'),
                         'review_references':review_references,
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
        from preparation.prompts import material_prompt_pack
        from demiflow.operator_llm.client import _response_contract_instruction
        from demiflow.operator_llm import compile_template
        from types import SimpleNamespace
        import yaml
        self.tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True)
        self.pre=json.loads((Path(model_path)/'preprocessor_config.json').read_text())
        if self.pre.get('processor_class')!='Qwen3VLProcessor':raise ValueError('Unsupported image token counter')
        _,text=material_prompt_pack(config)
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
            from preparation.operaters.images import pixels as load_pixels
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


import pyarrow as pa
from preparation.operaters.images import canonical, identity

ARTICLES_URI='demiwtg/preparation/datasets/articles.lance'
REFERENCE=pa.struct([(k,pa.string()) for k in ('title','url','local_path','source_note','original_url')]+[
    ('kinds',pa.list_(pa.string())),('paragraph_indices',pa.list_(pa.int64())),('source_ids',pa.list_(pa.string()))])
PLACEMENT=pa.struct([('caption',pa.large_string()),('figure_number',pa.int64()),('image_id',pa.string()),
    ('limitations',pa.large_string()),('paragraph_index',pa.int64()),('region',pa.string())])
TOPIC=pa.struct([('title',pa.string()),('content',pa.struct([
    ('paragraphs',pa.list_(pa.large_string())),('images',pa.list_(PLACEMENT))])),('references',pa.list_(REFERENCE))])
CITATION=pa.struct([('source_id',pa.string()),('source_family',pa.string()),('title',pa.string()),('text',pa.large_string()),
    ('sections',pa.list_(pa.string())),('context_before',pa.list_(pa.large_string())),
    ('context_after',pa.list_(pa.large_string())),('reference_notes',pa.list_(pa.large_string()))])
ILLUSTRATION=pa.struct([('image_id',pa.string()),('sha256',pa.string()),('evidence_json',pa.large_string())])
ARTICLES=pa.schema([pa.field('article_id',pa.string(),nullable=False),
    ('concept',pa.string()),('article_kind',pa.string()),('case_id',pa.string()),
    ('review_status',pa.string()),('status_reason',pa.large_string()),('release_ids',pa.list_(pa.string())),
    ('content',pa.list_(TOPIC)),('citations',pa.list_(CITATION)),('illustrations',pa.list_(ILLUSTRATION)),
    ('context_json',pa.large_string()),('source_file',pa.string()),('source_row',pa.int64()),('run_id',pa.string())])



def clean_article_delivery(row):
    """发布前检查正文依据并排除明确的配图指代段落；原始内容留在审计字段。"""
    audit = dict(row.get('audit') or {})
    failures = list(audit.get('validation_issues') or [])
    if audit.get('preflight_error'):
        failures.append(str(audit['preflight_error']))
    citations = {item['source_id'] for item in row.get('published_passages') or []}
    topics, excluded = [], []
    # 只识别明确的图号/方位图/指图表达，不把“流程图中”等概念正文当作配图指代。
    figure_reference = re.compile(
        r'图\s*[0-9一二三四五六七八九十]+|[上下左右]图|如图所示|'
        r'(?:这|该|此)(?:张|幅)?(?:图|照片|图片)|'
        r'(?:^|[，。；：！？\s])(?:图中|图片中|照片中|画面中)'
    )
    for ti, topic in enumerate(row.get('knowledge') or []):
        retained = {}
        paragraphs = []
        for pi, text in enumerate(topic['content'].get('paragraphs') or []):
            if figure_reference.search(text):
                excluded.append({'topic_index': ti, 'paragraph_index': pi,
                                 'text': text, 'reason': 'depends_on_figure'})
                continue
            refs = [r for r in topic.get('references') or [] if pi in (r.get('paragraph_indices') or [])]
            source_ids = {sid for r in refs if 'text' in (r.get('kinds') or [])
                          for sid in r.get('source_ids') or []}
            # 可独立阅读的图像观察描述仍可保留，原有图像依据只用于 preparation 审计。
            has_image_evidence = any('image' in (r.get('kinds') or []) for r in refs)
            if not text.strip() or not (source_ids or has_image_evidence) or source_ids - citations:
                failures.append(f'invalid_paragraph_evidence:{ti}:{pi}')
            retained[pi] = len(paragraphs)
            paragraphs.append(text)
        if paragraphs:
            topics.append({**topic, 'content': {**topic['content'], 'paragraphs': paragraphs,
                'images': [{**im, 'paragraph_index': retained[im['paragraph_index']]}
                           for im in topic['content'].get('images') or [] if im['paragraph_index'] in retained]},
                'references': [{**r, 'paragraph_indices': [retained[i] for i in r.get('paragraph_indices') or [] if i in retained]}
                               for r in topic.get('references') or []
                               if any(i in retained for i in r.get('paragraph_indices') or [])]})
    status, reason = row.get('status') or 'unreviewed', row.get('status_reason')
    if status == 'reviewed' and failures:
        status, reason = 'failed', '; '.join(dict.fromkeys(failures))
    elif status == 'reviewed' and excluded and not topics:
        status, reason = 'insufficient_materials', 'No independent text remains after excluding figure-dependent paragraphs'
    if excluded:
        audit['excluded_figure_paragraphs'] = excluded
    return {**row, 'knowledge': topics, 'status': status, 'status_reason': reason, 'audit': audit}

def article_entity(row, *, release_id=None, run_id=None, source_file=None, source_row=None, article_id=None):
    row = clean_article_delivery(row)
    known={'concept','case_id','status','status_reason','knowledge','published_passages','published_images'}
    illustrations=[]
    for item in row.get('published_images',[]):
        illustrations.append({'image_id':item.get('image_id') or item.get('record',{}).get('image_id'),
            'sha256':item.get('bytes',{}).get('sha256') or item.get('sha256'), 'evidence_json':canonical(item)})
    return dict(article_id=article_id or identity(['article',run_id or release_id,row['concept']]),
        concept=row['concept'],article_kind='knowledge',case_id=row.get('case_id'),
        review_status=row.get('status') or 'unreviewed',status_reason=row.get('status_reason'),
        release_ids=[release_id] if release_id and row.get('status')=='reviewed' else [],
        content=row.get('knowledge',[]),citations=row.get('published_passages',[]),illustrations=illustrations,
        context_json=canonical({k:v for k,v in row.items() if k not in known}),
        source_file=source_file,source_row=source_row,run_id=run_id)


def article_record(row):
    result=json.loads(row.get('context_json') or '{}')
    # Drop absent optional reference properties to preserve the original business contract.
    content=[]
    for t in row.get('content') or []:
        content.append({**t,'references':[{k:v for k,v in r.items() if v is not None} for r in t['references'] or []]})
    result.update(concept=row['concept'],case_id=row.get('case_id'),status=row['review_status'],
        status_reason=row.get('status_reason'),knowledge=content,published_passages=row.get('citations') or [],
        published_images=[json.loads(i['evidence_json']) for i in row.get('illustrations') or []])
    return result


def write_articles(root, rows, *, target_uri=None, write_mode='merge'):
    """写入文章结果表；merge 保留现有主键合并，append/overwrite 使用 Lance 原生语义。"""
    if write_mode not in {'merge', 'append', 'overwrite'}:
        raise ValueError('write_mode must be merge, append or overwrite')
    from demiflow.lance.transaction import registered_table_edit
    from demiflow.lance.registry import Catalog
    import lance
    target_uri = str((Path(root) / (target_uri or ARTICLES_URI)).resolve().relative_to(Path(root).resolve()))
    rows=list(rows)
    if not rows and write_mode == 'merge':raise ValueError('No articles to write')
    table=pa.Table.from_pylist(rows,schema=ARTICLES)
    with registered_table_edit(root,target_uri,schema_name='articles',schema_version='v1') as ds:
        if ds is None or write_mode != 'merge':
            # 显式覆盖允许空结果清空目标；追加不合并或更新已有文章行。
            create_indexes = ds is None or write_mode == 'overwrite'
            ds=lance.write_dataset(table,str(Path(root)/target_uri), mode='create' if write_mode == 'merge' else write_mode)
            if create_indexes and table.num_rows:
                ds.create_scalar_index('article_id','BTREE')
                ds.create_scalar_index('concept','BTREE')
                ds.create_scalar_index('release_ids','LABEL_LIST')
        else:
            keys=', '.join("'"+r['article_id']+"'" for r in rows)
            existing={r['article_id']:r for r in ds.to_table(filter=f'article_id IN ({keys})').to_pylist()}
            changed=[r for r in table.to_pylist() if existing.get(r['article_id'])!=r]
            if changed:ds.merge_insert('article_id').when_matched_update_all().when_not_matched_insert_all().execute(pa.Table.from_pylist(changed,schema=ARTICLES))
    return max((r for r in Catalog(root).registered() if r.resolve(root)==str(Path(root)/target_uri)),key=lambda r:r.lance_version)
