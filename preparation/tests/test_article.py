import copy
from preparation.operaters.article import (parse_article, PrepareArticleInput, PrepareFinalReview, ApplyArticle,
                         FinalizeArticle, decode_source_url)

SOURCES=[{'source_id':'s1','text':'原文事实','context_before':'必要条件'}]

def test_bracketed_publication_title_is_prose_not_an_evidence_marker():
    body='## 摄影资料\n栏目【摄影吹水王】介绍了微距摄影。【资料1】\n【完成】'
    for final in (False, True):
        topics, issues, _ = parse_article(body, SOURCES, [], final=final)
        assert not issues
        assert topics[0]['paragraphs'][0]['text'] == '栏目【摄影吹水王】介绍了微距摄影。'
        assert topics[0]['paragraphs'][0]['refs'] == [('资料', 1)]
        uncited = body.replace('【资料1】', '')
        assert 'paragraph_without_evidence' in parse_article(uncited, SOURCES, [], final=final)[1]


def test_malformed_reserved_evidence_markers_still_fail():
    for marker in ('【资料X】', '【资料1-3】', '【图1,2】'):
        body='## 摄影资料\n描述' + marker + '。【资料1】\n【完成】'
        for final in (False, True):
            assert 'unknown_marker' in parse_article(body, SOURCES, ['image'], final=final)[1]


def test_final_review_record_is_saved_but_not_published_as_knowledge():
    text='## 审查记录\n删除草稿中无依据的扩展说法，保留原文事实。【资料1】\n## 最终知识\n## 事实\n原文事实。【资料1】\n【完成】'
    result=ApplyArticle(final=True,review_notes_required=True)({'prompt_result':text,'source_catalog':SOURCES,'article_image_ids':[]})
    assert result['article_status']=='reviewed'
    published=FinalizeArticle()({'concept':'目标','materials':[],'review':result})
    assert published['knowledge'][0]['content']['paragraphs']==['原文事实。']
    assert published['audit']['review_notes']=='删除草稿中无依据的扩展说法，保留原文事实。【资料1】'
    assert published['audit']['review_references'][0]['input_marker']=='【资料1】'
    assert published['audit']['review_references'][0]['source_ids']==['s1']
    assert result['article_text']==text  # Full original response remains auditable.


def test_final_review_record_cannot_substitute_for_final_evidence_or_completion():
    prefix='## 审查记录\n原文事实有来源。【资料1】\n## 最终知识\n'
    for body in ['## 事实\n没有自己的引用。\n【完成】','## 事实\n原文事实。【资料1】']:
        result=ApplyArticle(final=True,review_notes_required=True)({'prompt_result':prefix+body,'source_catalog':SOURCES,'article_image_ids':[]})
        assert result['article_status']=='failed'
        assert not FinalizeArticle()({'concept':'目标','materials':[],'review':result})['knowledge']


def test_final_review_record_rejects_missing_ambiguous_or_unknown_input_references():
    body='## 最终知识\n## 事实\n原文事实。【资料1】\n【完成】'
    variants=[body,'## 审查记录\n\n'+body,'## 审查记录\n问题。【资料2】\n'+body,
              '## 审查记录\n问题。【图1】\n'+body,'## 审查记录\n【完成】\n'+body,
              '## 审查记录\n问题。\n'+body+'\n## 最终知识']
    for text in variants:
        result=ApplyArticle(final=True,review_notes_required=True)({'prompt_result':text,'source_catalog':SOURCES,'article_image_ids':[]})
        assert result['article_status']=='failed'
        assert not FinalizeArticle()({'concept':'目标','materials':[],'review':result})['knowledge']


def test_review_record_normalizes_explicit_input_references_without_selecting_images():
    text='## 审查记录\n资料1保留；【图1, 2】不选，图2不清楚。\n## 最终知识\n## 事实\n原文事实。【资料1】\n【完成】'
    result=ApplyArticle(final=True,image_selection_only=True,review_notes_required=True)(
        {'prompt_result':text,'source_catalog':SOURCES,'article_image_ids':['a','b']})
    assert result['article_status']=='reviewed'
    assert result['article_text']==text
    assert result['article_review_notes']=='【资料1】保留；【图1】【图2】不选，【图2】不清楚。'
    assert all(not t['images'] for t in result['article_topics'])


