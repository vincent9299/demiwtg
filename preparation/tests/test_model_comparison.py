import pytest
from preparation.prompts import validate_review_comparison_endpoint, prompt_execution_options
from preparation.operaters.runfiles import DEFAULT


def test_local_comparison_is_opt_in(tmp_path):
    config={**DEFAULT,'model':'gemma-4-31b-it','base_url':'http://127.0.0.1:8001/v1'}
    with pytest.raises(ValueError):prompt_execution_options(tmp_path/'preparation/runs/run',config)
    options=prompt_execution_options(tmp_path/'preparation/runs/run',{**config,'local_model_comparison':True,'temperature':0})
    assert options['verify_model'] and options['trust_env'] is False
    assert options['request_options']['temperature']==0


def test_thinking_template_options_follow_the_actual_model(tmp_path):
    config={**DEFAULT,'local_model_comparison':True,'enable_thinking':True,'reasoning_effort':'xhigh'}
    gemma=prompt_execution_options(tmp_path/'preparation/runs/run',{**config,'model':'gemma-4-31b-it','base_url':'http://127.0.0.1:8001/v1'})
    qwen=prompt_execution_options(tmp_path/'preparation/runs/run',{**config,'model':'qwen3.8-27b','base_url':'http://127.0.0.1:8000/v1'})
    assert gemma['request_options']['chat_template_kwargs']=={'enable_thinking':True}
    assert qwen['request_options']['chat_template_kwargs']=={'enable_thinking':True,'reasoning_effort':'xhigh'}


@pytest.mark.parametrize('url,model',[
 ('http://127.0.0.1:4001/v1','gemma-4-31b-it'),
 ('https://example.com/v1','gemma-4-31b-it'),
 ('http://127.0.0.1:8001/v1','unlisted-model'),
 ('http://user@localhost:8001/v1','gemma-4-31b-it')])
def test_comparison_stays_with_named_local_models(url,model):
    with pytest.raises(ValueError):validate_review_comparison_endpoint(url,model)
