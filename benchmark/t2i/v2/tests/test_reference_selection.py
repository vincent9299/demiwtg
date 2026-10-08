"""真实隔离 Lance 读写验证待审核名单；不请求模型或读取生产图片字节。"""
import json

import lance
import pyarrow as pa
import pytest
from preparation.articles.tests.conftest import isolated_lake

from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import reference_selection_config, run_reference_selection
from preparation.images.catalog.operators.schema import IMAGES
from benchmark.t2i.v2.operators.reference_selection import choose_reference_concepts
from project import resolve_root


def fixture_selection():
    root = resolve_root()
    screen = root / 'screen.lance'
    lance.write_dataset(pa.Table.from_pylist([
        {'concept': c, 'taxonomy': ['领域 / 类别 / ' + c], 'status': s, 'decision': d}
        for c, s, d in [('甲', 'screened', 'keep'), ('乙', 'screened', 'keep'),
                        ('丙', 'screened', 'keep'), ("D'X", 'screened', 'keep'),
                        ('暂缓', 'screened', 'hold'), ('失败', 'failed', 'keep')]
    ] + [{'concept': '甲', 'taxonomy': ['其他 / 分类 / 路径'], 'status': 'screened', 'decision': 'keep'}]), str(screen))
    records = []
    for c, count in [('甲', 3), ('乙', 3), ('丙', 2), ('暂缓', 3), ('失败', 3)]:
        for i in range(count):
            records.append({'sha256': f'{len(records):064x}', 'concepts': [c, c],
                'image_uri': 'file:///fixtures/independent-image',
                'width': 512, 'height': 512, 'availability': 'available',
                'generation_origin': 'not_verified', 'concept_assessments': [], 'published_concepts': []})
    records[0].update(published_concepts=['甲'], concept_assessments=[{
        'concept': '甲', 'published': True, 'review_status': 'keep'}])
    for override in ({'availability': 'metadata_only'}, {'generation_origin': 'generated'},
                     {'width': 0}, {'image_uri': None}):
        records.append({**records[0], 'sha256': f'{len(records):064x}', **override})
    images = root / 'images.lance'
    lance.write_dataset(pa.Table.from_pylist(records, schema=IMAGES), str(images))
    articles = root / 'articles.lance'
    lance.write_dataset(pa.Table.from_pylist([
        {'concept': "D'X", 'review_status': 'reviewed',
         'content': [{'content': {'paragraphs': ['已审正文', '  ']}}]},
        {'concept': '丙', 'review_status': 'unreviewed',
         'content': [{'content': {'paragraphs': ['不能当已审参考']}}]},
    ]), str(articles))
    cfg = reference_selection_config(run=root / 'demiwtg/benchmark/t2i/v2/datasets/select3',
        screening_source={'uri': str(screen), 'version': 1}, image_source={'uri': str(images), 'version': 1},
        article_source={'uri': str(articles), 'version': 1}, sample_size=3, sample_seed=42)
    return cfg, records


def read(ref):
    return lance.dataset(ref['uri'], version=ref['version']).to_table().to_pylist()


def test_selection_counts_filters_fixed_sources_and_no_model(monkeypatch):
    # 如果误进入任一模型阶段，测试立即失败。
    from demiflow.data.dataset import Dataset
    monkeypatch.setattr(Dataset, 'map_prompt_async', lambda *a, **k: pytest.fail('Unexpected model call'))
    cfg, records = fixture_selection()
    result = run_reference_selection(cfg)
    selected = read(result['reference_concepts'])
    coverage = {r['concept']: r for r in read(result['reference_coverage'])}
    assert result['screened_count'] == 4 and result['eligible_count'] == result['selected_count'] == 3
    assert {r['concept'] for r in selected} == {'甲', '乙', "D'X"}
    assert selected[-1]['concept'] == '乙'  # 已审核材料优先于待审图。
    assert [r['selection_rank'] for r in selected] == [1, 2, 3]
    assert coverage['甲']['candidate_image_count'] == 3 and coverage['甲']['published_image_count'] == 1
    assert len(coverage['甲']['taxonomy']) == 2
    assert coverage["D'X"]['reviewed_text_count'] == 1
    assert not coverage['丙']['eligible'] and coverage['丙']['reviewed_text_count'] == 0
    assert json.loads(selected[0]['sources_json'])['image_source'] == cfg['image_source']
    # 上游出现新版本后仍读指定快照；改变物理行序也不影响名单或次序。
    lance.write_dataset(pa.Table.from_pylist([], schema=IMAGES), cfg['image_source']['uri'], mode='overwrite')
    again = run_reference_selection(cfg)
    assert read(again['reference_concepts']) == selected
    reordered = resolve_root() / 'reordered.lance'
    lance.write_dataset(pa.Table.from_pylist(list(reversed(records)), schema=IMAGES), str(reordered))
    last = run_reference_selection({**cfg, 'image_source': {'uri': str(reordered), 'version': 1}})
    assert [r['concept'] for r in read(last['reference_concepts'])] == [r['concept'] for r in selected]


def test_insufficient_materials_do_not_write_a_partial_list():
    cfg, _ = fixture_selection()
    with pytest.raises(ValueError, match='Only 3'):
        run_reference_selection({**cfg, 'sample_size': 4})
    assert not list((resolve_root() / 'demiwtg/benchmark/t2i/v2/datasets').glob('reference_*.lance'))


def test_category_round_robin_is_reproducible():
    rows = [{'concept': f'{category}{i}', 'category': category, 'eligible': True,
             'material_status': 'needs_image_review'} for category in ('小类', '大类')
            for i in range(1 if category == '小类' else 8)]
    a = choose_reference_concepts({'rows': rows}, sample_size=3, sample_seed=42)
    b = choose_reference_concepts({'rows': list(reversed(rows))}, sample_size=3, sample_seed=42)
    assert a == b and {r['category'] for r in a['selected']} == {'小类', '大类'}


@pytest.mark.parametrize('extra', [{'sample_size': 0}, {'sample_size': True}, {'min_candidate_images': 0},
                                  {'sample_seed': 'random'}, {'category_depth': 0},
                                  {'image_source': {'uri': 'images.lance', 'version': None}}])
def test_selection_config_rejects_ambiguous_sources_or_limits(extra):
    values = dict(run='/unused', screening_source={'uri': 'screen.lance', 'version': 1},
                  image_source={'uri': 'images.lance', 'version': 1})
    with pytest.raises(ValueError):
        reference_selection_config(**{**values, **extra})
