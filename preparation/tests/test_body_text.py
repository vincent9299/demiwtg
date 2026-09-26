from preparation.operaters.documents import clean_document
from preparation.operaters.text import ApplyBlockSelection
from preparation.prompts import load_instruction
from preparation.operaters.runfiles import source_code

def test_inline_media_separation_preserves_offsets():
    raw='## 地方节日\n只在当地秋季举行。 ![](https://example.org/File:X.jpg)演奏场景\n仅限静止时。\n'
    out=clean_document(raw)
    assert '只在当地秋季举行。' in out['text'] and '仅限静止时。' in out['text']
    assert '![]' not in out['text'] and '演奏场景' not in out['text']
    assert out['media'][0]['caption']=='演奏场景'
    linked=clean_document('正文。 [![](https://x/a.jpg)](https://x/page)图注\n')
    assert '[' not in linked['text'] and '正文。' in linked['text']
    for b in out['blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if 'clean_start' in b:assert out['text'][b['clean_start']:b['clean_end']]==b['text']

def test_duplicates_keep_different_section_conditions():
    out=clean_document('# 描述\n仅限静止时。\n仅限静止时。\n# 另一地区\n仅限静止时。\n# 相关推荐\n[商品一](https://example.org/1)\n[商品二](https://example.org/2)\n')
    assert out['text'].count('仅限静止时。')==2 and '商品' not in out['text']
    assert any(b.get('duplicate_of') for b in out['blocks'])

def test_relevance_does_not_require_cleaning_scores():
    row={'case_id':'C','batch_id':'B','block_prompt':{'units':[{'unit_id':'U','text':'仅限静止时。'}]},'prompt_result':{'decisions':[{'unit_id':'U','relation':'direct','decision':'selected','reason':'描述适用条件'}]}}
    assert ApplyBlockSelection(relevance_only=True)(row)['block_decisions'][0]['decision']=='selected'
    row['prompt_result']['decisions'][0]['relation']='unrelated'
    assert ApplyBlockSelection(relevance_only=True)(row)['block_decisions'][0]['decision']=='deferred'
    assert '芦笙' not in load_instruction('relevance')
    assert 'preparation/prompts/relevance.yaml' in source_code()

def test_classifier_cannot_rewrite():
    out=clean_document('# 文档\n正文句子。\n',classifier=lambda b:{'decision':'defer','reason':'uncertain','text':'invented'})
    assert all(b['text']!='invented' for b in out['blocks'])
    assert any(b['decision']=='defer' for b in out['blocks'])

def test_unmarked_panels_separate_references_without_eating_following_prose():
    raw=('标题\n正文。\n[图册](https://example.org/gallery)\n'
         '[1 图片甲](https://example.org/a)\n[4 图片乙](https://example.org/b)\n'
         '参考资料\n* 1\n[来源](https://example.org/ref)．2020-01-01 [引用日期2021-01-01]\n'
         '后续正文仍然成立。\n![](https://example.org/img)\n概述图（1张）\n')
    c=clean_document(raw)
    assert '后续正文仍然成立。' in c['text'] and '正文。' in c['text']
    assert all(s not in c['text'] for s in ['图册','图片甲','参考资料','引用日期','概述图'])
    assert any(x.get('content_role')=='reference' for x in c['supplements'])
    assert any(x.get('related_image_block_id') for x in c['supplements'])
    for b in c['blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if 'clean_start' in b:assert c['text'][b['clean_start']:b['clean_end']]==b['text']

def test_labels_and_numbers_alone_do_not_delete_prose_or_tables():
    raw=('图册\n这部图册介绍植物的结构。\n参考资料\n参考资料不足时不能确定起源。\n'
         '1 长管\n2 短管\n|参数|数值|\n|A|2|\n这种作品称为概述图（1张）\n')
    c=clean_document(raw)
    for s in ['这部图册介绍植物的结构。','参考资料不足时不能确定起源。','1 长管','|A|2|','这种作品称为概述图（1张）']:
        assert s in c['text']
