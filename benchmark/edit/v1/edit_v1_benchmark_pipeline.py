"""从固定输入读取出题单位，展开构题请求、检查响应，按配置写基准题表。"""

from benchmark.edit.v1.operaters.transforms import (
    make_edit_job,
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
from functools import partial
from demiflow import data
from demiflow.execution.artifacts import run_lock
from benchmark.edit.v1.operaters.runfiles import graph_digest, EditAuthoringRunFiles
from benchmark.edit.v1.operaters.plan import read_plan
from benchmark.edit.v1.operaters.prompting import (
    prompt_config,
    prepare_construct,
    apply_construct,
    prompt_responses,
)
from benchmark.edit.v1.operaters.audit import AuditConstruct

DEFAULT_MODEL = {
    "base_url": "http://127.0.0.1:8000/v1",
    "name": "qwen3.8-27b",
    "max_output_tokens": 16384,
    "timeout_s": 600,
}


def config(mode, *, author_model=None, temperature=0.4, max_calls=100, limit=None, attempt=0):
    if mode == 'local' and author_model not in {None, DEFAULT_MODEL['name']}:
        raise ValueError('Local transport uses the configured local Qwen, not an offline author')
    return {
        'mode': mode,
        'temperature': temperature,
        'limit': limit,
        'attempt': attempt,
        'author': {
            'backend': mode,
            'model': author_model or (DEFAULT_MODEL['name'] if mode == 'local' else 'external'),
        },
        'model': {**DEFAULT_MODEL, 'max_calls': max_calls},
    }


def run_pipeline(run, plan, config, through="export", *, target_uri=None, write_mode='overwrite'):
    """保留 V1 处理协议，最终结果表按 write_mode 追加或覆盖；中间阶段仍按运行保存。"""
    if write_mode not in {'append', 'overwrite'}:
        raise ValueError('write_mode must be append or overwrite')
    with run_lock(Path(run).parent / '_demiflow' / Path(run).name):
        files = EditAuthoringRunFiles(run, config, graph_digest(), plan)
        # 目标与模式随运行冻结，完成的输出在续跑时不重复写入。
        target_uri = str((files.storage_root / target_uri).resolve()) if target_uri else None
        files.records.put('output', {'uri': target_uri, 'write_mode': write_mode})
        pack, options = prompt_config(run, config)
        # 每条冻结计划先解析源图，保存缺少图文或找不到图片的原因；不自动补计划。
        plan_stage = data.from_iter(lambda: read_plan(files))
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'plan',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('plan' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'plan' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                plan_stage.map(
                    partial(to_stage_row, stage='plan', upstream_identity=files.previous, migrated_us=0)
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
            files.records.put("stage/" + 'plan' + "/" + version, entry)
        files.stages['plan'] = entry
        (files.reused if reused else files.new).append('plan')
        files.previous = {"stage": 'plan', "stage_version": version}
        plan_stage = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "plan":
            return files.finish()
        # 每段计划文本绑定 image_index 指定的一张源图；越界保留失败行，不擅自换图。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'dispatch',
                "extra": None,
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('dispatch' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'dispatch' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                plan_stage.flat_map(
                    lambda row: (
                        [
                            {**row, 'text_entry': text, 'image_index': int(text.get('image_index', 0))}
                            for text in row['plan_texts']
                        ]
                        if row['status'] == 'plan_ready'
                        else [row]
                    )
                )
                .map(
                    lambda row: (
                        {
                            **row,
                            'task_id': row['qid'] + '_bad_index',
                            'status': 'invalid_plan_row',
                            'fail_reason': f"text image_index {row['image_index']} outside plan images",
                        }
                        if row['status'] == 'plan_ready'
                        and not 0 <= row['image_index'] < len(row['plan_images'])
                        else row
                    )
                )
                .map(
                    lambda row: (
                        {
                            **row,
                            'job_id': f"{row['qid']}_a{config.get('attempt', 0)}_{row['image_index']}_{row['text_entry'].get('edit_type', row['edit_type'])}",
                        }
                        if row['status'] == 'plan_ready'
                        else row
                    )
                )
                .map(partial(make_edit_job, config=config))
                .map(
                    lambda row: {
                        key: value for key, value in row.items() if key not in ('text_entry', 'image_index')
                    }
                )
                .map(partial(to_stage_row, stage='dispatch', upstream_identity=files.previous, migrated_us=0))
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
            files.records.put("stage/" + 'dispatch' + "/" + version, entry)
        files.stages['dispatch'] = entry
        (files.reused if reused else files.new).append('dispatch')
        files.previous = {"stage": 'dispatch', "stage_version": version}
        jobs = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        # 每个作业发送绑定源图和对应编辑文本；请求字段及原协议由既有模板生成。
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
                jobs.map(partial(prepare_construct, run=run, pack=pack, config=config)).map(
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
        # 每行一次构题请求，失败/待响应仍落表；下游只审核成功解析的结果。
        version = digest(
            {
                "upstream": files.previous,
                "stage": 'construct',
                "extra": prompt_responses(run, "construct"),
                "implementation": files.manifest.get("implementation"),
            }
        )
        relative = str(
            Path(files.relative).parent
            / ('construct' + '__' + Path(files.relative).name + '__' + version + '.lance')
        )
        uri = str(files.storage_root / relative)
        entry = files.records.get("stage/" + 'construct' + "/" + version)
        reused = entry is not None
        if not reused:
            if Path(uri).exists():
                raise ValueError("Unfinished stage table; inspect it before retrying: " + uri)
            (
                requests.map_prompt_async(
                    "construct",
                    config=pack,
                    options=options,
                    max_requests=config['model']['max_calls'],
                    inputs={"payload": "prompt_payload", "images": "prompt_images"},
                    output="construct_result",
                    call_output="construct_call",
                    error_output="construct_error",
                    when=lambda r: r["status"] == "ready_to_author",
                    concurrency=1,
                    queue_depth=1,
                )
                .map(partial(apply_construct, run=run))
                .map(partial(to_stage_row, stage='construct', upstream_identity=files.previous, migrated_us=0))
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
            files.records.put("stage/" + 'construct' + "/" + version, entry)
        files.stages['construct'] = entry
        (files.reused if reused else files.new).append('construct')
        files.previous = {"stage": 'construct', "stage_version": version}
        constructed = data.read_lance(uri, version=entry["dataset_ref"]["lance_version"]).map(from_stage_row)
        if through == "construct":
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
                constructed.map(AuditConstruct()).map(
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
                    partial(to_stage_row, stage='export', upstream_identity=files.previous, migrated_us=0)
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
                exported.filter(lambda r: r["status"] == "audited").map(
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
                exported.filter(lambda r: r["status"] != "audited").map(
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
        description='V1 Edit benchmark construction (v6.1 image-first protocol, demiflow Python pipeline)'
    )
    parser.add_argument('--run', required=True, type=Path, help='benchmark/edit/v1/datasets/<run>')
    parser.add_argument('--target', help='最终结果表路径')
    parser.add_argument(
        '--write-mode', choices=['append', 'overwrite'], default='overwrite', help='目标表追加或覆盖'
    )
    parser.add_argument(
        '--plan',
        required=True,
        type=historical_input,
        help='plan JSONL (batch policy + source images + request texts); explicit import boundary',
    )
    parser.add_argument('--mode', choices=['offline', 'local'], default='offline')
    parser.add_argument(
        '--through', choices=['plan', 'dispatch', 'requests', 'construct', 'audit', 'export'], default='export'
    )
    parser.add_argument('--author-model', help='Offline author identity recorded in the run')
    parser.add_argument('--temperature', type=float, default=0.4)
    parser.add_argument('--max-calls', type=int, default=100)
    parser.add_argument('--limit', type=int, default=0, help='use only the first N plan rows')
    parser.add_argument('--attempt', type=int, default=0, help='dispatch attempt index recorded in job ids')
    args = parser.parse_args()
    result = run_pipeline(
        args.run,
        args.plan,
        config(
            args.mode,
            author_model=args.author_model,
            temperature=args.temperature,
            max_calls=args.max_calls,
            limit=args.limit or None,
            attempt=args.attempt,
        ),
        through=args.through,
        target_uri=args.target,
        write_mode=args.write_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
