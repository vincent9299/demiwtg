"""独立图片中性标注：固定图片来源 → 描述证据 → 显式公共两列合并。

只读图片 URI/SHA；不读取文章、概念审定或历史 release，不做匹配或用途判断。
CLI / notebook 共用 config → run_pipeline，模型调用和写表均在主线。
"""
import argparse
import json
import os
from pathlib import Path

import lance
import pyarrow as pa
from demiflow import data
from demiflow.execution.artifacts import run_lock
from demiflow.lance.transaction import registered_table_edit
from project import resolve_root
from preparation.images.annotation.operaters.images import (
    AnnotationProgress, PrepareImagePixels, attach_reuse, finalize_image_result,
    image_input_row, reuse_candidate, unique_image,
)
from preparation.images.annotation.operaters.merge import image_patch_row, merge_public_row
from preparation.images.annotation.operaters.schema import (
    IMAGE_INPUTS, IMAGE_RESULTS, PATCH_ROW, SUMMARY, semantic_config_id,
)
from preparation.images.annotation.prompts import annotation_prompt_pack

MODULE_DIR = Path('demiwtg/preparation/images/annotation')
OWNED_COLUMNS = ['descriptions', 'image_scores']


def _fixed_source(name, value):
    if (not isinstance(value, dict) or not isinstance(value.get('uri'), str)
            or not value['uri'].strip() or type(value.get('version')) is not int
            or value['version'] < 1):
        raise ValueError(name + ' requires a fixed {uri, version}')
    return {'uri': value['uri'], 'version': value['version']}


