"""Edit 单题出题的运行绑定、固定来源与保留集约束；不执行 Dataset 或调用模型。"""
from pathlib import Path


from preparation.operaters import runfiles as storage
from preparation.operaters.inputs import SplitGuard, pixels
from demiflow.execution.artifacts import digest


class AuthoringRunFiles(storage.RunFiles):
    def __init__(self, run, knowledge_runs, branch, config, graph, visual_runs=None):
        if branch != 'benchmark' or config['task_types'] != ['edit']:
            raise ValueError('benchmark.edit.v2 requires benchmark edit configuration')
        if not storage.run_relative(run).startswith('demiwtg/benchmark/edit/v2/datasets/'):
            raise ValueError('Use benchmark/edit/v2/datasets/<run>')
        self.run = Path(run).resolve()
        from preparation.operaters.runfiles import check_run_location
        from project import resolve_root
        check_run_location(self.run, storage.ROOT, resolve_root())
        self.branch, self.config = branch, config
        from preparation.operaters.inputs import freeze_material_source, iter_material_rows
        def frozen(sources):
            unique = {digest(spec): spec for spec in map(freeze_material_source, sources)}
            return [unique[key] for key in sorted(unique)]
        self.knowledge_runs = frozen(knowledge_runs)
        self.visual_runs = frozen(visual_runs or [])
        if not self.knowledge_runs and not self.visual_runs:
            raise ValueError('At least one final publication is required')
        inputs = []
        for spec in self.knowledge_runs + self.visual_runs:
            _records, identity = iter_material_rows(spec)
            inputs.append(identity)
        self.knowledge_version = digest(inputs)
        self.manifest = {'schema': 'edit-v2-single-question/1', 'pipeline_version': 'V2', 'pipeline_module': 'benchmark.edit.v2', 'branch': branch,
            'knowledge_inputs': inputs, 'knowledge_runs': self.knowledge_runs,
            'visual_runs': self.visual_runs,
            'config': config, 'graph': graph, 'implementation': storage.code_version()}
        # Delivery integrity reads final published figures only. Previously
        # inspected scene/target bytes are rechecked as recorded dependencies.
        # 资产身份 = 内容 SHA + 来源（file/lake 表+版本）；路径仅提示。
        # 逐资产校验：单个资产不可解码/不可读记录失败状态，不中断整个 run。
        from preparation.operaters.inputs import source_entries
        from preparation.operaters.images import asset_resolution
        published_assets = {}
        unreadable_assets = []
        for directory in self.knowledge_runs + self.visual_runs:
            for item in source_entries(directory):
                if item['kind'] == 'image':
                    asset = item['asset']
                    resolution = asset_resolution(asset.get('path'), asset['sha256'])
                    entry = {'sha256': asset['sha256'], 'path': asset.get('path'),
                             'asset_source': resolution.source if resolution.status == 'ok' else None}
                    try:
                        pixels(asset)
                        published_assets[asset['sha256']] = entry
                    except (ValueError, OSError) as error:
                        entry['status'] = 'unreadable'
                        entry['error'] = str(error)[:200]
                        unreadable_assets.append(entry)
        if unreadable_assets:
            self.manifest['unreadable_published_assets'] = unreadable_assets
        self.manifest['published_assets'] = published_assets
        self.initialize_business()
        for key, observed in self.records.items(prefix='observed_asset/').items():
            if not key.startswith('observed_asset/'): continue
            if observed.get('blob_ref'):
                from preparation.operaters.inputs import asset_pixels
                asset_pixels({'blob_ref':observed['blob_ref'],'sha256':observed['expected_sha256']})
                continue
            resolution = asset_resolution(observed.get('path'), observed['expected_sha256'])
            actual = resolution.actual_sha256 if resolution.status == 'ok' else None
            previous = observed.get('actual_sha256')
            if previous is not None and actual != previous:
                raise ValueError('Previously inspected scene/target bytes changed: ' + str(observed.get('path')))
            if resolution.status == 'corrupt':
                raise ValueError('Previously inspected bytes now resolve corrupt: ' + str(observed.get('path')))
            # 缺失→可读 属于可用性恢复（湖接线后预期发生），不视为字节变化
        self.version = digest(self.manifest)
        self.previous = self.version
        self.stages, self.reused, self.new = {}, [], []


def split_guard(files):
    """保存本次保留集约束，供材料筛选和题面检查复用。"""
    registry = files.config.get('split_registry') or {'schema': 'v4-split-registry/1',
        'scope':'development_only','formal_test':{'concepts':[], 'rule_families':[], 'images':[],
        'source_urls':[], 'answer_text_sha256':[], 'question_sha256':[]}}
    files.records.put('split_registry',registry)
    return SplitGuard(registry)


def graph_version(branch="benchmark"):
    """冻结正式 Python 流程的指纹，不依赖 notebook 展示。"""
    return digest((Path(__file__).resolve().parents[1] / "edit_v2_benchmark_pipeline.py").read_bytes())
