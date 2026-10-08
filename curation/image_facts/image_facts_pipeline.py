"""固定图：审核正例快照 → 按 SHA 去重 → 独立多模型标注 → 逐字段比较。"""
import argparse
from contextlib import ExitStack
from functools import partial
import json
from pathlib import Path
import re
import time
import lance
from demiflow import data
from demiflow.execution.artifacts import run_lock, immutable
from demiflow.operator_llm import load_prompt_pack
from demiflow.operator_llm.client import required_environment
from demiflow.operator_llm.call_ref import journal_totals
from demiflow.operator_llm.http_options import validate_http_options
from demiflow.services import VLLMService, vllm_config
from project import resolve_root
from .operators import rows

SOURCE = Path(__file__).parent
MODULE = Path('demiwtg/curation/image_facts')


def config(*, run, source, models=('gpt61', 'luna', 'qwen4b'), reference_model='gpt61',
           prompt_config=None, batch_sizes=None, concurrency=None, prompt_options=None,
           local_service=None, max_images=64, max_source_rows=10,
           max_edge=1024, jpeg_quality=85, max_image_bytes=32*1024**2,
           max_image_pixels=64000000, max_encoded_batch_bytes=16*1024**2,
           max_requests=None, write_mode='overwrite'):
    """显式预算按模型分别计数；默认试验每模型 64 图，GPT 八图/请求、Qwen 单图。"""
    cfg = dict(locals())
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', run):
        raise ValueError('Invalid run')
    if (not isinstance(source, dict) or set(source) != {'uri', 'version'}
            or not isinstance(source['uri'], str) or type(source['version']) is not int or source['version'] < 1):
        raise ValueError('Source requires fixed uri and positive version')
    if not models or len(set(models)) != len(models) or set(models) - {'gpt61','luna','qwen4b'}:
        raise ValueError('Select unique configured models')
    if reference_model not in models or write_mode != 'overwrite':
        raise ValueError('Reference must be selected; write mode must be overwrite')
    cfg['models'] = list(models)
    for name, lower, upper in [('max_images',1,10000),('max_source_rows',1,10000),
            ('max_edge',256,1536),('jpeg_quality',40,95),('max_image_bytes',1,64*1024**2),
            ('max_image_pixels',1,64000000),('max_encoded_batch_bytes',1,32*1024**2)]:
        if type(cfg[name]) is not int or not lower <= cfg[name] <= upper:
            raise ValueError('Invalid resource limit: '+name)
    cfg['batch_sizes'] = dict(batch_sizes or {'gpt61':8,'luna':8,'qwen4b':1})
    cfg['concurrency'] = dict(concurrency or {'gpt61':2,'luna':2,'qwen4b':8})
    cfg['max_requests'] = dict(max_requests or {'gpt61':8,'luna':8,'qwen4b':64})
    for key in models:
        for name, upper in [('batch_sizes',8),('concurrency',16),('max_requests',10000)]:
            value = cfg[name].get(key)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError('Invalid '+name+' for '+key)
    cfg['prompt_config'] = str(Path(prompt_config or SOURCE/'prompts/tasks.yaml').resolve())
    pack = load_prompt_pack(cfg['prompt_config'])
    if set(models) - set(pack.prompt_definitions):
        raise ValueError('Missing prompt definition')
    cfg['prompt_options'] = prompt_options or {
        key: {'stream':True, 'timeout_s':900, 'gateway':'litellm',
              'request_options':{'max_completion_tokens':32768,'reasoning_effort':'xhigh'}}
        for key in ('gpt61','luna')}
    cfg['prompt_options'] = {**cfg['prompt_options']}
    cfg['prompt_options'].setdefault('qwen4b', {'stream':True, 'timeout_s':300,
        'request_options':{'max_tokens':8192,'temperature':0,
                           'chat_template_kwargs':{'enable_thinking':False}}})
    for key in models:
        if set(cfg['prompt_options'][key]) & {'sqlite_journal','journal_dir','offline_store'}:
            raise ValueError('The run owns its native journals')
        validate_http_options(cfg['prompt_options'][key])
    cfg['local_service'] = vllm_config(local_service or {
        'model_path':'models/Qwen3.5-4B','gpus':[0], 'gpu_memory_utilization':0.5,
        'max_model_len':16384,'max_num_seqs':8,'max_num_batched_tokens':16384,
        'limit_mm_per_prompt':{'image':1}, 'enforce_eager':True, 'startup_timeout_s':600})
    return cfg


