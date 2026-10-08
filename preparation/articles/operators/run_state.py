"""材料阶段协议的业务状态：显式阶段/摘要/发布 schema，控制日志用普通文件。

文章 producer 维护阶段契约；图片审核、训练和旧版评测复用该契约。
没有任意 key→JSON 的读写入口。模型上下文绑定使用具名文件和内容摘要。
"""
from pathlib import Path
import json
import pyarrow as pa
from demiflow import data
from demiflow.execution.artifacts import digest, immutable, read, run_lock
from demiflow.execution.file_ref import JsonArtifactRef, save_json_artifact
from demiflow.lance.legacy import read_legacy_record, scan_legacy_records

DATASET_REF = pa.struct([
    ('dataset_id', pa.string()), ('store_id', pa.string()), ('relative_uri', pa.string()),
    ('lance_version', pa.int64()), ('schema_name', pa.string()), ('schema_version', pa.string()),
    ('schema_hash', pa.string()), ('row_count', pa.int64()), ('content_digest', pa.string()),
])
STAGE_ENTRY = pa.struct([('name', pa.string()), ('revision', pa.string()), ('version', pa.string()),
    ('fingerprint', pa.string()), ('reused_from', pa.struct([('run', pa.string()), ('manifest_sha256', pa.string())])), ('dataset_ref', DATASET_REF), ('has_version', pa.bool_()), ('has_fingerprint', pa.bool_())])
STAGES = pa.schema(list(STAGE_ENTRY))
SUMMARY = pa.schema([('branch', pa.string()), ('stages', pa.list_(STAGE_ENTRY)),
                     ('reused_stages', pa.list_(pa.string())), ('new_stages', pa.list_(pa.string()))])
PUBLICATIONS = pa.schema([('kind', pa.string()), ('fingerprint', pa.string()), ('reused_from', pa.struct([('run', pa.string()), ('manifest_sha256', pa.string())])), ('dataset_ref', DATASET_REF),
    ('version', pa.int64()), ('binding_json', pa.large_string()), ('release_id', pa.string()), ('present_fields', pa.list_(pa.string()))])


def _stage_row(name, revision, entry):
    unknown = set(entry) - {'version', 'fingerprint', 'dataset_ref', 'reused_from'}
    if unknown:
        raise ValueError('Unknown stage fields: ' + str(unknown))
    return {'name': name, 'revision': revision, 'has_version': 'version' in entry,
            'has_fingerprint': 'fingerprint' in entry, **entry}


def _stage_entry(row):
    return {'dataset_ref': row['dataset_ref'], **({'version': row['version']} if row['has_version'] else {}),
            **({'fingerprint': row['fingerprint']} if row['has_fingerprint'] else {}),
            **({'reused_from': row['reused_from']} if row.get('reused_from') else {})}