def test_review_record_checks_bare_and_list_numbers_without_inventing_ranges():
    from preparation.operaters.article import split_review_record
    for note in ['资料2不存在。','图2不存在。','【资料1, 2】冲突。','【图1、2】不选。']:
        _,_,issues=split_review_record('## 审查记录\n'+note+'\n## 最终知识\n【完成】',SOURCES,['a'])
        assert 'unknown_review_reference' in issues
    _,_,issues=split_review_record('## 审查记录\n【资料1-2】。\n## 最终知识\n【完成】',SOURCES,['a'])
    assert 'unknown_review_marker' in issues


def test_review_record_expands_explicit_audit_lists_and_bounded_ranges():
    from preparation.operaters.article import split_review_record
    sources=SOURCES+[{'source_id':'s2','text':'另一资料'},{'source_id':'s3','text':'第三资料'}]
    body,notes,issues=split_review_record(
        '## 审查记录\n资料1、2、3有分歧。【资料1–3】与【资料1/3】。\n## 事实\n可靠内容。【资料1】\n【完成】',sources,[])
    assert not issues
    assert notes=='【资料1】【资料2】【资料3】有分歧。【资料1】【资料2】【资料3】与【资料1】【资料3】。'
    assert body=='## 事实\n可靠内容。【资料1】\n【完成】'
    for marker in ['【资料3–1】','【资料1-999999999】']:
        assert 'invalid_review_reference_range' in split_review_record(
            '## 审查记录\n'+marker+'\n## 事实\n可靠内容。【资料1】\n【完成】',sources,[])[2]


def test_adjacent_audit_references_share_explicit_type_only_within_the_chain():
    from preparation.operaters.article import split_review_record
    sources=SOURCES+[{'source_id':'s2','text':'第二资料'}]
    prefix='## 审查记录\n'
    body='\n## 最终知识\n## 事实\n原文事实。【资料1】\n【完成】'
    text=prefix+'核对【资料1】【2】；不选【图1】【2】。'+body
    result=ApplyArticle(final=True,image_selection_only=True,review_notes_required=True)(
        {'prompt_result':text,'source_catalog':sources,'article_image_ids':['a','b']})
    assert result['article_status']=='reviewed' and result['article_text']==text
    assert result['article_review_notes']=='核对【资料1】【资料2】；不选【图1】【图2】。'
    assert not any(t['images'] for t in result['article_topics'])
    for note in ['【2】','【资料1】，再看【2】','【资料1】【0】']:
        assert 'unknown_review_marker' in split_review_record(prefix+note+body,sources,[])[2]
    assert 'unknown_review_reference' in split_review_record(prefix+'【资料1】【3】'+body,sources,[])[2]


def test_review_article_heading_boundary_keeps_evidence_and_completion_checks():
    prefix='## 审查记录\n删除无关内容。【资料1】\n'
    for body,expected in [('## 事实\n原文事实。【资料1】\n【完成】','reviewed'),
                          ('## 事实\n无依据正文。\n【完成】','failed'),
                          ('## 事实\n原文事实。【资料1】','failed'),
                          ('仅有记录，没有正文边界。【资料1】\n【完成】','failed')]:
        result=ApplyArticle(final=True,review_notes_required=True)(
            {'prompt_result':prefix+body,'source_catalog':SOURCES,'article_image_ids':[]})
        assert result['article_status']==expected
        assert result['article_text']==prefix+body


def test_final_review_record_can_report_no_supported_knowledge():
    text='## 审查记录\n没有可靠材料。\n## 最终知识\n【无可保留知识】没有可靠材料。\n【完成】'
    result=ApplyArticle(final=True,review_notes_required=True)({'prompt_result':text,'source_catalog':[],'article_image_ids':[]})
    assert result['article_status']=='no_supported_knowledge'
    published=FinalizeArticle()({'concept':'目标','materials':[],'review':result})
    assert not published['knowledge'] and published['audit']['review_notes']=='没有可靠材料。'


def test_review_record_references_do_not_reintroduce_rejected_images():
    text='## 审查记录\n【图1】身份不明，不保留。\n## 最终知识\n## 事实\n原文事实。【资料1】\n【完成】'
    result=ApplyArticle(final=True,image_selection_only=True,review_notes_required=True)(
        {'prompt_result':text,'source_catalog':SOURCES,'article_image_ids':['rejected']})
    materials=[{'identity':{'target_label':'目标'},'cleaned_materials':[],
                'material_pack':{'passages':[],'images':[{'image_id':'rejected','record':{}}]}}]
    published=FinalizeArticle()({'concept':'目标','materials':materials,'review':result})
    assert result['article_status']=='reviewed'
    assert not any(t['content']['images'] for t in published['knowledge'])
    assert published['audit']['selected_image_ids']==[]
    assert published['audit']['review_references'][0]['source_ids']==['rejected']