def run_pipeline(options, *, execute_models=None, finalize_only=False, prepare_only=False):
    cfg = config(**options)
    selected = list(cfg['models'] if execute_models is None else execute_models)
    if len(selected) != len(set(selected)) or set(selected) - set(cfg['models']):
        raise ValueError('Selected execution models must belong to configured models')
    if prepare_only and (finalize_only or execute_models is not None):
        raise ValueError('Prepare cannot execute models or finalize')
    if finalize_only or prepare_only: selected = []
    root = resolve_root(); base = root/MODULE; run_dir = base/'runs'/cfg['run']
    source = {**cfg['source'], 'uri':str(root/cfg['source']['uri'])}
    if lance.dataset(**source).count_rows() > cfg['max_source_rows']:
        raise ValueError('Input concept rows exceed budget')
    paths = {name:base/'datasets'/f'{name}__{cfg["run"]}.lance'
             for name in ('scope','comparisons','summary')}
    paths['comparisons'] = base/'datasets'/f'comparisons_by_label__{cfg["run"]}.lance'
    pack = load_prompt_pack(cfg['prompt_config'])
    # 凭据配置错误在逐图执行之前失败，不制造整批失败结果。
    required_environment(tuple({name for key in selected
        for name in (pack.prompt_definitions[key].model.api_key_env, pack.prompt_definitions[key].model.base_url_env) if name}))
    # 不同模型使用独立运行锁与 journal，可由同一正式 CLI 同时提交。
    with ExitStack() as locks:
        names = selected if selected else cfg['models']
        for key in sorted(names):
            locks.enter_context(run_lock(root/'_demiflow/image_facts'/cfg['run']/('execute-'+key)))
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir/'config.json'
        frozen = {**cfg, 'prompt_pack_text':Path(cfg['prompt_config']).read_text()}
        if config_path.exists() and json.loads(config_path.read_text()) != frozen:
            raise ValueError('Run configuration changed; use a new run')
        if not config_path.exists():
            immutable(config_path, frozen)
        if paths['scope'].exists():
            version = lance.dataset(str(paths['scope'])).version
            outputs = {'scope':{'uri':str(paths['scope']), 'version':version}}
            images = data.read_lance(**outputs['scope']).materialize()
            count = images.count()
        else:
            images = (data.read_lance(**source, columns=['concept','concept_id','selection_rank','positive_images'],
                           batch_size=16, batch_readahead=1, fragment_readahead=1)
                .flat_map(rows.scope_rows).reduce_by_key('sha256', rows.merge_scope).materialize())
            count = images.count()
            if count > cfg['max_images']:
                raise ValueError('Unique image count exceeds budget')
            receipt = images.write_lance(str(paths['scope']),mode='overwrite',schema=rows.SCOPE,return_receipt=True)
            outputs = {'scope':{'uri':receipt.uri,'version':receipt.committed_version}}
        print(f'[image_facts] run={cfg["run"]} unique_images={count} models={cfg["models"]}',flush=True)
        if prepare_only:
            return {'complete':False, 'scope':outputs['scope'], 'image_count':count}
        model_summaries = []
        combined = None
        for key in selected:
            started = time.monotonic()
            model_name = pack.prompt_definitions[key].model.name
            # 标准 group_batches 限定每次送图数量；只发图片编号，不发概念/历史审核或其他模型答案。
            prepared = (images.map(lambda r:{**r,'group':'all'})
                .group_batches('group', max_rows=cfg['batch_sizes'][key], output='items')
                .map_async(partial(rows.prepare_batch,cfg=cfg,model_key=key,model=model_name),
                           execution='thread',concurrency=1,queue_depth=1))
            service = (VLLMService(cfg['local_service'], root=root, log_path=run_dir/'vllm.log')
                       if key == 'qwen4b' else None)
            options = {**cfg['prompt_options'][key], 'sqlite_journal':{
                'path':str(run_dir/f'{key}.sqlite'),'max_requests':cfg['max_requests'][key]}}
            print(f'[image_facts] start {key} batch={cfg["batch_sizes"][key]} concurrency={cfg["concurrency"][key]}',flush=True)
            batches = (prepared.map_prompt_async(key, config=pack,
                inputs={'payload':'payload','images':'images'}, output='result',call_output='call',error_output='error',
                when=lambda row:row['status']=='ready', options=options,
                max_requests=cfg['max_requests'][key],concurrency=cfg['concurrency'][key],queue_depth=cfg['concurrency'][key],
                service=service).map(partial(rows.finish_batch, annotation_schema=dict(
                    pack.prompt_definitions[key].response_schema['properties']['result']['properties']['annotations']['items']))).materialize())
            batch_path = base/'datasets'/f'batches_{key}__{cfg["run"]}.lance'
            saved = batches.select_columns(rows.BATCHES.names).write_lance(str(batch_path),mode='overwrite',schema=rows.BATCHES,return_receipt=True)
            outputs['batches_'+key] = {'uri':saved.uri,'version':saved.committed_version}
            labels = batches.flat_map(lambda row:row['labels'])
            saved = labels.write_lance(str(base/'datasets'/f'labels_{key}__{cfg["run"]}.lance'),mode='overwrite',schema=rows.LABELS,return_receipt=True)
            outputs['labels_'+key] = {'uri':saved.uri,'version':saved.committed_version}
            summary = batches.reduce_by_key('model_key', rows.aggregate_calls).take(1)
            summary = summary[0] if summary else {'model_key':key,'images':0,'ok':0,'calls':0}
            summary.update(wall_seconds=time.monotonic()-started, batch_size=cfg['batch_sizes'][key],concurrency=cfg['concurrency'][key])
            summary['wall_seconds_is_replay'] = bool(summary.get('calls') and summary.get('reused') == summary['calls'])
            # 请求级原生日志去重统计，避免同一压缩图的响应被多个 SHA 复用时重复计 tokens。
            totals = journal_totals(run_dir/f'{key}.sqlite')
            summary.update(requests=totals['requests'], usage_records=totals['usage_records'],
                           input_tokens=totals['input_tokens'], output_tokens=totals['output_tokens'])
            stage_path = base/'datasets'/f'stage_{key}__{cfg["run"]}.lance'
            if stage_path.exists():
                previous = data.read_lance(str(stage_path),version=lance.dataset(str(stage_path)).version).take(1)[0]
                prior_metrics = json.loads(previous['metrics_json'])
                summary['replay_wall_seconds'] = summary['wall_seconds']
                summary['wall_seconds'] = prior_metrics['wall_seconds']
                first_stage = data.read_lance(str(stage_path),version=1).take(1)[0]
                first_metrics = json.loads(first_stage['metrics_json'])
                summary['wall_seconds_is_replay'] = bool(first_metrics.get('calls') and
                    first_metrics.get('reused') == first_metrics['calls'])
            model_summaries.append(summary)
            print('[image_facts] committed '+rows.encoded(summary),flush=True)
            stage = {'model_key':key, 'image_count':count, 'metrics_json':rows.encoded(summary),
                     'outputs_json':rows.encoded({name:outputs[name] for name in ('scope','batches_'+key,'labels_'+key)})}
            data.from_items([stage]).write_lance(str(base/'datasets'/f'stage_{key}__{cfg["run"]}.lance'),
                                               mode='overwrite',schema=rows.STAGE)
        if execute_models is not None and not finalize_only:
            return {'complete':False, 'stage_models':selected, 'models':model_summaries,
                    'message':'模型阶段已提交；全部模型结束后通过同一入口汇总。'}
        # 汇总只读各模型已提交阶段的固定引用，不补模型调用。
        model_summaries = []
        for key in cfg['models']:
            stage_path = base/'datasets'/f'stage_{key}__{cfg["run"]}.lance'
            if not stage_path.exists():
                raise ValueError('Model stage not committed: '+key)
            stage_version = lance.dataset(str(stage_path)).version
            stage = data.read_lance(str(stage_path),version=stage_version).take(1)[0]
            if stage['image_count'] != count:
                raise ValueError('Model stage scope differs')
            outputs.update(json.loads(stage['outputs_json']))
            outputs['stage_'+key] = {'uri':str(stage_path),'version':stage_version}
            model_summaries.append(json.loads(stage['metrics_json']))
            saved_labels = data.read_lance(**outputs['labels_'+key])
            saved = saved_labels.map(rows.expand_label).write_lance(
                str(base/'datasets'/f'label_fields_{key}__{cfg["run"]}.lance'),
                mode='overwrite', schema=rows.LABEL_FIELDS, return_receipt=True)
            outputs['label_fields_'+key] = {'uri':saved.uri, 'version':saved.committed_version}
            combined = saved_labels if combined is None else combined.union(saved_labels)
        tags = rows.tag_definitions(pack.prompt_definitions[cfg['reference_model']].response_schema[
            'properties']['result']['properties']['annotations']['items'])
        comparisons = combined.reduce_by_key('sha256',rows.collect_models).flat_map(
            partial(rows.compare,reference_key=cfg['reference_model'],tags=tags))
        saved = comparisons.write_lance(str(paths['comparisons']),mode='overwrite',schema=rows.COMPARISONS,return_receipt=True)
        outputs['comparisons'] = {'uri':saved.uri,'version':saved.committed_version}
        summary = {'run':cfg['run'], 'complete':all(m['ok']==count for m in model_summaries),
            'image_count':count,'config_json':rows.encoded(cfg),'outputs_json':rows.encoded(outputs),'models_json':rows.encoded(model_summaries)}
        # 单行控制摘要；所有业务明细来自上面的 Dataset 图。
        saved = data.from_items([summary]).write_lance(str(paths['summary']),mode='overwrite',schema=rows.SUMMARY,return_receipt=True)
        print(f'[image_facts] summary committed version={saved.committed_version} complete={summary["complete"]}',flush=True)
        return {'summary':{'uri':saved.uri,'version':saved.committed_version},'complete':summary['complete'], 'models':model_summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--run')
    parser.add_argument('--model',action='append',choices=['gpt61','luna','qwen4b'])
    parser.add_argument('--finalize-only',action='store_true')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--write-mode',choices=['overwrite'],default='overwrite')
    args = parser.parse_args()
    options = json.loads(Path(args.config).read_text())
    if args.run: options['run'] = args.run
    options['write_mode'] = args.write_mode
    print(json.dumps(run_pipeline(config(**options),execute_models=args.model,finalize_only=args.finalize_only,prepare_only=args.prepare_only),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
