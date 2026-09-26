"""Regression cases for evidence loss, provenance and notebook execution semantics."""
import asyncio
import json
import pytest
from preparation.operaters.documents import clean_document


def test_cleaning_preserves_short_conditions_tables_links_and_quality():
    raw='登录\n## 条件\n仅限静止时。\n| 部位 | 数值 |\n| A | 2 |\n[证据](https://example.org/ref)\n![断面](https://example.org/img.png)\n[citation needed]\n这里讨论登录协议的结构。\n'
    out=clean_document(raw)
    assert out['blocks'][0]['decision']=='exclude'
    for s in ['仅限静止时。','| A | 2 |','[citation needed]','这里讨论登录协议的结构。']:
        assert s in out['text']
    assert any(b['links'] for b in out['blocks'])
    assert any(b['images'] for b in out['blocks'])
    for b in out['blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if 'clean_start' in b:assert out['text'][b['clean_start']:b['clean_end']]==b['text']


def test_html_structure_excludes_navigation_but_keeps_table_and_figure():
    raw='<html><body><nav><a href="/login">Login</a></nav><h2>条件</h2><p>只有静止时成立。</p><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table><figure><img src="x.png" alt="断面"><figcaption>截面图</figcaption></figure><script>bad()</script></body></html>'
    out=clean_document(raw)
    assert 'Login' not in out['text'] and 'bad()' not in out['text']
    assert '只有静止时成立。' in out['text'] and '1\t2' in out['text']
    assert any(b['images'] and b['text']=='截面图' for b in out['blocks'])
    for b in out['blocks']:assert raw[b['raw_start']:b['raw_end']]==b['raw_text']


def test_wiki_semantic_templates_retained_layout_excluded_and_empty_flagged():
    out=clean_document('{{Short description|Everything in space and time}}\n{{pp-semi-indef}}\n{{Other uses}}\n== 条件 ==\n短定义\n')
    assert 'Everything in space and time' in out['text']
    assert any(t['name']=='other uses' for b in out['blocks'] for t in b.get('templates',[]))
    assert 'pp-semi-indef' not in out['text']
    assert '短定义' in out['text']
    assert 'unexpanded_wiki_template_retained' not in out['warnings']
    assert clean_document('登录\n注册\n')['status']=='needs_review'






def test_access_wall_not_mistaken_for_clean_body():
    out=clean_document('Fanpop is currently available only for logged in users\nUsername:\nPassword:\n')
    assert out['status']=='needs_review'
    assert 'access_restriction_or_challenge_marker' in out['warnings']


def test_bounded_shell_preserves_article_and_source_offsets():
    from preparation.operaters.documents import clean_document
    raw='用户登录\n按内容 按标题 按作者\n# A real article\n来源：Author\n'+('正文需要保留链接和条件；'*60)+'\n### 大家都在看\n* [other](https://example.org/1)\n* [other2](https://example.org/2)\n'
    c=clean_document(raw,title='A real article')
    assert '用户登录' not in c['text'] and 'other2' not in c['text']
    assert '来源：Author' in c['text'] and '正文需要保留链接和条件' in c['text']
    for b in c['blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if 'clean_start' in b:assert c['text'][b['clean_start']:b['clean_end']]==b['text']


def test_error_snippet_and_legitimate_mention():
    from preparation.operaters.documents import clean_document
    c=clean_document('# Access Denied\nYour access is temporarily blocked for possible abuse.')
    assert c['status']=='unavailable' and not c['text']
    c=clean_document('An instrument uses bamboo pipes and...')
    assert 'possible_truncated_snippet' in c['warnings']
    c=clean_document('This article explains what access denied means, with a full description.')
    assert c['text'] and c['status']!='unavailable'


def test_cookie_requires_closed_boundary():
    from preparation.operaters.documents import clean_document
    raw='Revisit consent button\nCustomise Consent Preferences\nCookies text\nReject All Save My Preferences Accept All\n# Article\nImportant article content.'
    c=clean_document(raw)
    assert 'Cookies text' not in c['text'] and 'Important article content.' in c['text']
    c=clean_document('Revisit consent button\nImportant article content.')
    assert 'Important article content.' in c['text']
