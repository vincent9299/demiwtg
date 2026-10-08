"""Edit 训练的双目标提交与运行摘要；每个输出指纹一行，模型日志独立保存。"""
from pathlib import Path
import json
import pyarrow as pa
from demiflow import data
from demiflow.execution.artifacts import immutable, read
from demiflow.lance.legacy import read_legacy_record

OUTPUTS = pa.schema([('fingerprint', pa.string()), ('questions_uri', pa.string()),
    ('training_samples_uri', pa.string()), ('version', pa.int64()), ('training_samples_version', pa.int64()),
    ('summaries_json', pa.large_string()), ('stage', pa.string())])
COMMITS = pa.schema([('kind', pa.string()), ('fingerprint', pa.string()), ('version', pa.int64())])


class TrainingRunTables:
    def __init__(self, root, relative_uri):
        self.root, self.legacy = Path(root), str(relative_uri)
        old = self.root / relative_uri
        suffix = old.name.removeprefix('records__').removeprefix('metadata__')
        self.output_uri = str(old.with_name('outputs__' + suffix))
        self.summary_uri = str(old.with_name('summary__' + suffix))
        self.commit_uri = str(old.with_name('output_commits__' + suffix))
        self.manifest_path = self.root / '_demiflow' / 'run_manifests' / Path(relative_uri).with_suffix('.json')

    def save_manifest(self, manifest):
        prior = read(self.manifest_path) if self.manifest_path.exists() else read_legacy_record(self.root, self.legacy, 'manifest')
        if prior is not None and prior != manifest:
            raise ValueError('Immutable manifest differs; use a new run')
        immutable(self.manifest_path, manifest)

    @staticmethod
    def _decode(row):
        return {**{k:v for k,v in row.items() if k not in ('fingerprint', 'summaries_json')},
                'summaries': json.loads(row['summaries_json'])}

    def output(self, fingerprint):
        if Path(self.output_uri).exists():
            rows = data.read_lance(self.output_uri).filter(lambda r:r['fingerprint']==fingerprint).take(1)
            if rows:
                return self._decode(rows[0])
        return read_legacy_record(self.root, self.legacy, 'outputs/' + fingerprint)

    def save_output(self, fingerprint, result):
        prior = self.output(fingerprint)
        if prior is not None:
            if prior != result:
                raise ValueError('Training output differs')
            return
        row = {**{k:v for k,v in result.items() if k != 'summaries'}, 'fingerprint':fingerprint,
               'summaries_json':json.dumps(result['summaries'], ensure_ascii=False)}
        data.from_arrow(pa.Table.from_pylist([row], schema=OUTPUTS)).write_lance(self.output_uri, mode='append', schema=OUTPUTS)

    def finish(self, result):
        row = {**{k:v for k,v in result.items() if k != 'summaries'}, 'fingerprint':'latest',
               'summaries_json':json.dumps(result['summaries'], ensure_ascii=False)}
        data.from_arrow(pa.Table.from_pylist([row], schema=OUTPUTS)).write_lance(self.summary_uri, mode='overwrite', schema=OUTPUTS)

    def commit(self, kind, fingerprint):
        if kind not in ('question_output','sample_output'):
            raise ValueError('Unknown training output kind')
        if Path(self.commit_uri).exists():
            rows=data.read_lance(self.commit_uri).filter(lambda r:r['kind']==kind and r['fingerprint']==fingerprint).take(1)
            if rows:
                return {'version':rows[0]['version']}
        return read_legacy_record(self.root,self.legacy,kind+'/'+fingerprint)

    def save_commit(self, kind, fingerprint, output):
        prior=self.commit(kind,fingerprint)
        if prior is not None:
            if prior!=output:
                raise ValueError('Training commit differs')
            return
        data.from_items([{'kind':kind,'fingerprint':fingerprint,'version':output['version']}]).write_lance(
            self.commit_uri,mode='append',schema=COMMITS)
