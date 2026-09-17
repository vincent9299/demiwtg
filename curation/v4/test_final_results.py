from .final_results import image_selection


def test_reviewed_is_not_supporting_and_only_retained_fact_support_counts():
    images=[{'image_id':f'I{i}','material_id':f'M{i}'} for i in range(4)]
    pairs=[{'image_id':'I0','fact_id':'F1','status':'none'},
           {'image_id':'I1','fact_id':'F1','status':'partial','region':'mouthpiece','limitations':'not whole structure'},
           {'image_id':'I2','fact_id':'DEFERRED','status':'full'},
           {'image_id':'I3','fact_id':'F1','status':'full'}]
    knowledge={'facts':[{'fact_id':'F1'}],'image_evidence':{'status':'machine_reviewed','result':{
        'images':[{'image_id':f'I{i}'} for i in range(3)],'support':pairs}}}
    result=image_selection(images,knowledge)
    assert len(result['original_material_ids'])==4
    assert len(result['reviewed_image_ids'])==3
    assert result['supporting_images']==[{'image_id':'I1','material_id':'M1',
        'support':[pairs[1]],'review_status':'machine_candidate'}]
    knowledge['image_evidence']['status']='not_run'
    assert image_selection(images,knowledge)['supporting_images']==[]
