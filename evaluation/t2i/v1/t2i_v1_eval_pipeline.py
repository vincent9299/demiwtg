"""V1 评测：读取冻结题目，生成/导入答案，调用原版本 judge，写评分和汇总表。"""

import lance
import pyarrow as pa
from demiflow.execution.artifacts import digest
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import Catalog
from demiflow.lance.storage import schema_hash
from preparation.operaters.results import PIPELINE_STAGE_ROWS, to_stage_row, from_stage_row
from project import historical_input
import argparse
import json
from pathlib import Path
from functools import partial
from demiflow import data
from demiflow.execution.artifacts import run_lock
from evaluation.t2i.v1.operaters.runfiles import graph_digest, T2IEvaluationRunFiles
from evaluation.t2i.v1.operaters.answers import BuildAnswerJobs, GenerateT2I
from evaluation.t2i.v1.operaters.judging import (
    prompt_config,
    prepare_judge,
    apply_judge,
    prompt_responses,
    aggregate,
)

DEFAULT_JUDGE = {
    "base_url": "http://127.0.0.1:8000/v1",
    "name": "qwen3.8-27b",
    "max_output_tokens": 32768,
    "timeout_s": 600,
}


def config(
    mode,
    models,
    *,
    judge_model=None,
    endpoint="http://127.0.0.1:4001/v1",
    gen_mode="auto",
    cond="V2",
    max_edge=1024,
    max_calls=100,
    timeout=300,
    limit=None,
):
    # 此入口仅持有 v6.0-V2 协议，不能把其他条件名悄悄套用到同一规则。
    if cond != 'V2':
        raise ValueError('evaluation.t2i.v1 only supports the v6.0-V2 judge contract')
    if mode not in ('offline', 'online', 'local'):
        raise ValueError('mode must be offline, online or local')
    if mode != 'online' and not models:
        # Imported historical responses may substitute for generation.
        pass
    return {
        'mode': mode,
        'models': list(models or []),
        'endpoint': endpoint,
        'gen_mode': gen_mode,
        'cond': cond,
        'max_edge': max_edge,
        'limit': limit,
        'timeout': timeout,
        'judge_model': judge_model or (DEFAULT_JUDGE['name'] if mode == 'local' else 'external'),
        'judge': {**DEFAULT_JUDGE, 'max_calls': max_calls},
    }