def config(*, run, image_source, image_shas=None, scope_source=None, max_images=None,
           skip_annotated_images=False, through='annotate', model_mode='online',
           replay_journal=None, publish_policy='complete_only', target_uri='datasets/images.lance',
           model='qwen3.8-27b', model_revision=None, base_url='http://127.0.0.1:8000/v1',
           api_key_env='IMAGE_ANNOTATION_MODEL_KEY', temperature=0, max_output_tokens=4096,
           timeout_s=600, enable_thinking=False, max_edge=1536, jpeg_quality=90,
           prepare_concurrency=8, prepare_queue_depth=8, image_concurrency=64,
           image_queue_depth=8, image_max_calls=None, image_service=None, progress_every=50):
    """配置不读表、不加载服务。默认只提交本轮图片证据，不更新公共表。

    image_source 固定图片表，必需 sha256/image_uri；scope_source 可选 SHA 名单固定表，
    与 image_shas 互斥；缺失名单图片保留失败。max_images 按 SHA 排序限制候选范围。
    replay 只读指定 journal 且零新请求；online 使用本 run journal。complete_only
    禁止未完成批次写公共表；valid_rows 显式允许仅交付有效行，整轮仍未完成。
    model_revision 由调用者在同地址换部署时更新；None 明确表示未记录部署标识，
    可用于按原始请求身份回放未带 revision 的已保存响应，不能补写一个假版本。
    """
    from demiflow.services import vllm_config
    if not isinstance(run, str) or not run or Path(run).name != run or Path(run).suffix or run in {'.', '..'}:
        raise ValueError('run must be a suffix-free directory name')
    image_source = _fixed_source('image_source', image_source)
    scope_source = _fixed_source('scope_source', scope_source) if scope_source is not None else None
    if image_shas is not None:
        if (not isinstance(image_shas, (list, tuple)) or any(
            not isinstance(s, str) or len(s) != 64 or any(c not in '0123456789abcdef' for c in s)
            for s in image_shas)):
            raise ValueError('image_shas must be a list of lowercase SHA256 strings')
        image_shas = sorted(set(image_shas))
    if scope_source is not None and image_shas is not None:
        raise ValueError('Use scope_source or image_shas, not both')
    if through not in {'prepare', 'annotate', 'export'}:
        raise ValueError('through must be prepare, annotate or export')
    if model_mode not in {'online', 'replay'}:
        raise ValueError('model_mode must be online or replay')
    if (model_mode == 'replay') != bool(replay_journal):
        raise ValueError('Only replay mode requires an explicit replay_journal')
    if model_mode == 'replay' and image_service is not None:
        raise ValueError('Replay cannot configure a model service')
    if publish_policy not in {'complete_only', 'valid_rows'}:
        raise ValueError('publish_policy must be complete_only or valid_rows')
    for name, value in (('prepare_concurrency', prepare_concurrency), ('prepare_queue_depth', prepare_queue_depth),
                        ('image_concurrency', image_concurrency), ('image_queue_depth', image_queue_depth),
                        ('max_output_tokens', max_output_tokens), ('max_edge', max_edge),
                        ('jpeg_quality', jpeg_quality), ('progress_every', progress_every)):
        if type(value) is not int or value < 1:
            raise ValueError(name + ' must be a positive integer')
    if jpeg_quality > 100:
        raise ValueError('jpeg_quality must be <= 100')
    if max_images is not None and (type(max_images) is not int or max_images < 0):
        raise ValueError('max_images must be a nonnegative integer or None')
    if image_max_calls is not None and (type(image_max_calls) is not int or image_max_calls < 0):
        raise ValueError('image_max_calls must be a nonnegative integer or None')
    if type(temperature) not in (float, int) or temperature < 0:
        raise ValueError('temperature must be nonnegative')
    if type(timeout_s) not in (float, int) or timeout_s <= 0:
        raise ValueError('timeout_s must be positive')
    if type(skip_annotated_images) is not bool or type(enable_thinking) is not bool:
        raise ValueError('skip_annotated_images and enable_thinking must be booleans')
    if model_revision is not None and (not isinstance(model_revision, str) or not model_revision.strip()):
        raise ValueError('model_revision must be a nonempty string or None')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('model must be nonempty')
    image_service = vllm_config(image_service)
    return dict(run=run, image_source=image_source, image_shas=image_shas, scope_source=scope_source,
                max_images=max_images, skip_annotated_images=skip_annotated_images,
                through=through, model_mode=model_mode, replay_journal=str(replay_journal) if replay_journal else None,
                publish_policy=publish_policy, target_uri=str(target_uri), model=model,
                model_revision=model_revision, base_url=base_url, api_key_env=api_key_env,
                temperature=temperature, max_output_tokens=max_output_tokens, timeout_s=timeout_s,
                enable_thinking=enable_thinking, max_edge=max_edge, jpeg_quality=jpeg_quality,
                prepare_concurrency=prepare_concurrency, prepare_queue_depth=prepare_queue_depth,
                image_concurrency=image_concurrency, image_queue_depth=image_queue_depth,
                image_max_calls=image_max_calls, image_service=image_service, progress_every=progress_every)


def _endpoint_guard(cfg):
    from urllib.parse import urlparse
    url = urlparse(cfg['base_url'])
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or url.path.rstrip('/') != '/v1' or url.username or url.password or url.query or url.fragment):
        raise ValueError('annotation endpoints must be a loopback /v1 model service')
    os.environ.setdefault(cfg['api_key_env'], 'local-no-auth')


def _execution_options(cfg, journal_path):
    return {
        'sqlite_journal': {'path': str(journal_path), 'read_only': cfg['model_mode'] == 'replay'},
        'model_revision': cfg['model_revision'], 'timeout_s': cfg['timeout_s'],
        'trust_env': False, 'verify_model': True, 'require_finish_reason_stop': True,
        'max_keepalive_connections': 0,
        'request_options': {'max_tokens': cfg['max_output_tokens'], 'temperature': cfg['temperature'],
                            'response_format': {'type': 'json_object'},
                            'chat_template_kwargs': {'enable_thinking': cfg['enable_thinking']}},
    }


def _stage_path(stage, run):
    return resolve_root() / MODULE_DIR / 'datasets' / f'{stage}__{run}.lance'


