"""当前摘要引用与未提交文件的只读呈现，不运行旧 pipeline。"""
import lance
from preparation.images.annotation.operaters import preview
from preparation.images.annotation import image_annotation_pipeline as pipeline
from preparation.images.annotation.tests.test_annotation_pipeline import cfg


def test_uncommitted_stage_is_visible_without_false_success(lake):
    path = lake['root'] / pipeline.MODULE_DIR / 'datasets/image_results__stopped.lance/data'
    path.mkdir(parents=True)
    panels = preview.stage_panels(lake['root'], 'stopped')
    row = panels.loc[panels['stage'] == 'image_results'].iloc[0]
    assert row['version'] is None or str(row['version']) == 'nan'
    assert '未提交' in row['state']
    assert preview.image_result_frame(lake['root'], 'stopped').empty


def test_preview_uses_summary_fixed_version_not_latest_stage(lake, endpoint):
    result = pipeline.run_pipeline(cfg(lake, endpoint))
    ref = result['image_results']
    from demiflow import data
    table = lance.dataset(str(lake['root'] / ref['uri']), version=ref['version'])
    altered = table.to_table().to_pylist()
    altered[0]['status'] = 'wrong-new-head'
    import pyarrow as pa
    data.from_arrow(pa.Table.from_pylist(altered, schema=table.schema)).write_lance(
        str(lake['root'] / ref['uri']), mode='overwrite', schema=table.schema)
    assert 'wrong-new-head' not in set(preview.image_result_frame(lake['root'], 'neutral')['status'])
    assert preview.latest_stage_version(lake['root'], 'neutral', 'image_results') == ref['version']


def test_missing_uri_never_uses_other_tables_for_pixels(lake):
    assert preview._load_image(lake['root'], {'sha256': 'a' * 64, 'source_refs': ['not-an-image-uri']}) is None
