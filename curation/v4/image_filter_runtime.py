"""Prompt configuration/checkpoint guards; Dataset orchestration stays in notebooks."""
import json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable
from .ops.prompt_config import knowledge_prompt_pack, prompt_execution_options, save_prompt_config
from .ops.image_filter import IMAGE_FILTER_POLICY


def image_prompt_data(run, config, *, review=False):
    if config['model'] != 'qwen3.8-27b' or config['image_review_model'] != 'gemma-4-31b-it':
        raise ValueError('This image selection policy was validated for Qwen3.8 + Gemma31 only')
    cfg = {**config, 'temperature': 0, 'max_output_tokens': config['image_filter_output_tokens']}
    if review:
        cfg.update(model=config['image_review_model'], base_url=config['image_review_base_url'], local_model_comparison=True)
    pack, text = knowledge_prompt_pack(cfg)
    options = prompt_execution_options(run, cfg)
    # Share the durable request journal/budget across all formal pipeline stages.
    options['journal_dir'] = str(Path(run) / 'knowledge/calls')
    save_prompt_config(Path(run) / ('image_review' if review else 'image_primary'), text, options)
    return local_data(prompt_packs={'knowledge.yaml': pack}, prompt_options=options,
                      max_prompt_requests=config['max_calls'])


def review_needed(requests, checkpoint, version):
    checkpoint = Path(checkpoint)
    if checkpoint.exists():
        meta = checkpoint.with_suffix(checkpoint.suffix + '.meta.json')
        if not meta.exists() or json.loads(meta.read_text()).get('version') != version:
            raise ValueError('Image review checkpoint changed/incomplete; use a new run')
        return False
    return next(requests.iter_rows(), None) is not None


def save_image_filter_policy(run, config):
    immutable(Path(run) / 'image_filter_policy.json', {
        'policy': IMAGE_FILTER_POLICY, 'primary_model': config['model'],
        'review_model': config['image_review_model'], 'batch_size': config.get('image_batch_size', 4),
        'identity_definitions': config['image_identity_definitions'], 'neutral_input': True})


def validate_material_reuse(parent, config):
    path = Path(parent) / 'image_filter_policy.json'
    if not path.exists():
        raise ValueError('Old selected materials bypass the new two-model image filter; set reuse_materials=None')
    policy = json.loads(path.read_text())
    if policy != {'policy': IMAGE_FILTER_POLICY, 'primary_model': config['model'],
                  'review_model': config['image_review_model'], 'batch_size': config.get('image_batch_size', 4),
                  'identity_definitions': config['image_identity_definitions'], 'neutral_input': True}:
        raise ValueError('Image selection policy/identity scope differs; cannot reuse selected materials')
