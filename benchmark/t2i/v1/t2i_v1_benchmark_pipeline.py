"""从固定输入读取出题单位，展开构题请求、检查响应，按配置写基准题表。"""

from benchmark.t2i.v1.operaters.transforms import (
    expand_author_models,
    bind_question_metadata,
)

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
from benchmark.t2i.v1.operaters.runfiles import T2IAuthoringRunFiles, graph_digest
from functools import partial
from demiflow import data
from demiflow.execution.artifacts import run_lock
from benchmark.t2i.v1.operaters.runfiles import fail
from benchmark.t2i.v1.operaters.prompting import prompt_config, prepare_synth, apply_synth, prompt_responses
from benchmark.t2i.v1.operaters.audit import AuditSynthesized

DEFAULT_MODEL = {
    "base_url": "http://127.0.0.1:8000/v1",
    "name": "qwen3.8-27b",
    "max_output_tokens": 16384,
    "timeout_s": 600,
}


def config(
    mode, models, *, author_model=None, temperature=0.4, max_calls=100, mix=None, target_levels=None, limit=None
):
    if not models:
        raise ValueError('At least one authoring model is required')
    if any(level not in ('L1', 'L2', 'L3') for level in (target_levels or [])):
        raise ValueError('target levels use L1/L2/L3')
    if mode == 'local' and author_model not in {None, DEFAULT_MODEL['name']}:
        raise ValueError('Local transport uses the configured local Qwen, not an offline author')
    return {
        'schema': 'v6.0',
        'mode': mode,
        'models': list(models),
        'mix': mix,
        'target_levels': list(target_levels or []),
        'limit': limit,
        'temperature': temperature,
        'author': {
            'backend': mode,
            'model': author_model or (DEFAULT_MODEL['name'] if mode == 'local' else 'external'),
        },
        'model': {**DEFAULT_MODEL, 'max_calls': max_calls},
    }


