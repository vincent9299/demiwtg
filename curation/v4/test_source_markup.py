from curation.v4.ops.cleaning import clean_document
from curation.v4.ops.passage_selection import select_passages


def assert_alignment(raw,c):
    assert ''.join(b['raw_text'] for b in c['blocks'])==raw
    for b in c['blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if 'clean_start' in b:assert c['text'][b['clean_start']:b['clean_end']]==b['text']


def test_multiline_value_reference_and_unknown_condition_preserved():
    raw='{{Short description|Physical scope}}\nOnly in vacuum: {{val|12|2|e=3|u=cm}}.<ref name="a">Measured at 20 C.\nOriginal paper.</ref>\nUnrelated complete paragraph.\n{{unknown condition|value=7\n|only=static}}\n'
    c=clean_document(raw)
    assert '12 ± 2 × 10^(3) cm' in c['text']
    assert 'Unrelated complete paragraph.' in c['text']
    assert c['counts']['deferred']==1
    assert any('Measured at 20 C.' in r.get('content','') for b in c['blocks'] for r in b.get('references',[]))
    assert_alignment(raw,c)


def test_italic_heading_does_not_delete_article_at_repeated_footer_title():
    raw='Main menu\nView history\n# _Magnolia_\nFrom Wikipedia, the free encyclopedia\nMagnolia is a genus.\nOnly some species are evergreen.\nMagnolia\n* This page was last edited on Monday.\nPrivacy policy\n'
    c=clean_document(raw,title='Magnolia - Wikipedia')
    assert 'Magnolia is a genus.' in c['text']
    assert 'Only some species are evergreen.' in c['text']
    assert 'Privacy policy' not in c['text']
    assert_alignment(raw,c)


def test_section_budget_keeps_whole_conditions_and_reports_omitted_ranges():
    raw='# First\n'+('Long opening paragraph. '*30)+'\n# Second\nOnly when static; exceptions apply.\n# Third\nA distinct later observation.\n'
    c=clean_document(raw);ranges,omitted=select_passages(c,160)
    texts=[c['text'][a:b] for a,b in ranges]
    assert any('Only when static; exceptions apply.' in t for t in texts)
    assert any('A distinct later observation.' in t for t in texts)
    assert sum(b-a for a,b in ranges)<=160 and omitted
    for a,b in ranges:
        assert any(x.get('clean_start')==a for x in c['blocks'])
        assert any(x.get('clean_end')==b for x in c['blocks'])


def test_disambiguation_metadata_not_silently_lost():
    c=clean_document('{{about|the plant|the film|Magnolia (film)}}\nA plant genus.')
    assert 'the film' in c['text'] and 'Magnolia (film)' in c['text']


def test_native_section_boundaries_survive_serialization():
    sections=[{'title':'','text':'Leading body.'},{'title':'Later','text':'Only in vacuum.'}]
    raw='\n\n'.join((s['title']+'\n'+s['text']).strip() for s in sections)
    c=clean_document(raw,source_sections=sections)
    assert next(b for b in c['blocks'] if b['text']=='Only in vacuum.')['section']==['Later']
    assert_alignment(raw,c)


def test_navigation_box_stops_before_article_section_and_keeps_data_table():
    raw='# Body\n| Pressure | 12 |\nOnly in vacuum.\n| 展开\n* 查\n* 论\n* 编\nRelated pages |\n* Other article\n# Measurements\nMeasured at 20 C.\n'
    c=clean_document(raw)
    assert 'Other article' not in c['text']
    assert '| Pressure | 12 |' in c['text']
    assert 'Only in vacuum.' in c['text'] and 'Measured at 20 C.' in c['text']
    assert_alignment(raw,c)


def test_broken_markup_tails_are_deferred_without_losing_other_paragraphs():
    raw='A complete independent observation.\ndoi-access=free }}</ref>\nAnother observation.\n'
    c=clean_document(raw)
    assert 'doi-access' not in c['text']
    assert 'A complete independent observation.' in c['text'] and 'Another observation.' in c['text']
    assert c['counts']['deferred']==1
    assert_alignment(raw,c)


def test_reference_conditions_count_toward_selection_budget():
    from curation.v4.ops.passage_selection import reference_notes,note_chars
    c=clean_document('Only static: 12 cm.<ref>'+('condition '*30)+'</ref>\nIndependent fact.')
    ranges,_=select_passages(c,100)
    assert all('12 cm' not in c['text'][a:b] for a,b in ranges)
    assert any('Independent fact.' in c['text'][a:b] for a,b in ranges)


def test_baike_wrapper_removal_preserves_source_warning_and_reference():
    raw='[个人中心](https://baike.baidu.com/usercenter)\n网页新闻贴吧知道\n# 芦笙\n播报\n编辑\n需要更多来源。\nOnly under this condition.\n参考资料\nOriginal paper, page 7.\n词条统计\n订阅词条\n'
    c=clean_document(raw,title='芦笙_百度百科')
    assert '个人中心' not in c['text'] and '订阅词条' not in c['text']
    assert '需要更多来源。' in c['text'] and 'Only under this condition.' in c['text'] and 'Original paper, page 7.' in c['text']


def test_wuu_body_is_kept_without_requiring_english_language():
    raw='https://wuu.wikipedia.org/wiki/Test\n移至侧栏 囥起來\n出自维基百科，自由个百科全书\n花木兰，是文学作品中一位代父从军个女性人物。\n取自“https://wuu.wikipedia.org/w/index.php?oldid=1”\n隐私政策\n'
    c=clean_document(raw)
    assert '代父从军' in c['text'] and '移至侧栏' not in c['text'] and '隐私政策' not in c['text']