def run_pipeline(
    run, questions, config, through="summary", responses=None, *, target_uri=None, write_mode='overwrite'
):
    """保留 V1 处理协议，最终结果表按 write_mode 追加或覆盖；中间阶段仍按运行保存。"""
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        files = T2IEvaluationRunFiles(run, config, graph_digest(), questions, responses)
        # 目标与模式随运行冻结，完成的输出在续跑时不重复写入。
        target_uri = str((files.storage_root / target_uri).resolve()) if target_uri else None
        files.records.put('output', {'uri': target_uri, 'write_mode': write_mode})
        # prompt pack 沿用 V1 评分协议；异步调用和写表批次分别配置，保存响应便于重放。
        pack, options = prompt_config(run, config)
        # 每道冻结题目一行；题号与题面检查直接写入状态，错误题不进入模型调用。
        questions_stage = data.from_items(
            [
                {
                    'task_id': str(q.get('qid') or f'q{index}'),
                    'qid': str(q.get('qid') or f'q{index}'),
                    'question': q,
                    'gen_prompt': q.get('gen_prompt'),
                    'level': q.get('level'),
                    'branch': 'evaluation',
                    'status': 'question_ready' if q.get('gen_prompt') else 'invalid_question',
                }
                for index, q in enumerate(files.questions['rows'], 1)
            ]
        )
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'questions',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('questions' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'questions' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                questions_stage.map(
                    partial(to_stage_row, stage='questions', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'questions' + "/" + version, entry)
        files.stages['questions'] = entry
        (files.reused if reused else files.new).append('questions')
        files.previous = {"stage": 'questions', "stage_version": version}
        questions_stage = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(
            from_stage_row
        )
        if through == "questions":
            return files.finish()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'answer_jobs',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('answer_jobs' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'answer_jobs' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                questions_stage.flat_map(BuildAnswerJobs(config, files.responses)).map(
                    partial(to_stage_row, stage='answer_jobs', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'answer_jobs' + "/" + version, entry)
        files.stages['answer_jobs'] = entry
        (files.reused if reused else files.new).append('answer_jobs')
        files.previous = {"stage": 'answer_jobs', "stage_version": version}
        answers = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        # 只有 online 且未导入历史答案时才调用答题模型；其余路径保留导入结果或等待状态。
        if config["mode"] == "online" and not files.responses:
            version = digest(
                {
                    "upstream": files.previous,
                    "stage": 'generated',
                    "extra": None,
                    "implementation": files.manifest.get("implementation"),
                }
            )
            relative = str(
                Path(files.relative).parent
                / ('generated' + '__' + Path(files.relative).name + '__' + version + '.lance')
            )
            uri = str(files.storage_root / relative)
            entry = files.records.get("stage/" + 'generated' + "/" + version)
            reused = entry is not None
            if not reused:
                if Path(uri).exists():
                    raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
                (
                    answers.map_async(GenerateT2I(run, config))
                    .map(
                        partial(
                            to_stage_row, stage='generated', upstream_identity=files.previous, migrated_us=0
                        )
                    )
                    .materialize()
                    .write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
                )
                committed = lance.dataset(uri)
                ref = DatasetRef(
                    relative[:-6],
                    relative,
                    committed.version,
                    files.schema_name,
                    files.schema_version,
                    schema_hash(PIPELINE_STAGE_ROWS),
                    committed.count_rows(),
                )
                Catalog(files.storage_root).register(ref)
                entry = {"version": version, "dataset_ref": ref.to_dict()}
                files.records.put("stage/" + 'generated' + "/" + version, entry)
            files.stages['generated'] = entry
            (files.reused if reused else files.new).append('generated')
            files.previous = {"stage": 'generated', "stage_version": version}
            generated = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        else:
            generated = answers  # offline: pending_generation；导入响应：已 generated
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'answers',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('answers' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'answers' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                generated.map(
                    partial(to_stage_row, stage='answers', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'answers' + "/" + version, entry)
        files.stages['answers'] = entry
        (files.reused if reused else files.new).append('answers')
        files.previous = {"stage": 'answers', "stage_version": version}
        _saved = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "answers":
            return files.finish()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'judge_requests',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('judge_requests' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'judge_requests' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                generated.map(partial(prepare_judge, run=run, pack=pack, config=config)).map(
                    partial(
                        to_stage_row, stage='judge_requests', upstream_identity=files.previous, migrated_us=0
                    )
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'judge_requests' + "/" + version, entry)
        files.stages['judge_requests'] = entry
        (files.reused if reused else files.new).append('judge_requests')
        files.previous = {"stage": 'judge_requests', "stage_version": version}
        judge_requests = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "judge_requests":
            return files.finish()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'scores',
                "extra": prompt_responses(run, "judge"),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('scores' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        if target_uri:
            relative = str(Path(target_uri).relative_to(files.storage_root))
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'scores' + "/" + version)
        reused = entry is not None
        if not reused:
            if not target_uri and Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            # 异步评分先由 Dataset 固定结果，再一次提交目标表。
            (
                judge_requests.map_prompt_async(
                    "judge",
                    config=pack,
                    options=options,
                    max_requests=config['judge']['max_calls'],
                    inputs={
                        "gen_prompt": "prompt_gen_prompt",
                        "image_model": "prompt_image_model",
                        "images": "prompt_images",
                    },
                    output="judge_result",
                    call_output="judge_call",
                    error_output="judge_error",
                    when=lambda r: r["status"] == "generated",
                    concurrency=1,
                    queue_depth=1,
                )
                .map(partial(apply_judge, run=run, config=config))
                .map(partial(to_stage_row, stage='scores', upstream_identity=files.previous, migrated_us=0))
                .materialize()
                .write_lance(uri, mode=write_mode, schema=PIPELINE_STAGE_ROWS)
            )
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'scores' + "/" + version, entry)
        files.stages['scores'] = entry
        (files.reused if reused else files.new).append('scores')
        files.previous = {"stage": 'scores', "stage_version": version}
        scored = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "scores":
            return files.finish()
        # 先按 V1 原评分规则汇总，再写单行 summary；不重新读取最新输入版本。
        summary_value = aggregate(scored.take_all())
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'summary',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('summary' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'summary' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                data.from_items([summary_value]).map(
                    partial(to_stage_row, stage='summary', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode='overwrite', schema=PIPELINE_STAGE_ROWS)
            committed = lance.dataset(uri)
            ref = DatasetRef(
                relative[:-6],
                relative,
                committed.version,
                files.schema_name,
                files.schema_version,
                schema_hash(PIPELINE_STAGE_ROWS),
                committed.count_rows(),
            )
            Catalog(files.storage_root).register(ref)
            entry = {"version": version, "dataset_ref": ref.to_dict()}
            files.records.put("stage/" + 'summary' + "/" + version, entry)
        files.stages['summary'] = entry
        (files.reused if reused else files.new).append('summary')
        files.previous = {"stage": 'summary', "stage_version": version}
        summary = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        return files.finish()


def main():
    parser = argparse.ArgumentParser(
        description='V1 T2I evaluation: image-model answers and the v6.0-V2 judge (demiflow Python pipeline)'
    )
    parser.add_argument('--run', required=True, type=Path, help='evaluation/t2i/v1/datasets/<run>')
    parser.add_argument('--target', help='最终结果表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    parser.add_argument(
        '--questions',
        required=True,
        type=historical_input,
        help='questions JSONL (historical bench200 format); explicit import boundary',
    )
    parser.add_argument(
        '--responses',
        type=historical_input,
        default=None,
        help='import historical responses JSONL instead of calling image models',
    )
    parser.add_argument(
        '--models', nargs='*', default=[], help='image models via the gateway (required for online generation)'
    )
    parser.add_argument('--endpoint', default='http://127.0.0.1:4001/v1')
    parser.add_argument('--gen-mode', choices=['auto', 'chat', 'images'], default='auto')
    parser.add_argument(
        '--mode',
        choices=['offline', 'online', 'local'],
        default='offline',
        help='offline prepares/binds only; online calls the image gateway; local calls the local judge',
    )
    parser.add_argument(
        '--through', choices=['questions', 'answers', 'judge_requests', 'scores', 'summary'], default='summary'
    )
    parser.add_argument('--judge-model', help='Judge identity for offline binding records')
    parser.add_argument('--cond', default='V2', help='judge prompt condition (v6.0-<cond>)')
    parser.add_argument('--max-edge', type=int, default=1024)
    parser.add_argument('--max-calls', type=int, default=100)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.mode == 'online' and not args.models and not args.responses:
        parser.error('--mode online needs --models (or imported --responses)')
    result = run_pipeline(
        args.run,
        args.questions,
        config(
            args.mode,
            args.models,
            judge_model=args.judge_model,
            endpoint=args.endpoint,
            gen_mode=args.gen_mode,
            cond=args.cond,
            max_edge=args.max_edge,
            max_calls=args.max_calls,
            timeout=args.timeout,
            limit=args.limit or None,
        ),
        through=args.through,
        responses=args.responses,
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
