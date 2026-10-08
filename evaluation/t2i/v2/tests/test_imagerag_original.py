"""固定官方源码的离线请求捕获；不联网，不运行官方生成/检索模型。"""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from demiflow.operator_llm.template import compile_template, render_template
from evaluation.t2i.v2.operators import imagerag


BASE = Path(__file__).parents[1]
UPSTREAM = BASE / 'prompts/imagerag_upstream'
SPEC = yaml.safe_load((BASE / 'prompts/imagerag_original.yaml').read_text())


def render(name, **values):
    parts = render_template(compile_template(SPEC['templates'][name]), values)
    return ''.join(part.text for part in parts)


def official_calls(tmp_path, responses, prompt='A sheep {{ literal }} "oil"'):
    # 固定源码只依赖 base64；假 client 的 create 是唯一调用出口。
    namespace = {}
    exec(compile((UPSTREAM / 'utils.py.txt').read_text(), 'upstream-utils', 'exec'), namespace)
    captured = []
    replies = iter(responses)

    def create(**request):
        captured.append(copy.deepcopy(request))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))])

    path = tmp_path / 'initial.png'
    path.write_bytes(b'original-image-bytes')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = namespace['retrieval_caption_generation'](prompt, [str(path)], client)
    return result, captured


def test_upstream_snapshots_are_fixed():
    expected = {
        'utils.py.txt': '42127923a96276330d8355fad1388ee6706b7331a9da5d59156a83aa01900595',
        'imageRAG_OmniGen.py.txt': '1763728bf1b95ae10c56a30b39fce1c0aa513c8d9e7a0a13f8bd352b638acbe4',
        'retrieval.py.txt': 'c57080b2d39c85a9a869798975677d0035f8e79d32c7135a9b73a4afbb0d211e',
    }
    for name, digest in expected.items():
        assert hashlib.sha256((UPSTREAM / name).read_bytes()).hexdigest() == digest


def test_three_prompts_match_official_calls_byte_for_byte(tmp_path):
    prompt = 'A sheep {{ literal }} "oil"'
    result, calls = official_calls(tmp_path, ['no', 'oil painting style\na sheep', 'Oil painting.\nA sheep.'], prompt)
    assert result == 'Oil painting.\nA sheep.'
    for request, stage, values in zip(calls, ('decision', 'concepts', 'captions'),
                                    ({'prompt': prompt}, {}, {'k_captions_per_concept': '1'})):
        actual = request['messages'][-1]['content'][0]['text']
        assert actual.encode() == render(stage, **values).encode()
        assert set(request) == {'model', 'messages', 'response_format', 'temperature'}
        assert request['model'] == 'gpt-4o'
        assert request['temperature'] == 0
        assert request['response_format'] == {'type': 'text'}
    assert [[m['role'] for m in c['messages']] for c in calls] == [
        ['user'], ['user', 'assistant', 'user'], ['user', 'assistant', 'user', 'assistant', 'user']]
    assert [len(c['messages'][0]['content']) for c in calls] == [2, 2, 3]
    assert calls[2]['messages'][0]['content'][1:] == [calls[0]['messages'][0]['content'][1]] * 2
    assert calls[2]['messages'][1]['content'][0]['text'] == 'no'
    assert calls[2]['messages'][3]['content'][0]['text'] == 'oil painting style\na sheep'


@pytest.mark.parametrize('response', ['yes', 'YES.', 'Yesterday'])
def test_official_yes_substring_keeps_initial(tmp_path, response):
    result, calls = official_calls(tmp_path, [response])
    assert result is True
    assert len(calls) == 1


def test_official_refusal_retries_and_fallback(tmp_path):
    result, calls = official_calls(tmp_path, ['no', 'unable', "can't", 'unable'], 'original prompt')
    assert result == 'original prompt'
    assert len(calls) == 4
    assert [len(c['messages'][0]['content']) for c in calls] == [2, 2, 3, 4]
    assert all(c['temperature'] == 0 for c in calls)


def test_official_refusal_matching_is_case_sensitive(tmp_path):
    result, calls = official_calls(tmp_path, ['no', 'Unable', 'caption'])
    assert result == 'caption'
    assert len(calls) == 3


def test_generation_templates_match_official_expressions():
    tree = ast.parse((UPSTREAM / 'imageRAG_OmniGen.py.txt').read_text())
    expressions = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
    for prefix, stage, values, variables in [
        ('According to these images of ', 'generation', {'examples': 'a sheep: IMAGE', 'prompt': '题面 {{ x }}'},
         {'examples': 'a sheep: IMAGE', 'args': SimpleNamespace(prompt='题面 {{ x }}')}),
        (None, 'example', {'caption': 'a sheep', 'image_index': '1'}, {'captions': ['a sheep'], 'i': 0, 'j': 0}),
    ]:
        node = next(n for n in expressions if (prefix and isinstance(n.values[0], ast.Constant)
                    and n.values[0].value.startswith(prefix)) or
                    (prefix is None and any(isinstance(v, ast.Constant) and '<img>' in str(v.value) for v in n.values)))
        actual = eval(compile(ast.Expression(node), 'upstream-expression', 'eval'), {'__builtins__': {}}, variables)
        assert render(stage, **values).encode() == actual.encode()


def test_strict_preset_uses_messages_and_cannot_fall_back_to_json():
    preset = json.loads((BASE / 'configs/qwen21_imagerag_ab_20261005.json').read_text())
    cfg = preset['answers'][0]['imagerag']
    normalized = imagerag.configuration(cfg, max_images=3)
    assert normalized['original_prompt_spec'] == SPEC
    assert 'max_concepts' not in normalized
    assert normalized['max_queries'] == 32
    for p in normalized['prompt_pack']['prompts'].values():
        assert p['input_mode'] == 'messages' and p['response_format'] == 'text'
        assert 'template' not in p and p['schema_retries'] == 0
    legacy = yaml.safe_load((BASE / 'prompts/imagerag.yaml').read_text())
    with pytest.raises(ValueError, match='messages input'):
        imagerag.configuration({**cfg, 'prompt_pack': legacy}, max_images=3)
