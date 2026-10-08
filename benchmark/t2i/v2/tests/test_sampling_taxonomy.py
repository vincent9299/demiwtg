"""新分类仅用于分层；固定树、挂载及审定版本必须一致，不调用模型。"""
from copy import deepcopy

import lance
import pyarrow as pa
import pytest

from preparation.taxonomy.operators.contracts import NODES, PLACEMENTS, SUMMARY
from preparation.taxonomy.operators.tree import tree_hash
from benchmark.t2i.v2.operators.sampling_taxonomy import (
    collect_nodes, sampling_tree, placement_category, unique_placement,
)
from benchmark.t2i.v2.t2i_v2_benchmark_pipeline import config, run_pipeline
from test_paired_authoring import sources, configuration
from test_concept_context import adopted, rows


def node(key, parent='', name=None):
    return dict(node_id=key, parent_id=parent, name=name or key, definition='fixture definition',
        includes='fixture scope', excludes='other scope', examples=['example'],
        counterexamples=['counterexample'], status='active', replaced_by=[])


def fixed_taxonomy(tmp_path):
    nodes = [node('x', name='新甲'), node('x1', 'x', '细类'), node('y', name='新乙')]
    uri = str(tmp_path / 'nodes.lance')
    lance.write_dataset(pa.Table.from_pylist(nodes, schema=NODES), uri)
    placements = [dict(source_record_id='source1', assessment_id=assessment,
        node_id=key, status=status, tree_hash=tree_hash(nodes), reason='fixture', issue='', session_id='fixture')
        for assessment, key, status in [('A0','x1','assigned'), ('A1','y','assigned'),
                                       ('old-A2','x1','assigned'), ('A3',None,'unplaced')]]
    placement_uri = str(tmp_path / 'placements.lance')
    lance.write_dataset(pa.Table.from_pylist(placements, schema=PLACEMENTS), placement_uri)
    summary_uri = str(tmp_path / 'summary.lance')
    summary = dict(run='candidate', phase='review_incomplete', complete=False, quality_passed=False,
        outputs=[dict(stage='nodes',uri=uri,version=1),dict(stage='placements',uri=placement_uri,version=1)])
    lance.write_dataset(pa.Table.from_pylist([summary], schema=SUMMARY), summary_uri)
    # 不得偷读最新 head；最新节点刻意与挂载时的树不同。
    newer = deepcopy(nodes)
    newer[0]['name'] = '不应读取的新表头'
    lance.write_dataset(pa.Table.from_pylist(newer, schema=NODES), uri, mode='overwrite')
    return {'uri':summary_uri,'version':1}


def test_fixed_candidate_replaces_old_categories_and_excludes_stale_or_unplaced(sources, tmp_path):
    source = fixed_taxonomy(tmp_path)
    cfg = configuration(sources, sample_size=2, sampling_taxonomy_source=source)
    state = run_pipeline(cfg)
    selected = rows(state['inputs'])
    assert len(selected) == 2 and {r['concept_id'] for r in selected} == {'C0','C1'}
    assert {r['sampling_category'] for r in selected} == {'新甲 / 细类','新乙'}
    assert {r['sampling_node_id'] for r in selected} == {'x1','y'}
    assert all(r['positive_image_count'] == 3 and r['taxonomy'] == [] for r in selected)
    assert state['selection']['ready_with_positive_images'] == 4
    assert state['selection']['excluded_without_matching_taxonomy'] == 2
    assert state['sources']['sampling_taxonomy']['summary'] == source
    assert state['sources']['sampling_taxonomy']['quality_passed'] is False
    assert state['phase'] == 'inputs' and not state['complete']
    with pytest.raises(ValueError, match='Only 2 ready concepts'):
        run_pipeline({**cfg, 'sample_size':3})


def test_tree_identity_and_assignment_validation():
    nodes = [node('root'), node('leaf','root')]
    acc = None
    for item in nodes:
        acc = collect_nodes(acc, {'node':item})
    tree = sampling_tree(acc)
    row = dict(source_record_id='s',assessment_id='a',node_id='leaf',tree_hash=tree['tree_hash'])
    assert placement_category(row,tree=tree)['sampling_taxonomy'] == ['root / leaf']
    with pytest.raises(ValueError, match='different tree'):
        placement_category({**row,'tree_hash':'stale'},tree=tree)
    with pytest.raises(ValueError, match='absent or retired'):
        placement_category({**row,'node_id':'unknown'},tree=tree)
    with pytest.raises(ValueError, match='Duplicate taxonomy placement'):
        unique_placement(row,row)
    nodes[0]['parent_id']='leaf'
    with pytest.raises(ValueError, match='cycle'):
        sampling_tree({'nodes':nodes})


def test_revision_can_keep_audited_common_without_taxonomy_and_marks_gap(sources, tmp_path):
    source = fixed_taxonomy(tmp_path)
    cfg = configuration(sources, sample_size=2, sampling_taxonomy_source=source)
    base = run_pipeline(cfg)
    uri = str(tmp_path / 'labels.lance')
    lance.write_dataset(pa.Table.from_pylist([
        {'concept':'概念0','final_category':'3'}, {'concept':'概念1','final_category':'2'},
        {'concept':'概念3','final_category':'1'}]), uri)
    cfg = configuration(sources, sample_size=2, sampling_taxonomy_source=source,
        run=tmp_path / 'unused', cohort_revision={
            'base_cohort':base['inputs'], 'common_limit':1, 'min_common_positive_images':1,
            'common_allow_unassigned_taxonomy':True,
            'case_category_sources':[{'uri':uri,'version':1,'kind':'classifications'}]})
    # 使用同一入口要求的本模块运行目录，新目标保留旧固定版本。
    from pathlib import Path
    cfg['run'] = str(Path(base['inputs']['uri']).parent / 'common_unassigned')
    result = run_pipeline(cfg)
    selected = {r['concept_id']:r for r in rows(result['inputs'])}
    assert set(selected) == {'C1','C3'}
    assert selected['C3']['sampling_node_id'] is None
    assert selected['C3']['sampling_taxonomy'] == ['未匹配固定 taxonomy']
    assert result['selection']['revision']['unassigned_common_taxonomy'] == ['概念3']
    assert result['selection']['excluded_without_matching_taxonomy'] == 1


@pytest.mark.parametrize('source', [{'uri':'summary.lance'}, {'uri':'summary.lance','version':0},
                                    {'uri':'summary.lance','version':True}])
def test_sampling_requires_a_fixed_summary(sources, source):
    with pytest.raises(ValueError, match='sampling_taxonomy_source'):
        configuration(sources,sampling_taxonomy_source=source)
