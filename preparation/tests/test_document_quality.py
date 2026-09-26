from preparation.operaters.documents import clean_document
from preparation.operaters.documents import FilterSourceQuality


def apply(text,title='材料说明',url='https://example.org/article'):
    c=clean_document(text,title=title)
    return FilterSourceQuality()({'raw_text':text,'title':title,'url':url,'clean_text':c['text'],'clean_blocks':c['blocks'],'clean_warnings':c['warnings']})


def test_encyclopedia_item_route_preserves_list_and_parameters():
    r=apply('# 操作\n\n1、引流\n\n2、搅拌\n\n# 参数\n\n直径：6毫米\n\n长度：300毫米',url='https://example.org/item/123')
    assert '1、引流' in '\n'.join(b['text'] for b in r['quality_blocks'] if b['decision']=='keep')
    assert '直径：6毫米' in '\n'.join(b['text'] for b in r['quality_blocks'] if b['decision']=='keep')
    assert not r['filter_diagnostics']['transaction_page_signal']


def test_legal_disclaimer_is_not_product_information():
    r=apply('加入购物车\n\n立即购买\n\n本站商品信息均来自于厂商，其真实性由厂商负责。本站不提供任何保证，并不承担任何法律责任。',title='价格 品牌 商城')
    assert not '\n'.join(b['text'] for b in r['quality_blocks'] if b['decision']=='keep')




def test_short_definition_is_not_removed_for_english_word_count():
    r=apply('玻璃仪器，可用来搅拌加速溶质溶解，过滤时引流。')
    assert '\n'.join(b['text'] for b in r['quality_blocks'] if b['decision']=='keep')
