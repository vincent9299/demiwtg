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
    ('phase', pa.string()),
    ('calls_json', pa.large_string()),
    ('candidate_count', pa.int64()),
    ('candidates', TABLE_REF),
    ('complete', pa.bool_()),
    ('counts_json', pa.large_string()),
    ('designs', TABLE_REF),
    ('inputs', TABLE_REF),
    ('selection_json', pa.large_string()),
    ('sources_json', pa.large_string()),
    ('probe_json', pa.large_string()),
    ('question_review_json', pa.large_string()),
    ('present_fields', pa.list_(pa.string())),
])
JSON_FIELDS = frozenset({'sources', 'calls', 'selection', 'counts', 'probe', 'question_review'})
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

    def load(self, *, version=None):
        if not Path(self.summary_uri).exists():
            return read_legacy_record(self.root, self.legacy_uri, 'state', version=version)
        row = data.read_lance(self.summary_uri, version=version).take(1)[0]
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
