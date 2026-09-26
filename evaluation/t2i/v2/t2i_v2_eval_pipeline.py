"""两步评测：答题写入目标表；独立读取答案表打分，写入指定目标表。"""

from functools import partial

from evaluation.t2i.v2.operaters.transforms import (
    judge_response_fields,
    validate_scores,
    score_values,
    mean_scores,
)

import argparse
import json
import os
import subprocess
from pathlib import Path

import lance
import pyarrow as pa
import yaml
import demiflow
from demiflow.execution.artifacts import digest, run_lock
from demiflow.lance.blobs import LanceBlobStore
from demiflow.lance.records import LanceRecordStore
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow import data
from project import resolve_root
from evaluation.t2i.v2.operaters.answers import GenerateImage
from evaluation.t2i.v2.operaters.images import image_data_url

DATASETS = Path('demiwtg/evaluation/t2i/v2/datasets')
PROMPTS = Path(__file__).parent / 'prompts'
# 每个模型的每道题一行，图片放独立 Blob 表；scores 保留答案及逐项评分。
ANSWERS = pa.schema(
    [
        (name, pa.string())
        for name in ('task_id', 'concept', 'instruction', 'answer_model', 'status', 'reason', 'image_json')
    ]
    + [('generation_seconds', pa.float64())]
)
SCORES = pa.schema(
    [
        *ANSWERS,
        ('judge_model', pa.string()),
        ('judge_json', pa.large_string()),
        ('judge_call_json', pa.large_string()),
        ('alignment_score', pa.float64()),
        ('quality_score', pa.float64()),
        ('aesthetics_score', pa.float64()),
    ]
)


def config(*, answer_models=None, judge_model=None):
    """分别配置答题或 judge；单独打分无需提供答题模型配置。"""
    result = {}
    if answer_models is not None:
        answers = [
            {
                'parameters': {},
                'seed': 42,
                'device': 'cuda:0',
                'api': 'images',
                'use_references': False,
                'base_url': 'http://127.0.0.1:4001/v1',
                'api_key_env': 'MODELHUB_API_KEY',
                'timeout_s': 600,
                **model,
            }
            for model in answer_models
        ]
        names = [(answer.get('model'), answer['use_references']) for answer in answers]
        if not names or any(not name for name, _ in names) or len(set(names)) != len(names):
            raise ValueError('Specify unique model/use_references combinations')
        for answer in answers:
            if type(answer['use_references']) is not bool:
                raise ValueError('use_references must be true or false')
            if answer['use_references'] and answer['backend'] == 'modelhub':
                raise ValueError('Reference inputs are supported by local answer backends only')
            if answer.get('backend') not in {'diffusers', 'bagel', 'modelhub'}:
                raise ValueError('answer backend must be diffusers, bagel or modelhub')
            if answer['backend'] != 'modelhub' and not answer.get('model_path'):
                raise ValueError('Local generation requires model_path')
            if answer['api'] not in {'images', 'chat'} or answer['timeout_s'] <= 0:
                raise ValueError('Invalid API or timeout')
        result['answers'] = answers
    if judge_model is not None:
        judge = {
            'mode': 'online',
            'base_url': 'http://127.0.0.1:4001/v1',
            'api_key_env': 'MODELHUB_API_KEY',
            'timeout_s': 600,
            'max_output_tokens': 8192,
            **judge_model,
        }
        if not judge.get('model') or judge['mode'] not in {'online', 'offline'}:
            raise ValueError('Specify judge model and online/offline mode')
        if judge['max_output_tokens'] < 1 or judge['timeout_s'] <= 0:
            raise ValueError('Token limit and timeouts must be positive')
        result['judge'] = judge
    return result