def test_final_comma_citations_preserve_all_explicit_sources_and_prose():
    sources=SOURCES+[{'source_id':'s2','text':'第二来源'},{'source_id':'s3','text':'第三来源'}]
    text='## 共同知识\n所引结论。【资料1, 2、3】\n【完成】'
    result=ApplyArticle(final=True)({'prompt_result':text,'source_catalog':sources,'article_image_ids':[]})
    assert result['article_status']=='reviewed'
    assert result['article_topics'][0]['paragraphs']==[
        {'text':'所引结论。','refs':[('资料',1),('资料',2),('资料',3)]}]
    published=FinalizeArticle()({'concept':'目标','materials':[],'review':result})
    assert published['knowledge'][0]['content']['paragraphs']==['所引结论。']
    assert [r['source_ids'] for r in published['knowledge'][0]['references']]==[['s1'],['s2'],['s3']]
    assert 'unknown_marker' in parse_article(text,sources,[])[1]  # Frozen joint parsing unchanged.

def test_final_source_lists_cannot_hide_unknown_numbers_or_infer_ranges():
    for marker,issue in [('【资料1, 2】','unknown_资料_2'),('【资料1-3】','unknown_marker'),
                         ('【资料1,】','unknown_marker'),('【图1,2】','unknown_marker')]:
        result=ApplyArticle(final=True)({'prompt_result':f'## 事实\n原文事实。【资料1】{marker}\n【完成】',
                                        'source_catalog':SOURCES,'article_image_ids':[]})
        assert result['article_status']=='failed' and issue in result['validation_issues']
        assert not FinalizeArticle()({'concept':'目标','materials':[],'review':result})['knowledge']

def test_final_list_footer_cites_the_whole_explicit_list_block():
    sources=SOURCES+[{'source_id':'s2','text':'补充原文'}]
    text='## 符号\n所列符号如下：\n\n- 矩形：步骤。\n- 菱形：判断。\n\n【资料1】【资料2】\n【完成】'
    result=ApplyArticle(final=True)({'prompt_result':text,'source_catalog':sources,'article_image_ids':[]})
    assert result['article_status']=='reviewed'
    paragraph=result['article_topics'][0]['paragraphs'][0]
    assert paragraph=={'text':'所列符号如下：\n- 矩形：步骤。\n- 菱形：判断。','refs':[('资料',1),('资料',2)]}
    published=FinalizeArticle()({'concept':'符号','materials':[],'review':result})
    assert published['knowledge'][0]['content']['paragraphs']==[paragraph['text']]
    assert {s for ref in published['knowledge'][0]['references'] for s in ref['source_ids']}=={'s1','s2'}
    assert parse_article(text,sources,[])[1]  # Frozen joint parsing is unchanged.

def test_final_list_footer_does_not_cover_other_paragraphs_or_unknown_sources():
    body='## 符号\n未引用的独立断言。\n\n所列符号如下：\n\n1. 矩形：步骤。\n2. 菱形：判断。\n\n【资料1】\n【完成】'
    topics,issues,_=parse_article(body,SOURCES,[],final=True)
    assert issues==['paragraph_without_evidence']
    assert topics[0]['paragraphs'][0]['refs']==[]
    assert topics[0]['paragraphs'][1]['refs']==[('资料',1)]
    assert 'unknown_资料_2' in parse_article(body.replace('【资料1】','【资料2】'),SOURCES,[],final=True)[1]

def test_final_image_selection_publishes_originals_with_program_labels_only():
    result=ApplyArticle(final=True,image_selection_only=True)({'prompt_result':'## 原文知识\n原文事实。【资料1】\n\n## 配图\n【图2】\n\n【图1】\n【完成】',
        'source_catalog':SOURCES,'article_image_ids':['first','second']})
    assert result['article_status']=='reviewed'
    materials=[{'identity':{'target_label':'目标'},'cleaned_materials':[],'material_pack':{'passages':[],
        'images':[{'image_id':i,'record':{}} for i in ['first','second']]}}]
    published=FinalizeArticle()({'concept':'目标','review':result,'materials':materials})
    text,figures=published['knowledge']
    assert text['content']['paragraphs']==['原文事实。'] and not text['content']['images']
    assert figures['content']['paragraphs']==['图1','图2']
    assert [(i['image_id'],i['figure_number'],i['paragraph_index'],i['caption']) for i in figures['content']['images']]==[
        ('second',1,0,''),('first',2,1,'')]
    assert [r['source_ids'] for r in figures['references']]==[['second'],['first']]
    assert [i['image_id'] for i in published['published_images']]==['second','first']
    assert published['images']==[]

