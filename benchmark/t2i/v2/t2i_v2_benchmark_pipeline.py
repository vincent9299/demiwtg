"""按概念分别读取 preparation 文字与图片，限制图片数并编号，每概念请求一次后写题表。"""

import argparse
import json
import math
import os
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

import lance
import pyarrow as pa
import yaml
from demiflow.execution.artifacts import digest, run_lock
from demiflow.lance.records import LanceRecordStore
from demiflow.operator_llm.parser import parse_prompt_pack
from demiflow import data
from preparation.prompts import validate_local_endpoint
from project import resolve_root
from benchmark.t2i.v2.operaters.authoring import prepare_request, check_response
from benchmark.t2i.v2.operaters.images import image_blob_ref

DATASETS = Path('demiwtg/benchmark/t2i/v2/datasets')
PROMPTS = Path(__file__).parent / 'prompts'

# 与下方三个 writer 对应：每概念的输入、每概念的设计结果、每题一行的候选。
# 模型字段及类型由 tasks.yaml 检查，表结构不承担额外业务审核。
TEST_POINT = pa.struct([('point', pa.string()), ('basis', pa.string())])
QUESTION = pa.struct([('instruction', pa.string()), ('test_points', pa.list_(TEST_POINT))])
INPUTS = pa.schema(
    [
        ('concept', pa.string()),
        ('status', pa.string()),
        ('reason', pa.large_string()),
        ('references_json', pa.large_string()),
    ]
)
DESIGNS = pa.schema([*INPUTS, ('question', QUESTION), ('reasoning', pa.large_string()), ('call_json', pa.large_string())])
QUESTIONS = pa.schema(
    [
        ('task_id', pa.string()),
        ('concept', pa.string()),
        ('status', pa.string()),
        *QUESTION,
        ('reasoning', pa.large_string()),
        ('references_json', pa.large_string()),
    ]
)


def config(
    *,
    run,
    concepts,
    article_source=None,
    visual_source=None,
    target_uri=None,
    write_mode='overwrite',
    mode='offline',
    model=None,
    base_url=None,
    api_key_env=None,
    max_calls=None,
    max_reference_images=8,
    max_context_chars=60000,
    max_output_tokens=8192,
    timeout_s=600,
    concurrency=1,
    queue_depth=1,
    temperature=0,
):
    """统一运行位置、材料来源、输出和模型参数；默认 offline 不发起 HTTP 请求。"""
    if mode not in {'offline', 'local', 'modelhub'}:
        raise ValueError('mode must be offline, local or modelhub')
    if (
        not isinstance(concepts, (list, tuple))
        or not concepts
        or any(not isinstance(c, str) or not c.strip() for c in concepts)
        or len(set(concepts)) != len(concepts)
    ):
        raise ValueError('Select nonempty, unique concept names')
    # 每个概念一次请求，返回一道题或明确不足；调用预算按概念数计。
    max_calls = len(concepts) if max_calls is None else max_calls
    for value in (max_calls, max_reference_images, max_context_chars, max_output_tokens, concurrency):
        if type(value) is not int or value < 1:
            raise ValueError('Counts and budgets must be positive integers')
    if queue_depth is not None and (type(queue_depth) is not int or queue_depth < 1):
        raise ValueError('queue_depth must be a positive integer or None')
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0:
        raise ValueError('temperature must be a finite nonnegative number')
    if timeout_s <= 0:
        raise ValueError('timeout_s must be positive')
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    return {
        'run': str(run),
        'article_source': article_source,
        'visual_source': visual_source,
        'target_uri': str(target_uri) if target_uri is not None else None,
        'write_mode': write_mode,
        'concepts': list(concepts),
        'mode': mode,
        'model': model or ('qwen3.8-27b' if mode == 'local' else 'glm/glm-5.3-flash'),
        'base_url': base_url or ('http://127.0.0.1:8000/v1' if mode == 'local' else 'http://127.0.0.1:4001/v1'),
        'api_key_env': api_key_env or ('T2I_LOCAL_API_KEY' if mode == 'local' else 'MODELHUB_API_KEY'),
        'max_calls': max_calls,
        'max_reference_images': max_reference_images,
        'max_context_chars': max_context_chars,
        'max_output_tokens': max_output_tokens,
        'timeout_s': timeout_s,
        'concurrency': concurrency,
        'queue_depth': queue_depth,
        'temperature': temperature,
    }