def run_pipeline(run, samples, config, through="export", *, target_uri=None, write_mode='overwrite'):
    """保留 V1 处理协议，最终结果表按 write_mode 追加或覆盖；中间阶段仍按运行保存。"""
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        files = T2IAuthoringRunFiles(run, config, graph_digest(), samples)
        # 目标与模式随运行冻结，完成的输出在续跑时不重复写入。
        target_uri = str((files.storage_root / target_uri).resolve()) if target_uri else None
        files.records.put('output', {'uri': target_uri, 'write_mode': write_mode})
        pack, options = prompt_config(run, config)
        samples_stage = data.from_items(
            [
                {
                    'task_id': 'unit_' + str(row.get('sample_id') or f's{index:04d}'),
                    'sample_id': str(row.get('sample_id') or f's{index:04d}'),
                    'instance': row.get('instance') or '',
                    'mount_paths': list(row.get('mount_paths') or []),
                    'main_domain': row.get('l1') or row.get('main_domain'),
                    'image_ref': row.get('image'),
                    'seq': index - 1,
                    'branch': 'benchmark',
                    'status': 'sample_ready',
                }
                for index, row in enumerate(
                    files.samples['rows'][: config['limit']] if config.get('limit') else files.samples['rows'],
                    1,
                )
            ]
        ).map(
            lambda row: row if row['instance'] else fail(row, 'invalid_sample', 'sample has no instance name')
        )
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'samples',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('samples' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'samples' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                samples_stage.map(
                    partial(to_stage_row, stage='samples', upstream_identity=files.previous, migrated_us=0)
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
            files.records.put("stage/" + 'samples' + "/" + version, entry)
        files.stages['samples'] = entry
        (files.reused if reused else files.new).append('samples')
        files.previous = {"stage": 'samples', "stage_version": version}
        samples_stage = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "samples":
            return files.finish()
        # 按作者模型展开：每个有效样本×模型一行；采样图片只作来源记录，不发给作者。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'jobs',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('jobs' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'jobs' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                samples_stage.flat_map(partial(expand_author_models, config=config)).map(
                    partial(to_stage_row, stage='jobs', upstream_identity=files.previous, migrated_us=0)
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
            files.records.put("stage/" + 'jobs' + "/" + version, entry)
        files.stages['jobs'] = entry
        (files.reused if reused else files.new).append('jobs')
        files.previous = {"stage": 'jobs', "stage_version": version}
        jobs = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        # 每行只组装一个样本×作者的请求；V1 仅发送概念、分类路径及出题配置。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'requests',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('requests' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'requests' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                jobs.map(partial(prepare_synth, run=run, pack=pack, config=config)).map(
                    partial(to_stage_row, stage='requests', upstream_identity=files.previous, migrated_us=0)
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
            files.records.put("stage/" + 'requests' + "/" + version, entry)
        files.stages['requests'] = entry
        (files.reused if reused else files.new).append('requests')
        files.previous = {"stage": 'requests', "stage_version": version}
        requests = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "requests":
            return files.finish()
        # 只对 ready_to_author 行串行请求；响应经字段投影和物化后写入阶段表。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'synthesize',
                "extra": prompt_responses(run, "synthesize"),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('synthesize' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'synthesize' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                requests.map_prompt_async(
                    "synthesize",
                    config=pack,
                    options=options,
                    max_requests=config['model']['max_calls'],
                    inputs={"payload": "prompt_payload", "images": "prompt_images"},
                    output="synthesize_result",
                    call_output="synthesize_call",
                    error_output="synthesize_error",
                    when=lambda r: r["status"] == "ready_to_author",
                    concurrency=1,
                    queue_depth=1,
                )
                .map(partial(apply_synth, run=run))
                .map(partial(to_stage_row, stage='synthesize', upstream_identity=files.previous, migrated_us=0))
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
            files.records.put("stage/" + 'synthesize' + "/" + version, entry)
        files.stages['synthesize'] = entry
        (files.reused if reused else files.new).append('synthesize')
        files.previous = {"stage": 'synthesize', "stage_version": version}
        designed = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "synthesize":
            return files.finish()
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'audit',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('audit' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'audit' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                designed.map(AuditSynthesized(config)).map(
                    partial(to_stage_row, stage='audit', upstream_identity=files.previous, migrated_us=0)
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
            files.records.put("stage/" + 'audit' + "/" + version, entry)
        files.stages['audit'] = entry
        (files.reused if reused else files.new).append('audit')
        files.previous = {"stage": 'audit', "stage_version": version}
        audited = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "audit":
            return files.finish()
        # 通过原 V1 结构审核的结果追加来源字段；审核规则及历史 question 内容保持不变。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'export',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('export' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'export' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                audited.map(
                    lambda row: {
                        **row,
                        'export_ready': row['status']
                        in ('audited', 'cannot_construct_audited', 'rejected_audited'),
                    }
                )
                .map(bind_question_metadata)
                .map(
                    lambda row: (
                        {**row, 'question_sha256': digest(row['question'])} if row['export_ready'] else row
                    )
                )
                .map(partial(to_stage_row, stage='export', upstream_identity=files.previous, migrated_us=0))
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
            files.records.put("stage/" + 'export' + "/" + version, entry)
        files.stages['export'] = entry
        (files.reused if reused else files.new).append('export')
        files.previous = {"stage": 'export', "stage_version": version}
        exported = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
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
        if target_uri:
            relative = str(Path(target_uri).relative_to(files.storage_root))
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'questions' + "/" + version)
        reused = entry is not None
        if not reused:
            if not target_uri and Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                exported.filter(lambda r: r.get("export_ready")).map(
                    partial(to_stage_row, stage='questions', upstream_identity=files.previous, migrated_us=0)
                )
            ).write_lance(uri, mode=write_mode, schema=PIPELINE_STAGE_ROWS)
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
        questions = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'gaps',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('gaps' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'gaps' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                exported.filter(lambda r: not r.get("export_ready")).map(
                    partial(to_stage_row, stage='gaps', upstream_identity=files.previous, migrated_us=0)
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
            files.records.put("stage/" + 'gaps' + "/" + version, entry)
        files.stages['gaps'] = entry
        (files.reused if reused else files.new).append('gaps')
        files.previous = {"stage": 'gaps', "stage_version": version}
        gaps = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        return files.finish()


def main():
    parser = argparse.ArgumentParser(
        description='V1 T2I benchmark construction (v6.0 entry-item protocol, demiflow Python pipeline)'
    )
    parser.add_argument('--run', required=True, type=Path, help='benchmark/t2i/v1/datasets/<run>')
    parser.add_argument('--target', help='最终结果表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    parser.add_argument(
        '--samples',
        required=True,
        type=historical_input,
        help='samples JSONL (sample_id/instance/mount_paths/l1); explicit import boundary',
    )
    parser.add_argument(
        '--models', nargs='+', required=True, help='authoring model names as passed to the gateway'
    )
    parser.add_argument(
        '--mode',
        choices=['offline', 'local'],
        default='offline',
        help='offline binds prepared requests; local calls the local endpoint',
    )
    parser.add_argument(
        '--through', choices=['samples', 'jobs', 'requests', 'synthesize', 'audit', 'export'], default='export'
    )
    parser.add_argument(
        '--mix', default=None, help='entry quota as L1:1,L2:3,L3:6 (same syntax as the historical batch)'
    )
    parser.add_argument(
        '--target-levels', nargs='*', default=None, help='cycling target levels per sample order (L1/L2/L3)'
    )
    parser.add_argument('--author-model', help='Offline author identity recorded in the run')
    parser.add_argument('--temperature', type=float, default=0.4)
    parser.add_argument('--max-calls', type=int, default=100)
    parser.add_argument('--limit', type=int, default=0, help='use only the first N samples')
    args = parser.parse_args()
    mix = None
    if args.mix:
        mix = {}
        for part in args.mix.split(','):
            key, _, value = part.partition(':')
            mix[key.strip()] = int(value)
    result = run_pipeline(
        args.run,
        args.samples,
        config(
            args.mode,
            args.models,
            author_model=args.author_model,
            temperature=args.temperature,
            max_calls=args.max_calls,
            mix=mix,
            target_levels=args.target_levels,
            limit=args.limit or None,
        ),
        through=args.through,
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
