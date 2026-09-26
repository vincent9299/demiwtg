from preparation.operaters.documents import repair_piece
import hashlib


def test_remove_navigation_tail_only_after_complete_sentence():
    before='仪器用于引流。 详情>>用途 - 参数 - 注意事项'
    text,edits=repair_piece(before)
    assert text=='仪器用于引流。'
    assert before[edits[0]['start']:edits[0]['end']]==edits[0]['removed']
    assert repair_piece('详情>>在此处表示详细说明。')[0]=='详情>>在此处表示详细说明。'


def test_keep_dimensions_exceptions_and_nonreference_urls():
    text='长10至40厘米（3.9至15.7英寸），除HF外。2(https://example.org/a#cite_note-2) 网址https://example.org/3'
    cleaned,_=repair_piece(text)
    assert '3.9至15.7' in cleaned and '除HF外' in cleaned and '[2]' in cleaned
    assert 'https://example.org/3' in cleaned




def test_restoring_inline_links_does_not_restore_reference_section():
    from preparation.operaters.documents import RepairSourceBlocks
    text='A bibliography entry with multiple archive links. It has complete sentences but is not article prose.'
    block={'block_id':'b','text':text,'raw_text':text,'kind':'paragraph','section':['參考來源'],'decision':'defer','reason':'link_directory_requires_review','links':[]}
    row={'dedup_blocks':[block],'clean_blocks':[block],'filter_diagnostics':{'document_reason':''}}
    assert RepairSourceBlocks()(row)['final_text']==''
