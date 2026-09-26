"""Real isolated Lance writes with explicitly simulated VLM/image responses."""
import io
from pathlib import Path
from types import SimpleNamespace

import lance
import pytest
from demiflow.errors import LanceWriteError
from PIL import Image, ImageDraw
from demiflow.lance.blobs import LanceBlobStore
from curation.edit.edit_train_pipeline import config, run_attempt
from curation.edit.operaters.contracts import restore_question, training_sample
from curation.edit.operaters.prompting import CHECKS, bind_pair, apply_review, apply_design
from preparation.operaters.inputs import SplitGuard


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(tmp_path))
    im = Image.new('RGB', (96, 96), 'white')
    ImageDraw.Draw(im).rectangle((10, 15, 65, 75), fill='red')
    buffer = io.BytesIO(); im.save(buffer, format='PNG')
    ref = LanceBlobStore(tmp_path, 'demiwtg/collect/datasets/images.lance').put(buffer.getvalue())
    row = {'task_id': 'edit_test', 'concept': '测试器物', 'status': 'prepared',
           'existing': {'sha256': ref.sha256, 'blob_ref': ref.to_dict(), 'source': {'url': 'fixture'}},
           'materials': [], 'source_refs': [], 'previous_samples': []}
    return row, tmp_path / 'demiwtg/curation/edit/datasets/test'


def design(role):
    return dict(status='ok', reason='', learning_directions=['概念视觉特征'], learning_objective='保留结构并改变颜色',
        edit_type='属性修改', existing_role=role, role_reason='模拟测试理由', supervision_signal='可见主体',
        instruction='把主体改为蓝色' if role == 'source' else '把主体恢复为红色',
        synthesis_instruction='把主体改为蓝色', reference_reason='无需参考',
        preserve=['背景保持白色'], criteria=['主体颜色正确'], evidence=[], input_materials=[], synthesis_materials=[])


class SimulatedImageModel:
    def __init__(self): self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        im = kwargs['image'][0].copy()
        ImageDraw.Draw(im).rectangle((10, 15, 65, 75), fill='blue')
        return SimpleNamespace(images=[im])


@pytest.mark.parametrize('role', ['source', 'target'])
@pytest.mark.parametrize('accept', [True, False])
def test_real_lance_pipeline_roles_review_and_resume(case, monkeypatch, role, accept):
    from demiflow.operator_llm.runtime import AsyncOperatorLLMRuntime
    row, run = case
    calls = []
    async def response(self, name, values):
        calls.append(name)
        result = design(role) if name == 'design_edit' else {
            'checks': {k: {'passed': accept, 'observation': 'Simulated verdict'} for k in CHECKS},
            'criteria': [{'criterion': 1, 'passed': accept, 'observation': 'Simulated verdict'}],
            'reason': 'Simulated test, not semantic evidence'}
        return {'result': result}, {'elapsed_s': 0.25, 'model': 'simulated'}
    monkeypatch.setattr(AsyncOperatorLLMRuntime, 'call_with_trace', response)
    model = SimulatedImageModel()
    result = run_attempt(row, run=run, cfg=config(), image_model=model)
    assert result['status'] == ('accepted' if accept else 'rejected')
    assert result['existing_role'] == role
    assert result[role]['sha256'] == row['existing']['sha256']
    assert result['source']['sha256'] != result['target']['sha256']
    assert result['input_content'][1]['blob_ref'] == result['source']['blob_ref']
    assert result['target']['blob_ref'] not in [c.get('blob_ref') for c in result['input_content']]
    assert result['design_seconds'] == result['review_seconds'] == 0.25
    assert result['synthesis_seconds'] >= 0
    saved = lance.dataset(str(run.parent / 'review__test__edit_test.lance'), version=1).to_table().to_pylist()[0]
    assert saved['instruction'] == result['instruction']
    assert restore_question(saved)['status'] == result['status']
    if accept:
        sample = training_sample(result, audit_uri=run.parent / 'review__test__edit_test.lance')
        assert 'criteria' not in sample and 'learning_objective' not in sample
    else:
        with pytest.raises(ValueError): training_sample(result, audit_uri='unused')
    assert run_attempt(row, run=run, cfg=config(), image_model=model) == result
    assert calls == ['design_edit', 'review_edit'] and model.calls == 1


def test_material_selection_rejects_out_of_range(case):
    row, _ = case
    proposal = design('target'); proposal['input_materials'] = [1]
    result = apply_design({**row, 'design_result': proposal}, guard=SplitGuard(config()['split_registry']))
    assert result['status'] == 'invalid_design'


def test_noop_pair_rejected(case):
    row, _ = case
    result = bind_pair({**row, 'status': 'synthesized', 'existing_role': 'source',
                       'generated': row['existing'], 'answer_materials': []})
    assert result['status'] == 'invalid_pair'


def test_cli_import_does_not_load_diffusers():
    import subprocess, sys
    result = subprocess.run([sys.executable, '-m', 'curation.edit.edit_train_pipeline', '--help'],
                            text=True, capture_output=True)
    assert result.returncode == 0 and '--stage' in result.stdout


