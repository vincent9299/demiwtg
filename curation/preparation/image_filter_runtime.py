"""Prompt configuration/checkpoint guards; Dataset orchestration stays in notebooks."""
import json
from pathlib import Path
from demiflow.standalone import local_data
from curation.preparation.records import run_records, prompt_store
from curation.preparation.ops.prompt_config import material_prompt_pack, prompt_execution_options, save_prompt_config
from curation.preparation.ops.image_filter import IMAGE_FILTER_POLICY


def image_prompt_data(run, config, *, review=False):
    if config['model'] != 'qwen3.8-27b' or config['image_review_model'] != 'gemma-4-31b-it':
        raise ValueError('This image selection policy was validated for Qwen3.8 + Gemma31 only')
    cfg = {**config, 'temperature': 0, 'max_output_tokens': config['image_filter_output_tokens']}
    if review:
        cfg.update(model=config['image_review_model'], base_url=config['image_review_base_url'], local_model_comparison=True)
    pack, text = material_prompt_pack(cfg)
    options = prompt_execution_options(run, cfg)
    # Share the durable request journal/budget across all formal pipeline stages.
    options['lance_journal'] = prompt_store(run, 'calls')
    save_prompt_config(Path(run) / ('image_review' if review else 'image_primary'), text, options)
    return local_data(prompt_packs={'knowledge.yaml': pack}, prompt_options=options,
                      max_prompt_requests=config['max_calls'])


def review_needed(requests, checkpoint, version):
    from demiflow.lance.checkpoint import read_checkpoint_record
    record = read_checkpoint_record(str(checkpoint))
    if record is not None:
        if record['fingerprint'] != version:
            raise ValueError('Image review checkpoint changed; use a new run')
        return False
    return next(requests.iter_rows(), None) is not None


def save_image_filter_policy(run, config):
    run_records(run).put('image_filter_policy', {
        'policy': IMAGE_FILTER_POLICY, 'primary_model': config['model'],
        'review_model': config['image_review_model'], 'batch_size': config.get('image_batch_size', 4),
        'identity_definitions': config['image_identity_definitions'], 'neutral_input': True})


def validate_material_reuse(parent, config):
    policy = run_records(parent).get('image_filter_policy')
    if policy is None:
        raise ValueError('Old selected materials bypass the new two-model image filter; set reuse_materials=None')
    if policy != {'policy': IMAGE_FILTER_POLICY, 'primary_model': config['model'],
                  'review_model': config['image_review_model'], 'batch_size': config.get('image_batch_size', 4),
                  'identity_definitions': config['image_identity_definitions'], 'neutral_input': True}:
        raise ValueError('Image selection policy/identity scope differs; cannot reuse selected materials')
    validate_text_selection_reuse(parent,config)
    validate_image_selection_reuse(parent,config)


def validate_image_selection_reuse(parent,config):
    """A new image prompt must not inherit decisions from the previous prompt."""
    import yaml
    saved=run_records(Path(parent)/'knowledge').get('prompt_config')
    if saved is None:
        raise ValueError('Parent image selection prompt snapshot is missing')
    prior=yaml.safe_load(saved['yaml'])['prompts']['select_images']
    _,text=material_prompt_pack(config)
    active=yaml.safe_load(text)['prompts']['select_images']
    if any(prior[k]!=active[k] for k in ['version','template','model','response_schema']):
        raise ValueError('Image selection prompt/model differs; reuse from before image filtering instead')


def validate_text_selection_reuse(parent,config):
    """Image policy alone cannot authorize reuse after changing text relevance."""
    import yaml
    saved=run_records(Path(parent)/'knowledge').get('prompt_config')
    if saved is None:raise ValueError('Parent text selection prompt snapshot is missing')
    prior=yaml.safe_load(saved['yaml'])['prompts']['select_blocks']
    _,current=material_prompt_pack(config)
    active=yaml.safe_load(current)['prompts']['select_blocks']
    if any(prior[k]!=active[k] for k in ['template','model','response_schema']):
        raise ValueError('Text selection prompt/model differs; reuse from before filtering instead')
