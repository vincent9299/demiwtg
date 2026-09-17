from .ops.paragraphs import ApplyParagraphs,ApplyParagraphReview


def test_local_failures_do_not_defer_whole_topic_and_basis_is_unnecessary():
    row={'joint_prompt':{'passages':[{'source_id':'S','text':'白色花瓣。'}],'image_ids':['I']},'prompt_result':{'topics':[{'title':'外观','blocks':[
        {'block_id':'A','type':'text','text':'花瓣白色。','citations':[{'source_id':'S','quote':'白色花瓣。'}],'status':'candidate','reason':''},
        {'block_id':'B','type':'image','image_id':'I','caption':'图中花瓣白色','region':'中央','limitations':'身份待核定','related_block_ids':['A'],'status':'candidate','reason':''},
        {'block_id':'C','type':'text','text':'错误引用','citations':[{'source_id':'S','quote':'不存在'}],'status':'candidate','reason':''}]}],'coverage_note':''}}
    r=ApplyParagraphs()(row)
    assert [b['status'] for b in r['topics'][0]['blocks']]==['candidate','candidate','deferred']
    r['prompt_result']={'reviews':[{'block_id':'A','status':'supported','reason':'原文支持'},{'block_id':'B','status':'supported','reason':'仅限观察'}]}
    out=ApplyParagraphReview()(r)
    assert [b['status'] for b in out['topics'][0]['blocks']]==['candidate','candidate','deferred']
    r['prompt_result']={'reviews':[{'block_id':'B','status':'supported','reason':'一'},{'block_id':'B','status':'supported','reason':'二'}]}
    assert all(b['status']=='deferred' for b in ApplyParagraphReview()(r)['topics'][0]['blocks'])


def test_public_output_excludes_rejected_blocks_and_has_no_review_fields():
    from .ops.paragraphs import SelectRetainedParagraphs
    row={'run':'r','batch_id':'b','joint_prompt':{'concept':'C'},'topics':[{'title':'topic','blocks':[
        {'block_id':'T','type':'text','text':'remove','citations':[],'status':'candidate','review':{'reason':'machine pass'}},
        {'block_id':'D','type':'text','text':'deferred','citations':[],'status':'deferred'},
        {'block_id':'I','type':'image','image_id':'i','caption':'observation','limitations':'identity unknown','region':'all','related_block_ids':['T'],'status':'candidate'}]}]}
    result=SelectRetainedParagraphs([{'run':'r','batch_id':'b','block_id':'T','reason':'scope error'}])(row)
    blocks=result['content']['topics'][0]['blocks']
    assert len(blocks)==1 and blocks[0]['type']=='image' and blocks[0]['related_block_ids']==[]
    assert 'status' not in blocks[0] and 'review' not in blocks[0]
    assert [d['keep'] for d in result['decisions']['blocks']]==[False,False,True]


def test_retained_html_omits_failed_content_and_preserves_source_links(tmp_path):
    import json
    from .paragraph_results import export_results
    run=tmp_path/'run';run.mkdir()
    row={'batch_id':'b','joint_prompt':{'concept':'C','image_ids':['I']},'pixel_images':['data:image/png;base64,aA==']}
    (run/'requests.jsonl').write_text(json.dumps(row)+'\n')
    row['topics']=[{'title':'T','blocks':[
        {'block_id':'A','type':'text','text':'retained text','citations':[{'source_id':'S','quote':'q'}],'status':'candidate'},
        {'block_id':'B','type':'text','text':'must not appear','citations':[],'status':'deferred'},
        {'block_id':'C','type':'image','image_id':'I','caption':'visible shape','region':'all','limitations':'one object','related_block_ids':['A'],'status':'candidate'}]}]
    (run/'paragraphs.jsonl').write_text(json.dumps(row)+'\n')
    source=tmp_path/'sources.jsonl';source.write_text(json.dumps({'documents':[{'material_id':'M','record':{'title':'Source title','url':'https://example.org/source'}}],'images':[{'record':{}},{'image_id':'I','record':{'title':'Image title','landing_url':'https://example.org/image'}}],'audit':{'material_selection':{'passages':[{'source_id':'S','material_id':'M'}]}}})+'\n')
    out=tmp_path/'out';export_results([run],out,source)
    h=(out/'preview.html').read_text()
    assert 'must not appear' not in h and '审核状态' not in h and 'deferred' not in h
    assert 'retained text' in h and 'https://example.org/source' in h and h.count('<img ')==1
    assert json.loads((out/'decisions.json').read_text())['rows'][0]['blocks'][1]['keep'] is False


def test_visual_prose_has_real_image_reference_and_review_excludes_prior_decisions():
    row={'joint_prompt':{'concept':'C','image_ids':['I'],'passages':[], 'image_selection':[{'decision':'keep','reason':'trust me'}]},
         'prompt_result':{'topics':[{'title':'观察','blocks':[{'block_id':'T','type':'text','text':'可见一圈手指',
         'citations':[],'image_refs':[{'image_id':'I','region':'手部'}],'status':'candidate','reason':'visual'}]}]}}
    result=ApplyParagraphs()(row)
    assert result['topics'][0]['blocks'][0]['status']=='candidate'
    assert 'image_selection' not in result['verify_payload']
    assert 'status' not in result['verify_payload']['topics'][0]['blocks'][0]
    row['prompt_result']['topics'][0]['blocks'][0]['image_refs'][0]['image_id']='missing'
    assert ApplyParagraphs()(row)['topics'][0]['blocks'][0]['status']=='deferred'


def test_topic_gate_does_not_publish_orphan_image_under_stale_title():
    from .ops.paragraphs import SelectRetainedParagraphs
    row={'run':'r','batch_id':'b','topic_gate_required':True,'joint_prompt':{'concept':'C'},'topics':[{'title':'Cultivation',
        'topic_review':{'status':'supported'},'blocks':[{'block_id':'I','type':'image','image_id':'i','status':'candidate','caption':'flower','region':'all','limitations':'unknown','related_block_ids':[]}]}]}
    assert SelectRetainedParagraphs()(row)['content']['topics']==[]
