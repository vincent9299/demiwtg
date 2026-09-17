from .ops.paragraph_pipeline import ApplyLocalIntegration, BuildLocalMergeGroups


def test_no_relationship_skips_merge():
    result=BuildLocalMergeGroups()({'concept':'x','items':[], 'source_rows':[]})
    assert result['requests']==[] and result['pending']==[]


def test_failed_integration_defers_only_affected_blocks():
    original={'batch_id':'a','topics':[{'title':'t','blocks':[
        {'block_id':'x','type':'text','status':'candidate'},
        {'block_id':'y','type':'text','status':'candidate'}]}]}
    bad={'batch_id':'merge','merge_validation_issues':['accounting'],
         'consumed_blocks':[{'batch_id':'a','block_id':'x'}]}
    out=ApplyLocalIntegration()({'concept':'c','source_rows':[original],'local_results':[bad]})
    blocks=out['rows'][0]['topics'][0]['blocks']
    assert [b['status'] for b in blocks]==['deferred','candidate']
    assert original['topics'][0]['blocks'][0]['status']=='candidate'
    assert len(out['local_failures'])==1


def test_successful_merge_replaces_only_consumed_blocks():
    original={'batch_id':'a','topics':[{'title':'t','blocks':[
        {'block_id':'x','type':'text'}, {'block_id':'y','type':'text'},
        {'block_id':'i','type':'image','related_block_ids':['x','y']}]}]}
    merged={'batch_id':'merge','consumed_blocks':[{'batch_id':'a','block_id':'x'}], 'topics':[]}
    out=ApplyLocalIntegration()({'concept':'c','source_rows':[original],'local_results':[merged]})
    assert [b['block_id'] for b in out['rows'][0]['topics'][0]['blocks']]==['y','i']
    assert out['rows'][0]['topics'][0]['blocks'][1]['related_block_ids']==['y']
    assert out['rows'][1]==merged