def run_pipeline(config):
    """从 preparation 固定版本的图文结果出题，返回输入、设计和候选表引用。

    config 统一提供 run、article_source、visual_source、target_uri、write_mode 及模型参数。
    两类来源各指定一张表的 uri/version；不提供某类材料时设为 None。
    每概念一行输入、一次模型请求，返回一道题或明确不足；概念间并发数由 concurrency 控制，同概念材料不拆批。
    write_mode 只控制候选目标表；每次按当前参数读取材料，原生模型调用日志复用相同请求。
    """
    root = resolve_root()
    run = Path(config['run']).absolute()
    if run.parent != root / DATASETS or not run.name or run.suffix:
        raise ValueError('run must be a suffix-free name under ' + str(root / DATASETS))
    target_uri = (
        str((root / config['target_uri']).resolve()) if config['target_uri'] else str(run.parent / f'candidates__{run.name}.lance')
    )
    started = perf_counter()
    request_times = {}

    def log(message):
        """立即输出到 CLI/notebook；只记阶段摘要，不打印材料、图片字节或凭据。"""
        print(f'[T2I V2][{run.name}][+{perf_counter() - started:.1f}s] {message}', flush=True)

    def request_started(concept, image_count):
        """由原生调用的 when 在执行时记录起点；不把上游预取时间算作请求耗时。"""
        request_times[concept] = perf_counter()
        log(f'开始出题：{concept}，图片={image_count}，超时={config["timeout_s"]}s')
        return True

    def log_response(row):
        """记录实际返回/跳过并原样传递该行；计时不参与模型输入、判据或输出表。"""
        if row['status'] == 'ready':
            error = row.get('design_error') or {}
            call = row.get('design_call') or error.get('call', {})
            log(
                f'出题返回：{row["concept"]}，本次等待={perf_counter() - request_times[row["concept"]]:.1f}s，'
                f'复用={call.get("reused", "未知")}，错误={error.get("type", "无")} {error.get("detail", "")}'
            )
        else:
            log(f'跳过出题：{row["concept"]}，状态={row["status"]}，原因={row["reason"]}')
        return row

    log(
        f'开始运行，等待运行锁；概念数={len(config["concepts"])}，模型={config["model"]}，'
        f'模式={config["mode"]}，每概念一次、并发={config["concurrency"]}；续跑先查调用日志'
    )
    with run_lock(root / '_demiflow' / 'benchmark_t2i_v2' / run.name):
        # 锁住同名运行，避免并发覆盖同一组输出；不限制修改配置或代码。
        records = LanceRecordStore(root, str(DATASETS / f'records__{run.name}.lance'))
        call_store = {'root': str(root), 'relative_uri': str(DATASETS / f'calls__{run.name}.lance')}
        log('已取得运行锁，使用本次传入的来源和参数')
        # 直接配置原生 prompt pack 与调用参数；offline 只登记请求，不发起 HTTP 调用。
        spec = yaml.safe_load((PROMPTS / 'tasks.yaml').read_text())
        spec['prompts']['design_question']['model'].update(
            name=config['model'], base_url=config['base_url'], api_key_env=config['api_key_env']
        )
        pack = parse_prompt_pack(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
        if config['mode'] == 'offline':
            options = {'offline_store': call_store}
        else:
            provider = {}
            if config['mode'] == 'local':
                validate_local_endpoint(config['base_url'], config['model'])
                provider = {'chat_template_kwargs': {'enable_thinking': False}}
            else:
                # 保留原有网关地址检查；上游地址和密钥由 modelhub 管理。
                url = urlparse(config['base_url'])
                if (
                    url.scheme != 'http'
                    or url.hostname not in {'localhost', '127.0.0.1', '::1'}
                    or url.path.rstrip('/') != '/v1'
                    or url.username
                    or url.password
                    or url.query
                    or url.fragment
                ):
                    raise ValueError('modelhub requires a loopback /v1 gateway URL')
            os.environ.setdefault(config['api_key_env'], 'anything')
            options = {
                'lance_journal': call_store,
                'timeout_s': config['timeout_s'],
                'verify_model': True if config['mode'] == 'local' else 'listed',
                'require_finish_reason_stop': True,
                'trust_env': False,
                'request_options': {
                    'temperature': config['temperature'],
                    'max_tokens': config['max_output_tokens'],
                    'response_format': {'type': 'json_object'},
                    **provider,
                },
            }
        input_uri = str(run.parent / f'inputs__{run.name}.lance')
        materials_started = perf_counter()
        log(
            f'开始准备材料：文章={config["article_source"]}，图片={config["visual_source"]}；按概念取正文和独立图片'
        )

        quoted_concepts = ["'" + c.replace("'", "''") + "'" for c in config['concepts']]

        # 文章每行是一篇已审核文章；同概念的多篇文章均作为材料，不去重或判冲突。
        # preparation 负责正文清洗；这里仅展开主题正文，忽略引用、配图和内部审核字段。
        if config['article_source'] is not None:
            texts = (
                data.read_lance(
                    str(root / config['article_source']['uri']),
                    version=config['article_source']['version'], columns=['concept', 'content'],
                    filter="review_status = 'reviewed' AND concept IN (" + ','.join(quoted_concepts) + ')',
                )
                .flat_map(lambda row: [
                    {'concept': row['concept'], 'text': {
                        'kind': 'text', 'title': topic['title'], 'text': paragraph,
                    }}
                    for topic in row['content'] or [] for paragraph in topic['content']['paragraphs'] or []
                ])
                .reduce_by_key('concept', lambda acc, row: {
                    'concept': row['concept'], 'texts': (acc['texts'] if acc else []) + [row['text']],
                })
            )
        else:
            texts = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))

        # 图片每行包含多个概念关系；仅按公开发布/审核状态选取本次概念的图片。
        # 使用 preparation 已交付的存储引用，不读取来源或审核 JSON，不与文章匹配。
        if config['visual_source'] is not None:
            images = (
                data.read_lance(
                    str(root / config['visual_source']['uri']),
                    version=config['visual_source']['version'],
                    columns=['sha256', 'source_refs', 'concept_assessments'],
                    filter=' OR '.join('array_contains(published_concepts, ' + c + ')' for c in quoted_concepts),
                )
                .flat_map(lambda row: [
                    {'concept': assessment['concept'], 'sha256': row['sha256'],
                     'source_refs': row['source_refs']}
                    for assessment in row['concept_assessments'] or []
                    if assessment['concept'] in config['concepts']
                    and assessment['published'] and assessment['review_status'] == 'keep'
                ])
                # 在聚合中限制图片数量，不为未选中的图片读取字节或解析引用。
                .reduce_by_key('concept', lambda acc, row: {
                    'concept': row['concept'],
                    'images': ((acc['images'] if acc else []) + [row])[:config['max_reference_images']],
                })
                .map(lambda row: {
                    'concept': row['concept'],
                    'images': [
                        {'kind': 'image', 'blob_ref': image_blob_ref(im)} for im in row['images']
                    ],
                })
            )
        else:
            images = data.from_arrow(pa.table({'concept': pa.array([], type=pa.string())}))

        # 配置中的每个概念均进入模型节点；缺少文字或图片时列表为空，不生成缺失状态。
        # 文字在前、图片在后，从 1 编号；图片内部顺序与随后编码顺序一致。
        log(f'开始执行材料链并写输入表：{input_uri}')
        (
            data.from_items([{'concept': c} for c in config['concepts']])
            .join(texts, on='concept', how='left')
            .join(images, on='concept', how='left')
            .map(lambda row: {
                'concept': row['concept'], 'status': 'ready', 'reason': '',
                'references_json': json.dumps([
                    {'number': i, **ref}
                    for i, ref in enumerate(row.get('texts', []) + row.get('images', []), 1)
                ], ensure_ascii=False),
            })
            .write_lance(input_uri, mode='overwrite', schema=INPUTS)
        )
        inputs = {'uri': input_uri, 'version': lance.dataset(input_uri).version}
        log(f'材料准备并落表完成：耗时={perf_counter() - materials_started:.1f}s，版本={inputs["version"]}')
        # 3. 每概念一行、一次模型请求；materialize 执行异步链，完成后按明确 schema 写设计表。
        # 请求用的图片字节在缓存前投影掉；空结果也能写成有效空表。
        designs_uri = str(run.parent / f'designs__{run.name}.lance')
        log(f'开始执行出题；所有概念处理完成后写入设计表：{designs_uri}')
        (
            data.read_lance(inputs['uri'], version=inputs['version'])
            .map(
                lambda row: log(f'准备概念输入：{row["concept"]}，状态={row["status"]}；读取图片并编码') or row
            )
            .map(
                prepare_request,
                fn_kwargs={
                    'max_context_chars': config['max_context_chars'],
                    'prompt_chars': len(pack.prompt_definitions['design_question'].template.source),
                },
            )
            # 每个 ready 概念一次请求；图片数和超时在实际执行时输出，续跑由原生日志复用响应。
            .map_prompt_async(
                'design_question',
                config=pack,
                options=options,
                max_requests=config['max_calls'],
                inputs={'payload': 'prompt_payload', 'images': 'prompt_images'},
                output='design_result',
                call_output='design_call',
                error_output='design_error',
                when=lambda row: row['status'] == 'ready'
                and request_started(row['concept'], len(row['prompt_images'])),
                concurrency=config['concurrency'],
                queue_depth=config['queue_depth'],
            )
            .map(log_response)
            .map(check_response, fn_kwargs={
                'question_schema': pack.prompt_definitions['design_question'].response_schema[
                    'properties']['result']['properties']['question'],
            })
            # 只留下设计表字段，去掉用于请求的图片字节和原始响应；字段顺序与 Arrow schema 一致。
            .map(lambda row: {name: row[name] for name in DESIGNS.names})
            .map(
                lambda row: log(
                    f'概念结果：{row["concept"]}，状态={row["status"]}，'
                    f'题数={int(row["question"] is not None)}，原因={row["reason"] or "无"}'
                )
                or row
            )
            .materialize()
            .write_lance(designs_uri, mode='overwrite', schema=DESIGNS)
        )
        designs_version = lance.dataset(designs_uri).version
        log(f'出题执行及设计表写入完成：版本={designs_version}')
        designs = data.read_lance(designs_uri, version=designs_version)
        # 每概念只有一个 question；有题的行直接投影，空题及失败原因留在设计表。
        questions = (
            designs.filter(lambda row: row['status'] == 'candidate')
            .map(
                lambda row: {
                    'task_id': 't2i_' + digest([row['concept'], row['question'], row['references_json']]),
                    'concept': row['concept'],
                    'status': 'unreviewed',
                    **row['question'],
                    'reasoning': row['reasoning'],
                    'references_json': row['references_json'],
                }
            )
            .materialize()
        )

        # 4. 候选 Dataset 物化后供内容指纹和目标 writer 复用；不取出行再重建 Dataset。
        # 指纹仍汇总本批候选（文字和 Blob 引用，不含图片字节），保留续跑防重复追加的提交边界。
        # append 复用同目标同内容的提交记录，避免重复追加；overwrite 每次执行覆盖。
        # 零候选也写出具有 QUESTIONS schema 的空结果，覆盖模式下目标表会被清空。
        candidate_rows = questions.take_all()
        output_key = 'candidates/' + digest([target_uri, config['write_mode'], candidate_rows])
        output = records.get(output_key) if config['write_mode'] == 'append' else None
        if output is None:
            log(f'开始写候选表：题数={len(candidate_rows)}，模式={config["write_mode"]}，目标={target_uri}')
            questions.write_lance(target_uri, mode=config['write_mode'], schema=QUESTIONS)
            output = {'uri': target_uri, 'version': lance.dataset(target_uri).version}
            if config['write_mode'] == 'append':
                records.put(output_key, output)
            log(f'候选表写入完成：版本={output["version"]}')
        else:
            log(f'复用已写候选表：{output["uri"]}，版本={output["version"]}')

        # 按设计表的 status 统计概念数，将统计、本次题数和已提交的表路径/版本保存到 state。
        counts = {
            row['status']: row['count']
            for row in designs.reduce_by_key(
                'status', lambda acc, row: {'status': row['status'], 'count': (acc['count'] if acc else 0) + 1}
            ).take_all()
        }
        # 所有概念均为 candidate（已出题）或 insufficient（有理由的零题）时才标记 complete；不表示审题通过。
        state = {
            'complete': set(counts) <= {'candidate', 'insufficient'},
            'counts': counts,
            'candidate_count': len(candidate_rows),
            'inputs': inputs,
            'designs': {'uri': designs_uri, 'version': designs_version},
            'candidates': output,
            'calls': call_store,
        }
        # 此处只在前面的写表成功后执行；offline 的 pending 可补交响应后同名续跑。
        records.put('state', state, immutable=False)
        log(f'运行结束：状态={counts}，候选={len(candidate_rows)} 题，complete={state["complete"]}')
        return state


