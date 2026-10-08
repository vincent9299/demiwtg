"""本 pipeline 的运行摘要与追加提交表；字段和固定结果版本由业务定义。

摘要一运行一行；提交一 purpose/fingerprint 一行。历史 records 只读，
新写入只走 Dataset 的显式 schema，模型调用由 SQLite 原生日志负责。
"""
from pathlib import Path
import json
import pyarrow as pa
from demiflow import data
from demiflow.execution.artifacts import immutable, read
from demiflow.lance.legacy import read_legacy_record

TABLE_REF = pa.struct([('uri', pa.string()), ('version', pa.int64())])
SUMMARY = pa.schema([
    ('calls_json', pa.large_string()),
    ('complete', pa.bool_()),
    ('counts_json', pa.large_string()),
    ('counts_by_arm_json', pa.large_string()),
    ('arms_json', pa.large_string()),
    ('phase', pa.string()),
    ('expected_questions', pa.int64()),
    ('expected_answers', pa.int64()),
    ('outputs_json', pa.large_string()),
    ('target', TABLE_REF),
    ('present_fields', pa.list_(pa.string())),
])
JSON_FIELDS = frozenset({'calls', 'counts', 'counts_by_arm', 'arms', 'outputs'})
COMMITS = pa.schema([('purpose', pa.string()), ('fingerprint', pa.string()),
                     ('uri', pa.string()), ('version', pa.int64())])


class RunTables:
    def __init__(self, root, relative_uri):
        self.root, self.legacy_uri = Path(root), str(relative_uri)
        old = self.root / relative_uri
        suffix = old.name.removeprefix('records__')
        self.summary_uri = str(old.with_name('summary__' + suffix))
        self.commits_uri = str(old.with_name('output_commits__' + suffix))
        self.manifest_path = self.root / '_demiflow' / 'run_manifests' / Path(relative_uri).with_suffix('.json')

    def load(self):
        if not Path(self.summary_uri).exists():
            return read_legacy_record(self.root, self.legacy_uri, 'state')
        row = data.read_lance(self.summary_uri).take(1)[0]
        return {name: json.loads(row[name + '_json']) if name in JSON_FIELDS else row[name]
                for name in row['present_fields']}

    def save(self, state):
        known = {name.removesuffix('_json') if name in {f + '_json' for f in JSON_FIELDS} else name
                 for name in SUMMARY.names} - {'present_fields'}
        if set(state) - known:
            raise ValueError('Unknown summary fields: ' + str(set(state) - known))
        row = {name + '_json' if name in JSON_FIELDS else name:
               json.dumps(value, ensure_ascii=False, sort_keys=True) if name in JSON_FIELDS else value
               for name, value in state.items()}
        row['present_fields'] = sorted(state)
        data.from_arrow(pa.Table.from_pylist([row], schema=SUMMARY)).write_lance(
            self.summary_uri, mode='overwrite', schema=SUMMARY)

    def find_commit(self, purpose, fingerprint):
        if Path(self.commits_uri).exists():
            rows = data.read_lance(self.commits_uri).filter(lambda r: r['purpose'] == purpose and r['fingerprint'] == fingerprint).take(1)
            if rows:
                return {k: rows[0][k] for k in ('uri', 'version')}
        return read_legacy_record(self.root, self.legacy_uri, purpose + '/' + fingerprint)

    def save_commit(self, purpose, fingerprint, output):
        prior = self.find_commit(purpose, fingerprint)
        if prior is not None:
            if prior != output:
                raise ValueError('Append commit differs')
            return
        data.from_items([{'purpose': purpose, 'fingerprint': fingerprint, **output}]).write_lance(
            self.commits_uri, mode='append', schema=COMMITS)

    def load_manifest(self):
        return read(self.manifest_path) if self.manifest_path.exists() else read_legacy_record(self.root, self.legacy_uri, 'manifest')

    def save_manifest(self, manifest):
        prior = self.load_manifest()
        if prior is not None and prior != manifest:
            raise ValueError('Immutable manifest differs; use a new run')
        immutable(self.manifest_path, manifest)


    def _answer_uri(self):
        old = self.root / self.legacy_uri
        return str(old.with_name('answer_results__' + old.name.removeprefix('records__')))

    def answer(self, identity):
        uri = self._answer_uri()
        if Path(uri).exists():
            rows = data.read_lance(uri).filter(lambda row: row['request_id'] == identity).take(1)
            if rows:
                return {k:v for k,v in rows[0].items() if k != 'request_id'}
        return read_legacy_record(self.root, self.legacy_uri, 'answer/' + identity)

    def save_answer(self, identity, result):
        schema = pa.schema([('request_id', pa.string()), ('task_id', pa.string()), ('concept', pa.string()),
            ('instruction', pa.large_string()), ('answer_model', pa.string()), ('answer_mode', pa.string()),
            ('reference_images_json', pa.large_string()), ('reference_image_count', pa.int64()), ('status', pa.string()),
            ('reason', pa.large_string()), ('image_json', pa.large_string()), ('generation_seconds', pa.float64()), ('answer_call_json', pa.large_string())])
        previous = self.answer(identity)
        if previous is not None:
            if previous != result:
                raise ValueError('Answer result differs')
            return
        data.from_arrow(pa.Table.from_pylist([{'request_id':identity, **result}], schema=schema)).write_lance(
            self._answer_uri(), mode='append', schema=schema)

    def answer_request(self, identity):
        path = self.manifest_path.parent / self.manifest_path.stem / 'answer_requests' / (identity + '.json')
        return read(path) if path.exists() else read_legacy_record(self.root, self.legacy_uri, 'answer/' + identity + '/request')

    def start_answer(self, identity, request):
        from demiflow.execution.file_ref import save_json_artifact
        path = self.manifest_path.parent / self.manifest_path.stem / 'answer_requests' / (identity + '.json')
        ref = save_json_artifact(path.parent / 'artifacts', request)
        immutable(path, ref.to_dict())

    def answer_response(self, identity, response):
        from demiflow.execution.file_ref import save_json_artifact
        return save_json_artifact(self.manifest_path.parent / self.manifest_path.stem / 'answer_responses' / identity, response)
