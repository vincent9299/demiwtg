import asyncio
from curation.preparation.ops.dataset_operators import CleanDocument
from curation.preparation.ops.filter_document_blocks import FilterDocumentBlocks


def test_filter_rebuilds_offsets_and_preserves_raw():
    raw='玻璃棒用于搅拌。\n\n隐私政策\n\n玻璃棒可以引流。'
    baseline=asyncio.run(CleanDocument()({'raw_text':raw,'read_status':'readable','read_error':None,'title':'玻璃棒'}))
    result=FilterDocumentBlocks()(baseline)
    assert result['raw_text']==raw
    assert '隐私政策' not in result['clean_text']
    assert '玻璃棒用于搅拌' in result['clean_text']
    for b in result['clean_blocks']:
        assert raw[b['raw_start']:b['raw_end']]==b['raw_text']
        if b['decision']=='keep' and b['text'].strip():
            assert result['clean_text'][b['clean_start']:b['clean_end']]==b['text']
    assert baseline['clean_version']!=result['clean_version']


def test_rejected_body_cannot_remain_eligible():
    baseline=asyncio.run(CleanDocument()({'raw_text':'Confirm you are human. Solve a puzzle.', 'read_status':'readable','read_error':None}))
    result=FilterDocumentBlocks()(baseline)
    assert not result['clean_text']
    assert result['knowledge_eligibility']['status']=='pending'
