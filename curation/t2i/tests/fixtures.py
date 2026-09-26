"""Small synthetic inputs for retained operators; no training driver or model calls."""
import copy

from preparation.tests.publication_fixtures import (
    article_publication, publish_fixture, reviewed_image)
from preparation.operaters.images import FinalizeVisualMaterials
from preparation.operaters.inputs import SplitGuard
from demiflow.execution.artifacts import digest, read
from curation.t2i.operaters.candidates import ExpandCandidates


def draft_config(registry):
    return {'seed': 0, 'concepts': None, 'max_units': None, 'tasks_per_unit': 1,
            'task_types': ['t2i'], 'training_design': 'target_aware',
            'reference_batch_size': 4, 'max_reference_images': 16,
            'max_context_chars': 60000, 'training_sample_goal': 3,
            'max_target_cycles': 2, 'max_training_attempts': 100,
            'split_registry': read(registry)}


def material_catalog(run, knowledge_runs, cfg, visual_runs=()):
    # 材料测试使用正式读取/筛选链的 materials 停点，不保留已删除算子的复制实现。
    from curation.t2i.t2i_train_pipeline import config, run_pipeline
    from demiflow import data
    from project import resolve_root
    settings = {**config(), **cfg}
    run = resolve_root() / 'demiwtg/curation/t2i/datasets' / ('fixture_materials_' + digest([str(run), settings])[:16])
    state = run_pipeline(run, knowledge_runs, settings, visual_sources=visual_runs, through='materials')
    ref = state['stages']['materials']['dataset_ref']
    import json
    return data.read_lance(str(resolve_root() / ref['relative_uri']), version=ref['lance_version']).map(
        lambda row: json.loads(row['payload'])).take_all()


def design_result(draft):
    draft = copy.deepcopy(draft)
    application = draft.pop('knowledge_application')
    return {'status': 'ok', 'candidates': [{'task_type': 't2i', 'evidence': [1,2],
        'learning_objective': '正确表现流程图判断节点的形状与分支连接',
        'input_materials': [1], 'reference_selection_reason': '参考说明判断节点及其分支关系。',
        'knowledge_gap': '节点形状未告知', 'knowledge_application': application, 'draft': draft}]}


def visual_only_training(inputs):
    visuals = []
    for n, asset in enumerate(inputs[4][:2]):
        image = reviewed_image(asset)
        image['image_id'] = f'visual{n}'
        result = FinalizeVisualMaterials()({'identity': {'target_label': '流程图'},
                                          'material_pack': {'images': [image]}})
        visuals.extend(result['visual_materials'])
    row = {'concept': '流程图', 'status': 'reviewed', 'publication_kind': 'visual_materials',
           'knowledge': [], 'visual_materials': visuals, 'images': [v['image'] for v in visuals]}
    ref = publish_fixture(inputs[0] / 'visual_only_source', 'knowledge_base', [row])
    cfg = draft_config(inputs[2])
    return ref, cfg


def designed_row(inputs):
    cfg = draft_config(inputs[2])
    cfg['training_design'] = 'knowledge_first'
    row = material_catalog(inputs[0] / 'material_draft', article_publication(inputs), cfg)[0]
    design = design_result(copy.deepcopy(inputs[-1]))
    design['candidates'][0]['evidence'] = [1]
    row.update(status='candidates_designed', candidate_design=design)
    return row, cfg


def binding_row(inputs):
    row, cfg = designed_row(inputs)
    candidate = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    assert candidate['status'] == 'constructed'
    # A labeled unit-test input to binding, not a claim of semantic acceptance.
    candidate.update(status='accepted_task', criteria=[{
        'knowledge_ids': [m['item_id'] for m in candidate['materials']]}])
    return candidate