def test_image_selection_mode_rejects_generated_captions_and_wrong_references():
    base={'source_catalog':SOURCES,'article_image_ids':['image']}
    for body in ['## 配图\n【图1】声称图片过程。','## 配图\n声称图片过程。【图1】',
                 '## 正文\n原文事实。【资料1】\n【图1】']:
        result=ApplyArticle(final=True,image_selection_only=True)({**base,'prompt_result':body+'\n【完成】'})
        assert result['article_status']=='failed'
    for body,issue in [('## 配图\n【图2】\n【完成】','unknown_image_2'),
                       ('## 配图\n【图1】\n【图1】\n【完成】','duplicate_image'),
                       ('## 配图\n【图1】','missing_completion_marker')]:
        result=ApplyArticle(final=True,image_selection_only=True)({**base,'prompt_result':body})
        assert result['article_status']=='failed' and issue in result['validation_issues']
    text='## 配图\n【图1】\n【完成】'
    assert ApplyArticle(final=True,image_selection_only=True)({**base,'prompt_result':text})['article_status']=='reviewed'
    assert parse_article(text,SOURCES,['image'])[1]  # Frozen joint format is unchanged.

def test_final_untitled_intro_keeps_all_text_and_its_own_evidence():
    text='原文事实。【资料1】\n\n另一条原文知识。【资料1】\n\n## 图中可见的内容\n【图1】蓝色圆环。\n【完成】'
    result=ApplyArticle(final=True)({'prompt_result':text,'source_catalog':SOURCES,'article_image_ids':['image']})
    assert result['article_status']=='reviewed'
    assert [t['title'] for t in result['article_topics']]==['概述','图中可见的内容']
    intro,visual=result['article_topics']
    assert [p['text'] for p in intro['paragraphs']]==['原文事实。','另一条原文知识。']
    assert all(p['refs']==[('资料',1)] for p in intro['paragraphs'])
    assert not intro['images']
    assert visual['paragraphs'][0]['refs']==[('图',1)]
    assert visual['images'][0]['paragraph_index']==0
    assert 'text_before_topic' in parse_article(text,SOURCES,['image'])[1]

def test_final_missing_heading_does_not_relax_evidence_or_completion():
    assert 'paragraph_without_evidence' in parse_article('未引用的内容。\n【完成】',SOURCES,[],final=True)[1]
    assert 'unknown_资料_2' in parse_article('内容。【资料2】\n【完成】',SOURCES,[],final=True)[1]
    assert 'missing_completion_marker' in parse_article('内容。【资料1】',SOURCES,[],final=True)[1]
    topics,issues,reason=parse_article('【无可保留知识】没有可靠材料。\n【完成】',[],[],final=True)
    assert not topics and not issues and reason=='没有可靠材料。'

def test_final_source_as_sentence_subject_survives_publication():
    text='## 符号\n【资料1】中列出的符号有特定含义。\n\n根据资料1，符号应结合上下文理解。【资料1】\n\n其含义没有改变。【资料1】\n【完成】'
    result=ApplyArticle(final=True)({'prompt_result':text,'source_catalog':SOURCES,'article_image_ids':[]})
    assert result['article_status']=='reviewed'
    published=FinalizeArticle()({'concept':'符号','materials':[],'review':result})
    topic=published['knowledge'][0]
    assert topic['content']['paragraphs']==['所引资料中列出的符号有特定含义。','根据所引资料，符号应结合上下文理解。','其含义没有改变。']
    assert topic['references'][0]['source_ids']==['s1']
    assert topic['references'][0]['paragraph_indices']==[0,1,2]

def test_final_source_markers_keep_evidence_without_duplicating_readable_subject():
    sources=SOURCES+[{'source_id':'s2','text':'另一原文'}]
    topics,issues,_=parse_article('## 符号\n【资料1】【资料2】均指出符号需约定。\n\n共同结论【资料1】【资料2】。后续说明。【资料2】\n【完成】',sources,[],final=True)
    assert not issues
    assert [p['text'] for p in topics[0]['paragraphs']]==['所引资料均指出符号需约定。','共同结论。后续说明。']
    assert topics[0]['paragraphs'][0]['refs']==[('资料',1),('资料',2)]
    assert 'unknown_资料_3' in parse_article('## 符号\n根据资料3给出含义。\n【完成】',sources,[],final=True)[1]