def main():
    """命令行入口；与 notebook 调用同一个 run_pipeline，不另建执行链。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--run', required=True, help='New run name (tables are flat under this pipeline datasets/)'
    )
    parser.add_argument('--concept', action='append', required=True)
    parser.add_argument('--article-table', help='preparation 文章结果表路径')
    parser.add_argument('--article-version', type=int, help='文章表固定 Lance 版本')
    parser.add_argument('--visual-table', help='preparation 图片结果表路径')
    parser.add_argument('--visual-version', type=int, help='图片表固定 Lance 版本')
    parser.add_argument('--mode', choices=['offline', 'local', 'modelhub'], default='offline')
    parser.add_argument('--model')
    parser.add_argument('--concurrency', type=int, default=1, help='同时执行的概念请求数')
    parser.add_argument('--queue-depth', type=int, default=1, help='模型节点队列深度')
    parser.add_argument('--temperature', type=float, default=0, help='模型采样温度')
    parser.add_argument('--target', help='候选题输出表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    args = parser.parse_args()
    for name in ('article', 'visual'):
        if bool(getattr(args, name + '_table')) != (getattr(args, name + '_version') is not None):
            parser.error('--' + name + '-table and --' + name + '-version must be supplied together')
    article_source = (
        {'uri': args.article_table, 'version': args.article_version} if args.article_table else None
    )
    visual_source = {'uri': args.visual_table, 'version': args.visual_version} if args.visual_table else None
    cfg = config(
        run=resolve_root() / DATASETS / args.run,
        concepts=args.concept, mode=args.mode, model=args.model,
        concurrency=args.concurrency, queue_depth=args.queue_depth, temperature=args.temperature,
        article_source=article_source,
        visual_source=visual_source,
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    result = run_pipeline(cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
