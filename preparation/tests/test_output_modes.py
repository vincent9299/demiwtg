"""验证实际 Lance 目标的追加、覆盖、空输出及固定版本，所有表使用隔离数据根。"""
import pytest

from preparation.operaters.article import article_entity, write_articles
from preparation.operaters.images import assessment, write_curation
from preparation.tests.conftest import raw_metadata_snapshot


def test_article_append_overwrite_and_empty_output(tmp_path):
    """追加保留原行，覆盖仅留下本批次；旧版本始终可读。"""
    target = 'demiwtg/preparation/datasets/chosen_articles.lance'
    first_row = article_entity({'concept': 'A', 'status': 'reviewed', 'knowledge': []}, run_id='first')
    next_row = article_entity({'concept': 'B', 'status': 'reviewed', 'knowledge': []}, run_id='next')
    first = write_articles(tmp_path, [first_row], target_uri=target, write_mode='append')
    appended = write_articles(tmp_path, [next_row], target_uri=target, write_mode='append')
    assert appended.open(tmp_path).to_table()['concept'].to_pylist() == ['A', 'B']
    replaced = write_articles(tmp_path, [next_row], target_uri=target, write_mode='overwrite')
    assert replaced.open(tmp_path).to_table()['concept'].to_pylist() == ['B']
    assert first.open(tmp_path).to_table()['concept'].to_pylist() == ['A']
    unchanged = write_articles(tmp_path, [], target_uri=target, write_mode='append')
    assert unchanged.row_count == 1
    empty = write_articles(tmp_path, [], target_uri=target, write_mode='overwrite')
    assert empty.row_count == 0 and empty.open(tmp_path).schema == first.open(tmp_path).schema


def test_image_modes_keep_append_distinct_from_merge(tmp_path):
    """同一 SHA 的 append 保留两行；merge 才合并概念审核，覆盖不携带旧审核。"""
    target = 'demiwtg/preparation/datasets/chosen_images.lance'
    sha = 'a' * 64
    raw = raw_metadata_snapshot(tmp_path, [sha])
    rows = [{'sha256': sha, 'concept_assessments': [assessment({
        'sha256': sha, 'concept': concept, 'publication_status': 'reviewed',
        'concept_review': {'decision': 'keep', 'reason': 'fixture'},
        'visual_support': {'supports': concept, 'region': 'whole', 'limitations': ''}}, concept)]}
        for concept in ('A', 'B')]
    first = write_curation(tmp_path, rows[:1], source_ref=raw, target_uri=target, write_mode='append')
    appended = write_curation(tmp_path, rows[1:], source_ref=raw, target_uri=target, write_mode='append')
    assert appended.row_count == 2
    assert appended.open(tmp_path).to_table()['published_concepts'].to_pylist() == [['A'], ['B']]
    replaced = write_curation(tmp_path, rows[1:], source_ref=raw, target_uri=target, write_mode='overwrite')
    assert replaced.open(tmp_path).to_table()['published_concepts'].to_pylist() == [['B']]
    merged = write_curation(tmp_path, rows[:1], source_ref=raw, target_uri=target)
    assert merged.row_count == 1
    assert merged.open(tmp_path).to_table()['published_concepts'].to_pylist() == [['A', 'B']]
    assert first.open(tmp_path).to_table()['published_concepts'].to_pylist() == [['A']]
    empty = write_curation(tmp_path, [], source_ref=raw, target_uri=target, write_mode='overwrite')
    assert empty.row_count == 0
    assert raw.open(tmp_path).count_rows() == 1


@pytest.mark.parametrize('writer', [write_articles, write_curation])
def test_invalid_mode_is_rejected_before_reading_or_writing(writer, tmp_path):
    """参数拼写错误不应启动数据读取或产生表。"""
    kwargs = {'source_ref': None} if writer is write_curation else {}
    with pytest.raises(ValueError, match='write_mode'):
        writer(tmp_path, [], write_mode='typo', **kwargs)
    assert not list(tmp_path.iterdir())