def run_answers(run, source, model):
    """执行一个模型的标准生成链；逐题保存答案记录，最后统一写目标表。

    BAGEL 等独立 Python 环境也调用此函数；每个模型处理完全部题目后释放资源。
    """
    root = resolve_root()
    records = LanceRecordStore(root, str(DATASETS / f'records__{run.name}.lance'))
    blobs = LanceBlobStore(root, str(DATASETS / f'images__{run.name}.lance'))
    generator = GenerateImage(
        model, records, blobs, root / '_demiflow' / 'evaluation_t2i_v2' / run.name / 'offload'
    )
    (
        data.read_lance(source['uri'], version=source['version'])
        .map(
            lambda row: {key: row.get(key) for key in ('task_id', 'concept', 'instruction', 'references_json')}
        )
        .map_async(generator, concurrency=1, queue_depth=1)
        .run_stream(log_every=0)
    )


def run_pipeline(run, source, config, *, target_uri=None, write_mode='overwrite'):
    """第一步：读取 benchmark，答题后写目标表，所有评分字段为空。

    返回 target 的 uri/version，供独立打分使用；本入口不会调用 judge。
    """
    root = resolve_root()
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    run = Path(run).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    if type(source.get('version')) is not int or source['version'] < 1:
        raise ValueError('Specify a fixed input table version')
    source = {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
    target_uri = (
        str((root / target_uri).resolve()) if target_uri else str(run.parent / f'results__{run.name}.lance')
    )
    with run_lock(root / '_demiflow' / 'evaluation_t2i_v2' / run.name):
        records = LanceRecordStore(root, str(DATASETS / f'records__{run.name}.lance'))
        directory = Path(__file__).parent
        paths = [directory / 't2i_v2_eval_pipeline.py', directory / 'operaters/answers.py']
        code = {str(path.relative_to(directory)): path.read_text() for path in paths if path.is_file()}
        if any(model['backend'] == 'bagel' for model in config['answers']):
            code['evaluation/bagel/adapter.py'] = (directory.parents[1] / 'bagel/adapter.py').read_text()
        request = {
            'source': source,
            'target_uri': target_uri,
            'write_mode': write_mode,
            'config': {'answers': config['answers']},
            'code': code,
        }
        previous = records.get('manifest')
        if previous is not None and previous != request:
            raise ValueError('Input, configuration or code changed; use a new run name')
        records.put('manifest', request)
        state = records.get('state')
        if state:
            return state
        # 参考信息按模型配置启用；考点与判据始终不进入答题输入。
        questions = data.read_lance(source['uri'], version=source['version']).materialize()
        invalid = questions.filter(
            lambda row: not isinstance(row.get('task_id'), str)
            or not row['task_id'].strip()
            or not isinstance(row.get('instruction'), str)
            or not row['instruction'].strip()
        ).take(1)
        duplicate_ids = (
            (
                questions.reduce_by_key(
                    'task_id',
                    lambda acc, row: {'task_id': row['task_id'], 'count': (acc['count'] if acc else 0) + 1},
                )
                .filter(lambda row: row['count'] > 1)
                .take(1)
            )
            if not invalid
            else []
        )
        if invalid or duplicate_ids:
            raise ValueError('Input requires unique nonempty task_id and nonempty instruction')
        if any(model['use_references'] for model in config['answers']):
            if questions.filter(lambda row: not isinstance(json.loads(row['references_json']), list)).take(1):
                raise ValueError('Reference answers require references_json containing a list')
        # 逐题结果已持久化到 records；所有答题结束后一次写表，重试 writer 不重生成。
        for index, model in enumerate(config['answers']):
            if model.get('python'):
                # 独立环境仅用于解决模型依赖冲突；子进程仍执行同一个正式生成链。
                environment = dict(os.environ)
                # 两个模型环境共用本次运行的业务源码与 demiflow，避免旧安装覆盖当前 API。
                environment['PYTHONPATH'] = os.pathsep.join(
                    [
                        str(directory.parents[2]),
                        str(Path(demiflow.__file__).resolve().parents[1]),
                        # Qwen-Image-2.1 的专用依赖只进入该模型的子进程。
                        *model.get('pythonpath', []),
                        environment.get('PYTHONPATH', ''),
                    ]
                )
                environment['DEMIWTG_DATASETS_ROOT'] = str(root)
                if model.get('cuda_visible_devices') is not None:
                    environment['CUDA_VISIBLE_DEVICES'] = str(model['cuda_visible_devices'])
                subprocess.run(
                    [
                        model['python'],
                        '-m',
                        'evaluation.t2i.v2.t2i_v2_eval_pipeline',
                        '--run',
                        run.name,
                        '--generate-model',
                        str(index),
                    ],
                    env=environment,
                    check=True,
                )
            else:
                run_answers(run, source, model)
        # 题目×模型的答案引用由模型、是否带参考、task_id 定位；字段投影显式将评分置空。
        answer_sources = [
            questions.map(
                lambda row, model=model: records.get(
                    'answer/' + digest([model['model'], model['use_references'], row['task_id']])
                )
            )
            for model in config['answers']
        ]
        answers = (
            answer_sources[0]
            .union(*answer_sources[1:])
            .map(lambda row: {name: row.get(name) for name in SCORES.names})
            .materialize()
        )
        answers.write_lance(target_uri, mode=write_mode, schema=SCORES)
        # 仅收集按 status 聚合后的计数；不将全部答案拉回 Python 逐条统计。
        counts = {
            row['status']: row['count']
            for row in answers.reduce_by_key(
                'status', lambda acc, row: {'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}
            ).take_all()
        }
        state = {
            'complete': set(counts) <= {'generated'},
            'counts': counts,
            'target': {'uri': target_uri, 'version': lance.dataset(target_uri).version},
        }
        records.put('state', state, immutable=False)
        return state


def run_judging(run, source, config, *, target_uri, write_mode='overwrite'):
    """第二步：读取源表固定版本的答案，judge 后写入指定目标表。

    源表和目标表分别配置；目标可以与源相同，也可以是另一张表。

    不调用答题模型。每次独立打分使用自己的运行名和调用日志；同名可续跑。
    所有行处理完成后才提交目标表的新版本，执行中断不会提前清空原表。
    """
    root = resolve_root()
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    run = Path(run).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    if type(source.get('version')) is not int or source['version'] < 1:
        raise ValueError('Specify a fixed input table version')
    source = {'uri': str((root / source['uri']).resolve()), 'version': source['version']}
    target_uri = str((root / target_uri).resolve())
    with run_lock(root / '_demiflow' / 'evaluation_t2i_v2' / run.name):
        records = LanceRecordStore(root, str(DATASETS / f'records__{run.name}.lance'))
        calls = {'root': str(root), 'relative_uri': str(DATASETS / f'calls__{run.name}.lance')}
        directory = Path(__file__).parent
        paths = [
            directory / 't2i_v2_eval_pipeline.py',
            directory / 'operaters/images.py',
            *sorted(PROMPTS.glob('*')),
        ]
        request = {
            'source': source,
            'target_uri': target_uri,
            'write_mode': write_mode,
            'judge': config['judge'],
            'code': {str(path.relative_to(directory)): path.read_text() for path in paths if path.is_file()},
        }
        previous = records.get('manifest')
        if previous is not None and previous != request:
            raise ValueError('Input, output, judge configuration or code changed; use a new run name')
        records.put('manifest', request)
        state = records.get('state')
        if state and state['complete']:
            return state
        # judge 配置直接进入原生 prompt pack；保留用户指定模型名，不依赖网关目录完整性。
        judge = config['judge']
        spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
        spec['prompts']['judge']['model'].update(
            name=judge['model'], base_url=judge['base_url'], api_key_env=judge['api_key_env']
        )
        pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
        if judge['mode'] == 'offline':
            options = {'offline_store': calls}
        else:
            os.environ.setdefault(judge['api_key_env'], 'anything')
            options = {
                'lance_journal': calls,
                'timeout_s': judge['timeout_s'],
                'verify_model': False,
                'trust_env': False,
                'require_finish_reason_stop': True,
                'request_options': {
                    'temperature': 0,
                    'max_tokens': judge['max_output_tokens'],
                    'response_format': {'type': 'json_object'},
                },
            }
        # 每行是一张既有答案；已有图片可以重新打分。输入只包含题面和图片，不发送模型名、考点或参考。
        scored = (
            data.read_lance(source['uri'], version=source['version'])
            .map(
                lambda row: (
                    {
                        **row,
                        'status': 'generated',
                        'reason': '',
                        'prompt_images': [image_data_url(json.loads(row['image_json']), root)],
                    }
                    if row.get('image_json')
                    else row
                )
            )
            .map_prompt_async(
                'judge',
                config=pack,
                options=options,
                inputs={'instruction': 'instruction', 'images': 'prompt_images'},
                output='judge_result',
                call_output='judge_call',
                error_output='judge_error',
                when=lambda row: row['status'] == 'generated',
                concurrency=1,
                queue_depth=1,
            )
            .map(partial(judge_response_fields, ANSWERS=ANSWERS, judge=judge))
            .map(
                lambda row: (
                    {
                        **row,
                        'status': (
                            'pending_judge'
                            if row['judge_error']['type'] == 'PromptResponsePending'
                            else 'judge_failed'
                        ),
                        'reason': row['judge_error']['detail'],
                    }
                    if row['status'] == 'generated' and row['judge_error']
                    else row
                )
            )
            # 分值只接受整数 0/1/2 或 N/A；不把字符串、浮点或布尔值转换成合法分数。
            .map(validate_scores)
            .map(
                lambda row: (
                    {**row, 'status': 'judge_failed', 'reason': row['invalid_scores'][0]}
                    if row['status'] == 'generated' and row['invalid_scores']
                    else row
                )
            )
            # 各维度排除 N/A 后，将 0/1/2 映射为 0/60/100 求均值；全 N/A 的维度仍为空。
            .map(score_values)
            .map(mean_scores)
            .map(lambda row: {name: row.get(name) for name in SCORES.names})
            # 固定异步结果供指纹、写表和计数复用；缓存仅含题面、Blob 引用和评分。
            .materialize()
        )
        # 相同评分快照只写一次；待响应或失败的结果再次查看时不重复追加。
        output_key = 'scores/' + digest(scored.take_all())
        output = records.get(output_key)
        if output is None:
            scored.write_lance(target_uri, mode=write_mode, schema=SCORES)
            output = {'uri': target_uri, 'version': lance.dataset(target_uri).version}
            records.put(output_key, output)
        counts = {
            row['status']: row['count']
            for row in scored.reduce_by_key(
                'status', lambda acc, row: {'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}
            ).take_all()
        }
        state = {'complete': set(counts) <= {'scored'}, 'counts': counts, 'target': output, 'calls': calls}
        records.put('state', state, immutable=False)
        return state


def main():
    """CLI 与 notebook 共用入口；模型子进程只读取父进程已冻结的配置。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--table')
    parser.add_argument('--version', type=int)
    parser.add_argument('--config', help='JSON file containing answers or judge')
    parser.add_argument('--stage', choices=['answers', 'judge'], default='answers')
    parser.add_argument('--target', help='Output table URI (required for judge)')
    parser.add_argument('--write-mode', choices=['append', 'overwrite'], default='overwrite')
    parser.add_argument(
        '--generate-model', type=int, help='Execute model at this index in the frozen configuration'
    )
    args = parser.parse_args()
    root = resolve_root()
    run = root / DATASETS / args.run
    if args.generate_model is not None:
        manifest = LanceRecordStore(root, str(DATASETS / f'records__{run.name}.lance')).get('manifest')
        model = manifest['config']['answers'][args.generate_model]
        run_answers(run, manifest['source'], model)
        return
    if not args.table or args.version is None or not args.config:
        parser.error('--table, --version and --config are required')
    settings = json.loads(Path(args.config).read_text())
    source = {'uri': args.table, 'version': args.version}
    if args.stage == 'answers':
        settings = config(answer_models=settings['answers'])
        state = run_pipeline(run, source, settings, target_uri=args.target, write_mode=args.write_mode)
    else:
        settings = config(judge_model=settings['judge'])
        if not args.target:
            parser.error('--target is required for judge')
        state = run_judging(run, source, settings, target_uri=args.target, write_mode=args.write_mode)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
