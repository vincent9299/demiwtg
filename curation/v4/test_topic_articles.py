from .ops.topic_articles import choose_images,BuildTopicArticle,TopicTextInputs


def test_selection_limits_duplicates_and_rejects_unrelated_diversity():
    candidates=[{'image_id':str(i)} for i in range(8)]
    vectors={str(i):[1.,0.] for i in range(7)};vectors['7']=[0.,1.]
    chosen,audit=choose_images(candidates,[{'embedding':[1.,0.]}],vectors)
    assert len(chosen)==1 and '7' not in chosen
    assert any(a['reason']=='near_duplicate' for a in audit)
    chosen,_=choose_images(candidates,[{'embedding':[1.,0.]}],vectors,duplicate_threshold=1.1)
    assert len(chosen)==5


def test_article_has_three_sections_and_sources_deduplicate_across_blocks():
    sources={'S1':{'title':'Page','url':'https://example.org/page#first'},'S2':{'title':'Page2','url':'https://example.org/page#second'}}
    row={'concept':'C','topic_id':'T','title':'Title','run':'r','batch_id':'b','blocks':[
        {'type':'text','text':'One','citations':[{'source_id':'S1'}]},
        {'type':'text','text':'Two','citations':[{'source_id':'S2'}]}]}
    out=BuildTopicArticle({}, {},sources,{ })(row)['article']
    assert set(out)=={'title','content','references'}
    assert out['content']['paragraphs']==['One','Two']
    assert len(out['references'])==1 and out['references'][0]['source_ids']==['S1','S2']


def test_embedding_input_uses_final_prose_and_has_encoder_identity():
    row={'concept':'C','items':[{'topic_id':'T','title':'Title','blocks':[{'type':'text','text':'Final prose'}]}]}
    out=TopicTextInputs()(row)
    assert out['case_id']=='C' and out['passages'][0]['text']=='Title\nFinal prose'


def test_publisher_preserves_model_selected_images_without_limit_or_rescoring():
    images=[{'type':'image','image_id':str(i),'caption':'View','region':'All','limitations':'Limited'} for i in range(7)]
    row={'concept':'C','topic_id':'T','title':'Title','run':'r','batch_id':'b','blocks':images}
    out=BuildTopicArticle({}, {},{},{})(row)
    assert len(out['article']['content']['images'])==7
    assert all(x['selected'] for x in out['audit']['image_selection'])