def test_final_subheadings_are_sections_without_changing_frozen_draft_parsing():
    text='## 方法\n事实。【资料1】\n\n### 条件\n【资料1】中列出了条件。\n【完成】'
    topics,issues,_=parse_article(text,SOURCES,[],final=True)
    assert not issues and [t['title'] for t in topics]==['方法','条件']
    assert topics[1]['paragraphs'][0]['text']=='所引资料中列出了条件。'
    draft,_,_=parse_article(text,SOURCES,[])
    assert len(draft)==1 and '### 条件' in draft[0]['paragraphs'][1]['text']

def test_plain_article_restores_references_and_image_relationship():
    text='## 包装\n图中包装标注5克。【资料1】【图1】\n\n【图1】标注5克的盒子\n局限：只见外包装\n【完成】'
    topics,issues,empty=parse_article(text,SOURCES,['image1'])
    assert not issues and empty is None
    assert topics[0]['paragraphs'][0]['refs']==[('资料',1),('图',1)]
    assert topics[0]['images'][0]['paragraph_index']==0
    assert topics[0]['images'][0]['limitations']=='只见外包装'

def test_unknown_source_and_missing_finish_are_rejected():
    _,issues,_=parse_article('## 标题\n内容。【资料2】',SOURCES,[])
    assert 'unknown_资料_2' in issues and 'missing_completion_marker' in issues

def test_cited_image_is_attached_by_code_but_duplicate_explicit_placement_rejected():
    topics,issues,_=parse_article('## 标题\n内容。【图1】\n【完成】',[],['i'])
    assert not issues and topics[0]['images'][0]=={'number':1,'caption':'','limitations':'','paragraph_index':0}
    _,issues,_=parse_article('## 标题\n内容。【图1】\n【图1】一图\n【图1】同图\n【完成】',[],['i'])
    assert 'duplicate_image' in issues

def test_internal_id_not_removed_silently():
    _,issues,_=parse_article('## 标题\n如I123456789abc所示。【资料1】\n【完成】',SOURCES,[])
    assert 'internal_id_in_prose' in issues

def test_unmarked_figure_number_cannot_survive_renumbering():
    _,issues,_=parse_article('## 标题\n见图17的内容。【资料1】\n【完成】',SOURCES,[])
    assert 'unmarked_image_number' in issues

def test_known_readable_figure_is_normalized_before_renumbering():
    topics,issues,_=parse_article('## 形态\n图2中可见蓝色圆环。\n【完成】',[],['red','blue'])
    assert not issues and topics[0]['paragraphs'][0]['text']=='【图2】中可见蓝色圆环。'

def test_image_observation_starting_with_reference_is_not_a_caption():
    topics,issues,_=parse_article('## 形态\n【图1】中可见蓝色圆环。【图1】\n【完成】',[],['image'])
    assert not issues
    assert topics[0]['paragraphs'][0]['text']=='【图1】中可见蓝色圆环。'
    assert topics[0]['images'][0]['caption']==''

def test_inline_figure_numbers_follow_final_image_order():
    result=ApplyArticle(final=True)({'prompt_result':'## 形态\n【图2】中可见蓝色圆环。【图2】\n\n【图1】中可见红色圆环。【图1】\n【完成】',
                                    'source_catalog':[],'article_image_ids':['red','blue']})
    materials=[{'identity':{'target_label':'环'},'cleaned_materials':[],'material_pack':{'passages':[],
                 'images':[{'image_id':iid,'record':{}} for iid in ['red','blue']]}}]
    published=FinalizeArticle()({'concept':'环','materials':materials,'review':result})
    topic=published['knowledge'][0]
    assert topic['content']['paragraphs']==['图1中可见蓝色圆环。','图2中可见红色圆环。']
    assert [(im['image_id'],im['figure_number']) for im in topic['content']['images']]==[('blue',1),('red',2)]


def test_trailing_evidence_before_punctuation_is_not_prose():
    topics, issues, _ = parse_article('## 表现形式\n两图分别用不同颜色表示数值【图1】【图2】。\n【完成】', [], ['a', 'b'])
    assert not issues
    assert topics[0]['paragraphs'][0]['text'] == '两图分别用不同颜色表示数值。'
    assert topics[0]['paragraphs'][0]['refs'] == [('图', 1), ('图', 2)]
    assert [i['number'] for i in topics[0]['images']] == [1, 2]


def test_sentence_final_explicit_figure_reference_survives():
    for text in ['蓝色圆环见图2。', '蓝色圆环见【图2】。']:
        topics, issues, _ = parse_article('## 外形\n' + text + '\n【完成】', [], ['a', 'b'])
        assert not issues
        assert topics[0]['paragraphs'][0]['text'] == '蓝色圆环见【图2】。'
        assert topics[0]['paragraphs'][0]['refs'] == [('图', 2)]