@pytest.mark.parametrize('custom_target', [False, True])
def test_pipeline_mixed_outcomes_typed_tables_and_limit(case, monkeypatch, custom_target):
    """Regression: null fields in rejected rows must not break the aggregate writer."""
    import curation.edit.edit_train_pipeline as pipeline
    from demiflow.operator_llm.runtime import AsyncOperatorLLMRuntime
    from demiflow.lance.refs import DatasetRef
    from demiflow.lance.storage import schema_hash
    from curation.edit.operaters.contracts import QUESTIONS, SAMPLES
    row, run = case
    root = run.parents[4]
    raw = lance.dataset(str(root / 'demiwtg/collect/datasets/images.lance'))
    ref = DatasetRef('fixture', 'demiwtg/collect/datasets/images.lance', 1, 'pipeline_stage_rows', 'v1', schema_hash(raw.schema), 1).to_dict()
    sources = {'articles': [{'dataset_ref': ref, 'release_id': None}], 'raw_images_ref': ref}
    # 实际读取隔离材料表；只替换图片来源/字节读取边界，关联、筛选、编号仍走正式 Dataset 链。
    import json
    import pyarrow as pa
    from preparation.operaters.results import PIPELINE_STAGE_ROWS, to_stage_row
    uri = root / 'materials.lance'
    inputs = [{'concept': name, 'status': 'reviewed', 'publication_kind': 'visual_materials', 'knowledge': [],
        'visual_materials': [{'concept': name, 'image_id': 'fixture',
            'image': {'bytes': {'sha256': row['existing']['sha256']}},
            'publication': {'schema': 'concept-visual-publication/1', 'status': 'reviewed',
                'sha256': row['existing']['sha256'], 'support': {'supports': '测试结构', 'region': '主体'},
                'identity_reason': '模拟发布依据'}}] * 3} for name in ['可用', '不可用']]
    table = pa.Table.from_pylist([to_stage_row(item, stage='fixture', upstream_identity='fixture', migrated_us=0)
                                 for item in inputs], schema=PIPELINE_STAGE_ROWS)
    lance.write_dataset(table, str(uri), mode='create')
    material_ref = DatasetRef('fixture_materials', 'materials.lance', 1, 'pipeline_stage_rows', 'v1', schema_hash(table.schema), 2).to_dict()
    sources['articles'] = [{'dataset_ref': material_ref, 'release_id': None}]
    monkeypatch.setattr(pipeline, 'source_asset', lambda image: dict(row['existing']))
    calls = []
    async def response(self, name, values):
        calls.append((values['payload']['concept'], name))
        if name == 'design_edit':
            result = design('target')
            if values['payload']['concept'] == '不可用':
                result.update(status='insufficient', reason='Synthetic insufficient material')
        else:
            result = {'checks': {k: {'passed': True, 'observation': 'Simulated'} for k in CHECKS},
                      'criteria': [{'criterion': 1, 'passed': True, 'observation': 'Simulated'}], 'reason': 'Simulated'}
        return {'result': result}, {'elapsed_s': 0.1}
    monkeypatch.setattr(AsyncOperatorLLMRuntime, 'call_with_trace', response)
    cfg = config(samples_per_concept=1, max_attempts_per_concept=1)
    model = SimulatedImageModel()
    outputs = {'target_uri': str(run.parent / 'chosen_samples.lance'),
               'questions_uri': str(run.parent / 'chosen_questions.lance')} if custom_target else {}
    result = pipeline.run_pipeline(run, sources, cfg, image_model=model, **outputs)
    if custom_target:
        assert result['training_samples_uri'] == outputs['target_uri']
        assert result['questions_uri'] == outputs['questions_uri']
    questions = lance.dataset(result['questions_uri'], version=1)
    training = lance.dataset(result['training_samples_uri'], version=1)
    assert questions.schema == QUESTIONS and questions.count_rows() == 2
    assert training.schema == SAMPLES and training.count_rows() == 1
    assert len(calls) == 3 and model.calls == 1
    assert {r['status'] for r in questions.to_table().to_pylist()} == {'accepted', 'insufficient'}
    assert pipeline.run_pipeline(run, sources, cfg, image_model=model, **outputs) == result
    assert len(calls) == 3 and model.calls == 1
    if custom_target:
        # 第一张表已追加、第二张表失败时，续跑只补写第二张表。
        writer = lance.write_dataset
        def fail_samples(data, uri, **kwargs):
            if str(uri) == outputs['target_uri']:
                raise OSError('injected sample writer failure')
            return writer(data, uri, **kwargs)
        append_run = run.with_name('append')
        with monkeypatch.context() as patch:
            patch.setattr(lance, 'write_dataset', fail_samples)
            with pytest.raises(LanceWriteError, match='injected'):
                pipeline.run_pipeline(append_run, sources, cfg, image_model=model, **outputs,
                                      write_mode='append', questions_write_mode='append')
        assert lance.dataset(outputs['questions_uri']).count_rows() == 4
        assert lance.dataset(outputs['target_uri']).count_rows() == 1
        appended = pipeline.run_pipeline(append_run, sources, cfg, image_model=model, **outputs,
                                         write_mode='append', questions_write_mode='append')
        assert lance.dataset(outputs['questions_uri']).count_rows() == 4
        assert lance.dataset(outputs['target_uri']).count_rows() == 2
        assert pipeline.run_pipeline(append_run, sources, cfg, image_model=model, **outputs,
                                     write_mode='append', questions_write_mode='append') == appended
        # 两个目标各自选择模式，不能把题目表的覆盖误用到训练表。
        pipeline.run_pipeline(run.with_name('mixed'), sources, cfg, image_model=model, **outputs,
                              write_mode='overwrite', questions_write_mode='append')
        assert lance.dataset(outputs['questions_uri']).count_rows() == 6
        assert lance.dataset(outputs['target_uri']).count_rows() == 1
