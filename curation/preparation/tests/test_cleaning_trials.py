from curation.preparation.ops.cleaning import clean_document
from curation.preparation.ops.cleaning_trials import QualityBranches, repetition


def apply(text,title='材料说明',url='https://example.org/article'):
    c=clean_document(text,title=title)
    return QualityBranches()({'raw_text':text,'title':title,'url':url,'clean_text':c['text'],'clean_blocks':c['blocks'],'clean_warnings':c['warnings']})


def test_encyclopedia_item_route_preserves_list_and_parameters():
    r=apply('# 操作\n\n1、引流\n\n2、搅拌\n\n# 参数\n\n直径：6毫米\n\n长度：300毫米',url='https://example.org/item/123')
    assert '1、引流' in r['variants']['block_quality']
    assert '直径：6毫米' in r['variants']['block_quality']
    assert not r['filter_diagnostics']['transaction_page_signal']


def test_legal_disclaimer_is_not_product_information():
    r=apply('加入购物车\n\n立即购买\n\n本站商品信息均来自于厂商，其真实性由厂商负责。本站不提供任何保证，并不承担任何法律责任。',title='价格 品牌 商城')
    assert not r['variants']['block_quality']


def test_repetition_character_coverage_does_not_double_count():
    scores=repetition('alpha beta gamma '*30)
    # Top 2–4-gram counts may overlap; only the 5–10 coverage union must not.
    assert all(0 <= scores[f'ngram_{n}'] <= 1 for n in range(5, 11))


def test_short_definition_is_not_removed_for_english_word_count():
    r=apply('玻璃仪器，可用来搅拌加速溶质溶解，过滤时引流。')
    assert r['variants']['block_quality']
    assert not r['variants']['gopher_english_thresholds']
