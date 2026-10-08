"""中性图片描述的唯一 prompt pack；没有概念评分节点。"""
from pathlib import Path
import yaml
from demiflow.operator_llm.parser import parse_prompt_pack

TASKS = Path(__file__).with_name('tasks.yaml')


def annotation_prompt_pack(config):
    spec = yaml.safe_load(TASKS.read_text())
    spec['prompts']['describe_image']['model'].update(
        name=config['model'], base_url=config['base_url'], api_key_env=config['api_key_env'])
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    return parse_prompt_pack(text), text