def test_explicit_adjacent_picture_is_a_declared_visual_reference():
    topics,issues,_=parse_article('## 包装\n图中可见盒装产品。\n【图1】蓝色包装盒。\n【完成】',[],['image'])
    assert not issues and topics[0]['paragraphs'][0]['refs']==[('图',1)]
    _,issues,_=parse_article('## 包装\n没有标明来源的断言。\n【完成】',[],['image'])
    assert issues==['paragraph_without_evidence']

def test_image_description_section_needs_no_duplicate_body_and_renumbers_references():
    result=ApplyArticle(final=True)({'prompt_result':'## 包装\n【图3】蓝色盒，5克。\n\n【图1】白色盒，15克，其余标注同图3。\n【完成】',
                                    'source_catalog':[],'article_image_ids':['white','unused','blue']})
    assert result['article_status']=='reviewed'
    materials=[{'identity':{'target_label':'包装'},'cleaned_materials':[],'material_pack':{'passages':[],
                'images':[{'image_id':i,'record':{}} for i in ['white','unused','blue']]}}]
    topic=FinalizeArticle()({'concept':'包装','materials':materials,'review':result})['knowledge'][0]
    assert topic['content']['paragraphs']==['蓝色盒，5克。','白色盒，15克，其余标注同图1。']
    assert [(i['image_id'],i['paragraph_index']) for i in topic['content']['images']]==[('blue',0),('white',1)]
    assert parse_article('## 包装\n【图9】蓝色盒。\n【完成】',[],['white'])[1]

def test_caption_reference_follows_final_numbering_too():
    result=ApplyArticle(final=True)({'prompt_result':'## 包装\n产品有不同包装。【资料1】\n【图3】蓝盒5克。\n【图1】白盒15克，其余标注同图3。\n【完成】',
                                    'source_catalog':SOURCES,'article_image_ids':['white','unused','blue']})
    assert result['article_status']=='reviewed'
    materials=[{'identity':{'target_label':'包装'},'cleaned_materials':[],'material_pack':{'passages':[],
                'images':[{'image_id':i,'record':{}} for i in ['white','unused','blue']]}}]
    topic=FinalizeArticle()({'concept':'包装','materials':materials,'review':result})['knowledge'][0]
    assert topic['content']['images'][1]['caption']=='白盒15克，其余标注同图1。'


def test_draft_citation_gap_goes_to_review_without_reintroducing_uncited_sources():
    row={'concept':'目标','batch_id':'a','source_catalog':SOURCES+[{'source_id':'unused','text':'不能偷带回来的资料'}],
         'article_image_ids':[],'pixel_images':[],
         'prompt_result':'## 主题\n有引用的陈述。【资料1】\n\n还需要核对的陈述。\n【完成】'}
    draft=ApplyArticle()(row)
    assert draft['article_status']=='draft' and draft['review_required_issues']==['paragraph_without_evidence']
    request=PrepareFinalReview()({'concept':'目标','drafts':[draft]})
    assert request['preflight_error'] is None and request['source_catalog']==SOURCES
    assert '还需要核对的陈述' in request['article_input'] and '不能偷带回来的资料' not in request['article_input']
    assert ApplyArticle(final=True)(row)['article_status']=='failed'
    row['prompt_result']='## 主题\n完全没有声明依据的稿子。\n【完成】'
    assert 'no_cited_evidence' in ApplyArticle()(row)['validation_issues']

def test_input_excludes_upstream_captions_and_decisions_keeps_context():
    row={'joint_prompt':{'concept':'目标','passages':SOURCES,'image_ids':['i'],
                         'image_selection':[{'caption':'污染描述','decision':'keep','reason':'污染判断'}]},'pixel_images':['pixels']}
    out=PrepareArticleInput()(row)
    assert '污染' not in out['article_input'] and 'keep' not in out['article_input']
    assert '必要条件' in out['article_input']