class MaterialRunState:
    def __init__(self, root, relative):
        self.root, self.relative = Path(root), Path(relative)
        self.legacy_uri = str(self.relative.parent / ('metadata__' + self.relative.name + '.lance'))
        self.directory = self.root / '_demiflow' / 'pipeline_runs' / self.relative
        self.stages_uri = str(self.root / self.relative.parent / ('stage_commits__' + self.relative.name + '.lance'))
        self.summary_uri = str(self.root / self.relative.parent / ('run_summary__' + self.relative.name + '.lance'))
        self.publications_uri = str(self.root / self.relative.parent / ('publications__' + self.relative.name + '.lance'))

    def load_manifest(self):
        path = self.directory / 'manifest.json'
        return read(path) if path.exists() else read_legacy_record(self.root, self.legacy_uri, 'manifest')

    def save_manifest(self, manifest):
        prior = self.load_manifest()
        if prior is not None and prior != manifest:
            raise ValueError('Immutable manifest differs; use a new run or explicitly reset an empty run')
        immutable(self.directory / 'manifest.json', manifest)

    def manifest_ref(self):
        return save_json_artifact(self.directory / 'manifest_history', self.load_manifest())

    def configuration(self, name):
        if name not in {'pipeline_source', 'prompt_config', 'judge_config', 'split_registry', 'output', 'image_filter_policy', 'rubrics_index'}:
            raise ValueError('Unknown run configuration field')
        path = self.directory / (name + '.json')
        return read(path) if path.exists() else read_legacy_record(self.root, self.legacy_uri, name)

    def save_configuration(self, name, value):
        previous = self.configuration(name)
        if previous is not None and previous != value:
            raise ValueError('Immutable run configuration differs: ' + name)
        immutable(self.directory / (name + '.json'), value)
        return save_json_artifact(self.directory / 'configuration_history', value)

    def stage(self, name, revision=''):
        if Path(self.stages_uri).exists():
            rows = data.read_lance(self.stages_uri).filter(lambda row: row['name'] == name and row['revision'] == revision).take(1)
            if rows:
                return _stage_entry(rows[0])
        return read_legacy_record(self.root, self.legacy_uri, 'stage/' + name + ('/' + revision if revision else ''))

    def commit_stage(self, name, entry, revision='', *, immutable=True):
        with run_lock(self.directory / 'stage_writer'):
            prior = self.stage(name, revision)
            if prior is not None:
                if prior == entry:
                    return
                if immutable:
                    raise ValueError('Immutable stage differs: ' + name)
            row = _stage_row(name, revision, entry)
            ds = data.from_arrow(pa.Table.from_pylist([row], schema=STAGES))
            if Path(self.stages_uri).exists():
                ds.write_lance(self.stages_uri, mode='merge', on=['name', 'revision'], when_not_matched='insert')
            else:
                ds.write_lance(self.stages_uri, mode='overwrite', schema=STAGES)

    def load(self):
        if not Path(self.summary_uri).exists():
            return read_legacy_record(self.root, self.legacy_uri, 'latest')
        row = data.read_lance(self.summary_uri).take(1)[0]
        return {**row, 'stages': {stage['name']: _stage_entry(stage) for stage in row['stages']}}

    def finish(self, state):
        row = {**state, 'stages': [_stage_row(name, '', entry) for name, entry in state['stages'].items()]}
        data.from_arrow(pa.Table.from_pylist([row], schema=SUMMARY)).write_lance(self.summary_uri, mode='overwrite', schema=SUMMARY)

    def publication(self, kind, fingerprint=''):
        if Path(self.publications_uri).exists():
            rows = data.read_lance(self.publications_uri).filter(lambda r: r['kind'] == kind and r['fingerprint'] == fingerprint).take(1)
            if rows:
                row = rows[0]
                return {name: json.loads(row['binding_json']) if name == 'binding' else row[name] for name in row['present_fields']}
        key = kind + ('/' + fingerprint if fingerprint else '')
        return read_legacy_record(self.root, self.legacy_uri, key)

    def publish(self, kind, value, fingerprint=''):
        allowed = {'dataset_ref', 'version', 'binding', 'release_id'}
        if set(value) - allowed:
            raise ValueError('Unknown publication fields: ' + str(set(value) - allowed))
        with run_lock(self.directory / 'publication_writer'):
            prior = self.publication(kind, fingerprint)
            if prior is not None:
                if prior != value:
                    raise ValueError('Publication differs')
                return
            row = {'kind': kind, 'fingerprint': fingerprint, 'present_fields': sorted(value),
                   **{k: v for k, v in value.items() if k != 'binding'},
                   'binding_json': json.dumps(value['binding'], ensure_ascii=False) if 'binding' in value else None}
            data.from_arrow(pa.Table.from_pylist([row], schema=PUBLICATIONS)).write_lance(self.publications_uri, mode='append', schema=PUBLICATIONS)

    def bind_request(self, stage, task_id, input_sha256, request):
        # Identity fields stay explicit; input files are externalized, not copied as base64.
        location = self.directory / 'requests' / digest([stage, task_id, input_sha256])
        ref = save_json_artifact(location, request)
        immutable(location / 'binding.json', {'stage': stage, 'task_id': task_id, 'input_sha256': input_sha256, 'ref': ref.to_dict()})
        return ref

    def request_bindings(self, stage=None, task_prefix=None):
        result = []
        for path in (self.directory / 'requests').glob('*/binding.json'):
            binding = read(path)
            if (stage is None or binding['stage'] == stage) and (task_prefix is None or binding['task_id'].startswith(task_prefix)):
                result.append(JsonArtifactRef(**binding['ref']).read())
        legacy = self.root / self.legacy_uri
        if legacy.exists():
            import lance
            for key, value, _ in scan_legacy_records(self.root, self.legacy_uri, version=lance.dataset(str(legacy)).version, prefix='request/' + (stage + '/' if stage else '')):
                if task_prefix is None or value['task_id'].startswith(task_prefix):
                    if not any(r['input_sha256'] == value['input_sha256'] and r['task_id'] == value['task_id'] for r in result):
                        result.append(value)
        return result

    def record_response(self, stage, task_id, fingerprint, response):
        return save_json_artifact(self.directory / 'responses' / digest([stage, task_id, fingerprint]), response)

    def record_raw_response(self, response):
        return save_json_artifact(self.directory / 'raw_responses', response)

    def observe_asset(self, identity, record):
        immutable(self.directory / 'observed_assets' / (digest(identity) + '.json'), record)

    def observed_assets(self):
        result = {p.stem: read(p) for p in (self.directory / 'observed_assets').glob('*.json')}
        legacy = self.root / self.legacy_uri
        if legacy.exists():
            import lance
            result.update({key: value for key, value, _ in scan_legacy_records(self.root, self.legacy_uri,
                version=lance.dataset(str(legacy)).version, prefix='observed_asset/')})
        return result

    def generation(self, task_id, component):
        if component not in {'attempt', 'result', 'http_request', 'http_body', 'http_response', 'native_input'}:
            raise ValueError('Unknown generation component')
        path = self.directory / 'generations' / digest(task_id) / (component + '.json')
        if path.exists():
            return JsonArtifactRef(**read(path)).read()
        return read_legacy_record(self.root, self.legacy_uri, 'generation/' + task_id + '/' + component)

    def record_generation(self, task_id, component, value):
        previous = self.generation(task_id, component)
        if previous is not None and previous != value:
            raise ValueError('Immutable generation record differs')
        ref = save_json_artifact(self.directory / 'generation_artifacts', value)
        immutable(self.directory / 'generations' / digest(task_id) / (component + '.json'), ref.to_dict())
        return ref

    def record_environment(self, backend, value):
        return save_json_artifact(self.directory / 'environments' / digest(backend), value)