def _stage_ref(path):
    """只返回成功提交表的固定引用；不负责执行或写入阶段。"""
    table = lance.dataset(str(path))
    print(f'[Image annotation] committed {path.name}@{table.version}; rows={table.count_rows()}', flush=True)
    return {'uri': str(path.relative_to(resolve_root())), 'version': table.version}


def _count_status(status_rows):
    # 按状态聚合后只取回小摘要，不将生产明细搬到 driver。
    counts = (status_rows.map(lambda row: {'status': row['status'], 'count': 1})
              .reduce_by_key('status', lambda acc, row: {
                  'status': row['status'], 'count': (acc['count'] if acc else 0) + row['count']})
              .take_all())
    return {row['status']: row['count'] for row in counts}


def run_pipeline(cfg):
    """一 SHA 一次描述；阶段完成与公共提交分开报告，失败图片保留在分母。"""
    cfg = config(**cfg)
    root, run = resolve_root(), cfg['run']
    paths = {name: _stage_path(name, run) for name in ('image_inputs', 'image_results', 'patch', 'summary')}
    source_path = (root / cfg['image_source']['uri']).resolve()
    target_path = (root / cfg['target_uri']).resolve()
    scope_path = (root / cfg['scope_source']['uri']).resolve() if cfg['scope_source'] else None
    if any(path.resolve() in {source_path, target_path, scope_path} for path in paths.values()):
        raise ValueError('Stage paths must differ from source and target tables')
    target_relative = str(target_path.relative_to(root))
    pack, _ = annotation_prompt_pack(cfg)
    config_id = semantic_config_id(pack, cfg)
    run_dir = root / MODULE_DIR / 'runs' / run
    source_binding = cfg['image_source']
    details = {'config': cfg, 'image_config_id': config_id, 'phase': 'started',
               'image_inputs': None, 'image_results': None, 'patch': None}
    summary = dict(run=run, through=cfg['through'], model_mode=cfg['model_mode'], input_count=0,
                   required_count=0, skipped_count=0, done_count=0, reused_count=0, error_count=0,
                   stage_complete=False, complete=False, committed=False, target_version=None)
    print(f'[Image annotation] run={run}; through={cfg["through"]}; mode={cfg["model_mode"]}; '
          f'source={source_binding}; model={cfg["model"]}; concurrency={cfg["image_concurrency"]}', flush=True)
    with run_lock(root / '_demiflow' / 'preparation_image_annotation' / run):
        # 先使旧成功摘要失效；中断后查看会显示 started，而不会冒充本轮完成。
        data.from_items([{**summary, 'details_json': json.dumps(details, ensure_ascii=False)}]).write_lance(
            str(paths['summary']), mode='overwrite', schema=SUMMARY)
        source = lance.dataset(str(source_path), version=source_binding['version'])
        if not {'sha256', 'image_uri'} <= set(source.schema.names):
            raise ValueError('image_source requires sha256 and image_uri columns')
        columns = ['sha256', 'image_uri'] + [n for n in OWNED_COLUMNS if n in source.schema.names]
        images = data.read_lance(str(source_path), version=source_binding['version'], columns=columns)
        # 显式 SHA 名单左连接目录，缺失图片不从本轮消失。
        if scope_path is not None:
            scope = data.read_lance(str(scope_path), version=cfg['scope_source']['version'], columns=['sha256'])
            images = scope.reduce_by_key('sha256', unique_image).join(images, on='sha256', how='left')
        elif cfg['image_shas'] is not None:
            scope = data.from_items([{'sha256': sha} for sha in cfg['image_shas']])
            images = scope.join(images, on='sha256', how='left')
        images = images.reduce_by_key('sha256', unique_image)
        if cfg['max_images'] is not None:
            images = images.sort('sha256').limit(cfg['max_images'])
        inputs = images.map(image_input_row, fn_kwargs={'skip_annotated': cfg['skip_annotated_images']})
        inputs.write_lance(str(paths['image_inputs']), mode='overwrite', schema=IMAGE_INPUTS)
        details['image_inputs'] = _stage_ref(paths['image_inputs'])
        input_counts = _count_status(data.read_lance(
            str(paths['image_inputs']), version=details['image_inputs']['version'], columns=['status']))
        summary.update(input_count=sum(input_counts.values()), required_count=input_counts.get('ready', 0),
                       skipped_count=input_counts.get('skipped_annotated', 0))
        details['input_status'] = input_counts
        details['phase'] = 'prepared' if cfg['through'] == 'prepare' else 'annotating'
        if cfg['through'] == 'prepare':
            summary.update(stage_complete=True, complete=True)
        else:
            _endpoint_guard(cfg)
            journal_path = (root / cfg['replay_journal']).resolve() if cfg['replay_journal'] else run_dir / 'model_calls.sqlite'
            if cfg['model_mode'] == 'replay' and not journal_path.is_file():
                raise FileNotFoundError('Replay journal does not exist: ' + str(journal_path))
            options = _execution_options(cfg, journal_path)
            service = None
            if cfg['image_service']:
                from demiflow.services import VLLMService
                service = VLLMService(cfg['image_service'], root=root, log_path=run_dir / 'image_vllm.log')
            ready = data.read_lance(str(paths['image_inputs']), version=details['image_inputs']['version'], filter="status = 'ready'")
            # 公共证据按当前输入 SHA 关联，候选选择逐行校验，不建全库 Python 索引。
            reusable = data.read_lance(str(source_path), version=source_binding['version'],
                                      columns=['sha256'] + [n for n in OWNED_COLUMNS if n in source.schema.names])
            reusable = reusable.join(ready.select_columns(['sha256']), on='sha256', how='semi').map(
                reuse_candidate, fn_kwargs={'config_id': config_id})
            results = (ready.join(reusable, on='sha256', how='left').map(attach_reuse)
                .map_async(PrepareImagePixels(root, max_edge=cfg['max_edge'], jpeg_quality=cfg['jpeg_quality']),
                           execution='thread', concurrency=cfg['prepare_concurrency'],
                           queue_depth=cfg['prepare_queue_depth'], label='prepare_image_pixels')
                .map_prompt_async('describe_image', config=pack, options=options,
                    max_requests=0 if cfg['model_mode'] == 'replay' else cfg['image_max_calls'],
                    inputs={'images': 'prompt_images'}, output='prompt_result', call_output='prompt_call',
                    error_output='prompt_error', when=lambda row: row['status'] == 'ready',
                    concurrency=cfg['image_concurrency'], queue_depth=cfg['image_queue_depth'], service=service)
                .map(finalize_image_result, fn_kwargs=dict(run_id=run, image_config_id=config_id, model=cfg['model'],
                    source_binding=source_binding, max_edge=cfg['max_edge'], jpeg_quality=cfg['jpeg_quality']))
                .map(AnnotationProgress('describe_image', every=cfg['progress_every']))
                .map(lambda row: {name: row.get(name) for name in IMAGE_RESULTS.names})
                .materialize())
            results.write_lance(str(paths['image_results']), mode='overwrite', schema=IMAGE_RESULTS)
            details['image_results'] = _stage_ref(paths['image_results'])
            actual = data.read_lance(str(paths['image_results']), version=details['image_results']['version'], columns=['sha256'])
            actual.reduce_by_key('sha256', unique_image).count()
            expected = ready.select_columns(['sha256'])
            if (expected.join(actual, on='sha256', how='anti').limit(1).count()
                    or actual.join(expected, on='sha256', how='anti').limit(1).count()):
                raise ValueError('Image result keys differ from the required input keys')
            counts = _count_status(data.read_lance(
                str(paths['image_results']), version=details['image_results']['version'], columns=['status']))
            errors = sum(n for key, n in counts.items() if key not in {'done', 'reused'})
            summary.update(done_count=counts.get('done', 0), reused_count=counts.get('reused', 0),
                           error_count=errors, stage_complete=errors == 0)
            details.update(image_status=counts, phase='annotated')
            if cfg['through'] == 'export' and (summary['stage_complete'] or cfg['publish_policy'] == 'valid_rows'):
                # 公共写入是独立显式阶段，只维护 descriptions/image_scores。
                valid = data.read_lance(str(paths['image_results']), version=details['image_results']['version'],
                                        filter="status IN ('done', 'reused')")
                valid.map(image_patch_row).write_lance(str(paths['patch']), mode='overwrite', schema=PATCH_ROW)
                details['patch'] = _stage_ref(paths['patch'])
                with registered_table_edit(root, target_relative, schema_name='curated_images', schema_version='v1') as current:
                    if current is None:
                        raise ValueError('Public target must be published by catalog before annotation export')
                    patch_keys = data.read_lance(str(paths['patch']), version=details['patch']['version'], columns=['sha256'])
                    target_keys = data.read_lance(str(target_path), version=current.version, columns=['sha256'])
                    if patch_keys.join(target_keys, on='sha256', how='anti').limit(1).count():
                        raise ValueError('Image SHA absent from target; catalog must publish it first')
                    target_keys.join(patch_keys, on='sha256', how='semi').reduce_by_key('sha256', unique_image).count()
                    missing = [field for field in PATCH_ROW if field.name not in current.schema.names]
                    if missing:
                        data.add_lance_columns(str(target_path), pa.schema(missing), expected_version=current.version)
                    # 平台锁协调写入；版本冲突按平台错误中止，业务不另建重试循环。
                    current = lance.dataset(str(target_path))
                    patch = data.read_lance(str(paths['patch']), version=details['patch']['version'])
                    old = data.read_lance(str(target_path), version=current.version, columns=['sha256', *OWNED_COLUMNS]).map(
                        lambda row: {'sha256': row['sha256'], 'target_present': True,
                                     **{name + '_old': row[name] for name in OWNED_COLUMNS}})
                    updates = (patch.join(old, on='sha256', how='left').map(merge_public_row)
                               .filter(lambda row: row['changed']).select_columns(PATCH_ROW.names))
                    receipt = updates.write_lance(str(target_path), mode='merge', on='sha256',
                        update_columns=OWNED_COLUMNS, when_not_matched='error', expected_version=current.version,
                        return_receipt=True, schema=PATCH_ROW)
                    summary.update(committed=True, target_version=receipt.committed_version)
                    details['merge_stats'] = dict(receipt.merge_stats or {})
                    details['phase'] = 'exported'
            elif cfg['through'] == 'export':
                details['phase'] = 'export_blocked_incomplete'
            summary['complete'] = summary['stage_complete'] and (cfg['through'] != 'export' or summary['committed'])
        summary['details_json'] = json.dumps(details, ensure_ascii=False)
        data.from_items([summary]).write_lance(str(paths['summary']), mode='overwrite', schema=SUMMARY)
        summary_ref = _stage_ref(paths['summary'])
        result = {**summary, 'summary': summary_ref, 'image_config_id': config_id,
                  'image_inputs': details['image_inputs'], 'image_results': details['image_results'],
                  'patch': details['patch'], 'target': {'uri': target_relative, 'version': summary['target_version']}}
        print(f'[Image annotation] complete={summary["complete"]}; committed={summary["committed"]}; '
              f'valid={summary["done_count"] + summary["reused_count"]}/{summary["required_count"]}; '
              f'errors={summary["error_count"]}', flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--config', type=Path, required=True, help='JSON containing the same config() parameters as the notebook')
    parser.add_argument('--through', choices=['prepare', 'annotate', 'export'])
    parser.add_argument('--write-mode', choices=['merge'], default='merge')
    args = parser.parse_args()
    values = json.loads(args.config.read_text())
    values['run'] = args.run
    if args.through:
        values['through'] = args.through
    print(json.dumps(run_pipeline(config(**values)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