def test_background_identity_survives_routing_and_review_without_generated_evidence():
    from preparation.operaters.routing import PrepareRoutingMaterials, BuildRoutedJointRequest
    from preparation.operaters.routing import RouteByTokenBudget
    source={'source_id':'s1','material_id':'m1','title':'相关方法','text':'该方法帮助理解目标。'}
    row={'case_id':'case','identity':{'target_label':'目标','material_reviews':[
             {'material_id':'m1','relation':'related_context','basis':'text','reason':'污染理由'},
             {'material_id':'m2','relation':'related_context','basis':'metadata'}]},
         'bundle':{'materials':[{'kind':'legacy_concepts','record':{'aliases':['Target']}}]},
         'cleaned_materials':[], 'material_pack':{'passages':[source,
             {'source_id':'s2','material_id':'m2','title':'图片caption对象','text':'原文二'}], 'images':[]}}
    material=PrepareRoutingMaterials()(row)
    assert material['scope_context']=={'aliases':['Target'],'background_titles':['相关方法']}
    routed=RouteByTokenBudget('',counter=lambda r:10)(material)['requests'][0]
    prepared=PrepareArticleInput()(BuildRoutedJointRequest()(routed))
    assert '不是与目标等同的对象：相关方法' in prepared['article_input']
    assert '污染理由' not in prepared['article_input'] and 'related_context' not in prepared['article_input']
    extracted=ApplyArticle()({**prepared,'prompt_result':'## 方法\n方法帮助理解目标。【资料1】\n【完成】'})
    # A separate material batch contributes another boundary; uncited sources stay out.
    other=copy.deepcopy(extracted);other['batch_id']='other'
    other['source_catalog']=[{**source,'source_id':'s3','title':'另一方法'}]
    other['scope_context']={'aliases':['Target'],'background_titles':['另一方法','未引用方法']}
    final=PrepareFinalReview()({'concept':'目标','drafts':[extracted,other]})
    assert final['scope_context']['background_titles']==['另一方法','相关方法']
    assert '未引用方法' not in final['article_input'] and '图片caption对象' not in final['article_input']
    assert '原始概念记录别名：Target' in final['article_input']
    explicit=PrepareArticleInput({'目标':'精确物种范围'})(BuildRoutedJointRequest()(routed))
    assert explicit['identity_scope'].startswith('精确物种范围')
    assert 'Target' not in explicit['identity_scope']

def draft(batch,source_id,image_id,text='内容'):
    row={'concept':'目标','batch_id':batch,'source_catalog':[{'source_id':source_id,'text':'来源'+source_id}],
         'article_image_ids':[image_id],'pixel_images':['pixel:'+image_id],
         'prompt_result':f'## 主题\n{text}。【资料1】【图1】\n【图1】图像\n【完成】'}
    return ApplyArticle()(row)

def test_review_gathers_batches_remaps_numbers_and_only_referenced_material():
    a=draft('a','s1','i1');b=draft('b','s2','i2')
    a['source_catalog'].append({'source_id':'unused','text':'未引用内容'})
    out=PrepareFinalReview()({'concept':'目标','drafts':[b,a]})
    assert out['parent_batches']==['a','b']
    assert [p['source_id'] for p in out['source_catalog']]==['s1','s2']
    assert out['article_image_ids']==['i1','i2']
    assert '【资料2】' in out['article_input'] and '【图2】' in out['article_input']
    assert '未引用内容' not in out['article_input']

def test_review_outline_removes_generated_claims_keeps_complete_evidence_pool():
    a=draft('a','s1','i1',text='草稿自行选择凉开水')
    b=draft('b','s2','i2',text='草稿补出的错误译名')
    a['source_catalog'].append({'source_id':'unused','text':'未采用的原文'})
    group={'concept':'目标','drafts':[b,a]}
    frozen=copy.deepcopy(group)
    prose=PrepareFinalReview()(group)
    outline=PrepareFinalReview(draft_outline_only=True)(group)
    assert group==frozen
    for key in ['source_catalog','article_image_ids','pixel_images','parent_batches','failed_batches','scope_context']:
        assert outline[key]==prose[key]
    assert outline['preflight_error'] is None and outline['draft_outline_only']
    assert '草稿自行选择凉开水' in prose['article_input']
    assert all(text not in outline['article_input'] for text in ['草稿自行选择凉开水','草稿补出的错误译名','未采用的原文'])
    assert '- 主题 【资料1】' in outline['article_input'] and '- 主题 【资料2】' in outline['article_input']
    assert '来源s1' in outline['article_input'] and '来源s2' in outline['article_input']

def test_review_outline_preserves_image_only_failure_and_capacity_boundaries():
    visual=ApplyArticle()({'concept':'目标','batch_id':'visual','source_catalog':[],
        'article_image_ids':['i'],'pixel_images':['pixel:i'],
        'prompt_result':'## 视觉标题\n无依据的生成图像描述。【图1】\n【完成】'})
    group={'concept':'目标','drafts':[visual]}
    out=PrepareFinalReview(draft_outline_only=True)(group)
    assert out['preflight_error'] is None and out['pixel_images']==['pixel:i']
    assert '无依据的生成图像描述' not in out['article_input'] and '视觉标题' not in out['article_input']
    out=PrepareFinalReview(draft_outline_only=True,counter=lambda r:100,max_input_tokens=10)(group)
    assert out['preflight_error']=='review_capacity_exceeded' and out['pixel_images']==['pixel:i']
    visual['article_status']='failed'
    assert PrepareFinalReview(draft_outline_only=True)(group)['preflight_error']=='failed_extraction_batches'

def test_review_omits_generated_visual_prose_but_preserves_every_adopted_pixel():
    row={'concept':'目标','batch_id':'visual','source_catalog':SOURCES,
         'article_image_ids':['a','b','unused'],'pixel_images':['pixel:a','pixel:b','pixel:unused'],
         'prompt_result':'## 错误视觉分类\n图1的颜色表示人口密度。【图1】\n\n图2的箭头返回A。【图2】\n【完成】'}
    visual=ApplyArticle()(row)
    out=PrepareFinalReview()({'concept':'目标','drafts':[visual]})
    assert out['preflight_error'] is None
    assert out['article_image_ids']==['a','b'] and out['pixel_images']==['pixel:a','pixel:b']
    assert out['source_catalog']==[]
    assert all(s not in out['article_input'] for s in ['错误视觉分类','颜色表示人口密度','箭头返回A','原文事实','unused'])
    assert '【图1】' in out['article_input'] and '【图2】' in out['article_input']
    # Text-backed draft claims still reach review alongside their actual source.
    mixed=draft('text','s2','c',text='有原文依据的待审陈述')
    out=PrepareFinalReview()({'concept':'目标','drafts':[visual,mixed]})
    assert '有原文依据的待审陈述' in out['article_input'] and '来源s2' in out['article_input']
    assert set(out['article_image_ids'])=={'a','b','c'}
    assert '颜色表示人口密度' not in out['article_input']

def test_failed_batch_prevents_partial_concept_publication():
    a=draft('a','s1','i1');b=draft('b','s2','i2');b['article_status']='failed'
    out=PrepareFinalReview()({'concept':'目标','drafts':[a,b]})
    assert out['preflight_error']=='failed_extraction_batches'
    final=ApplyArticle(final=True)(out)
    assert final['article_status']=='failed'
    assert FinalizeArticle()({'concept':'目标','materials':[],'review':final})['knowledge']==[]

def test_capacity_explicit_no_truncation():
    out=PrepareFinalReview(counter=lambda r:999,max_input_tokens=20)({'concept':'目标','drafts':[draft('a','s1','i1')]})
    assert out['preflight_error']=='review_capacity_exceeded'
    assert out['pixel_images']==['pixel:i1'] and len(out['source_catalog'])==1

def test_final_budget_counts_labels_in_the_same_order_as_actual_pixels():
    from demiflow.operator_llm import compile_template
    from preparation.operaters.article import ArticleTokenBudget
    from types import SimpleNamespace
    calls=[]
    budget=ArticleTokenBudget.__new__(ArticleTokenBudget)
    budget.compiled_template=compile_template('审查{{ payload }}\n{{ images | numbered_image }}\n完成')
    budget.system='plain';budget.thinking=True;budget.effort='low'
    budget.image_tokens=lambda p:2 if p.endswith('AAAA') else 3
    def tokenize(messages,**kwargs):
        calls.append(messages)
        return list(range(len(messages[1]['content'])))
    budget.tokenizer=SimpleNamespace(apply_chat_template=tokenize)
    result=budget({'article_input':'材料','pixel_images':['data:image/png;base64,AAAA','data:image/png;base64,BBBB']})
    expected=('审查材料\n\nImage 1:\n<|vision_start|><|image_pad|><|image_pad|><|vision_end|>'
              '\nImage 2:\n<|vision_start|><|image_pad|><|image_pad|><|image_pad|><|vision_end|>\n完成')
    assert calls[0][1]['content']==expected
    assert result==len(expected)+256

def test_empty_is_distinct_from_failed():
    out=ApplyArticle(final=True)({'prompt_result':'【无可保留知识】身份无法确认\n【完成】','source_catalog':[],'article_image_ids':[]})
    assert out['article_status']=='no_supported_knowledge' and out['empty_reason']=='身份无法确认'
    assert ApplyArticle(final=True)({'source_catalog':[],'article_image_ids':[]})['article_status']=='failed'

def test_baidu_encoded_url_is_deterministically_decoded():
    assert decode_source_url('ippr_z2C$qAzdH3FAzdH3Fkwt17_z&e3Bv54')=='http://baidu.com'
    assert decode_source_url('javascript:alert(1)')==''
