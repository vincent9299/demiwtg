"""文章库+视觉库按概念组合为训练输入（review R2）的契约测试。"""
import json
from pathlib import Path

import pytest

from preparation.operaters.inputs import combined_input_records, iter_material_rows


def write_kb(run, rows):
    from preparation.tests.publication_fixtures import republish_fixture
    return republish_fixture(None, rows)


def article_row(concept, text="文章正文段"):
    return {"concept": concept, "publication_kind": "article", "status": "reviewed",
            "knowledge": [{"title": "t", "content": {"paragraphs": [text], "images": []}}],
            "images": [], "visual_materials": []}


def visual_row(concept, sha):
    return {"concept": concept, "publication_kind": "visual_materials", "status": "reviewed",
            "knowledge": [], "images": [],
            "visual_materials": [{
                "concept": concept, "image_id": "img_" + sha[:8],
                "image": {"image_id": "img_" + sha[:8], "bytes": {"sha256": sha, "path": f"blobs/{sha[:2]}/{sha}.png"},
                          "record": {"source": "wikimedia", "content_url": "test://c"}},
                "publication": {"schema": "concept-visual-publication/1", "status": "reviewed",
                                "sha256": sha, "identity_reason": "r",
                                "support": {"supports": "支持范围", "region": "整体"}}}]}


def test_same_concept_article_plus_visual_combines_instead_of_error(tmp_path):
    article = tmp_path / "article_run"
    visual = tmp_path / "visual_run"
    article = write_kb(article, [article_row("中华鲟")])
    visual = write_kb(visual, [visual_row("中华鲟", "a" * 64)])
    merged = list(combined_input_records([article], [visual]))
    assert len(merged) == 1
    row = merged[0]
    assert row["concept"] == "中华鲟"
    # 文章知识保留 + 视觉材料合并 + 各自来源标注
    assert row["knowledge"] and len(row["visual_materials"]) == 1
    assert row["_article_source"]["dataset_ref"]["lance_version"] >= 1
    assert row["_visual_sources"][0]["dataset_ref"]["lance_version"] >= 1
    # 候选源指向视觉库 run（原始候选更全），正文来源保留文章 run
    assert {"dataset_ref":visual,"release_id":None} in row["_candidate_specs"]
    assert row['_article_source']['dataset_ref']['relative_uri'] == article['relative_uri']


def test_visual_only_concept_keeps_explicit_shape(tmp_path):
    visual = tmp_path / "visual_run"
    visual = write_kb(visual, [visual_row("猪鼻龟", "b" * 64)])
    merged = list(combined_input_records([], [visual]))
    assert len(merged) == 1
    row = merged[0]
    assert row["publication_kind"] == "visual_materials"
    assert row["knowledge"] == []  # 无正文状态显式，不伪装成文章
    assert len(row["visual_materials"]) == 1
    assert {"dataset_ref":visual,"release_id":None} in row["_candidate_specs"]


def test_distinct_article_versions_conflict_explicitly(tmp_path):
    a1, a2 = tmp_path / "r1", tmp_path / "r2"
    a1 = write_kb(a1, [article_row("黄水晶", "版本一正文")])
    a2 = write_kb(a2, [article_row("黄水晶", "版本二正文")])
    with pytest.raises(ValueError, match="Conflicting article publications"):
        list(combined_input_records([a1, a2], []))


def test_explicit_concept_scope_is_applied_before_merging_publications(tmp_path):
    a1 = write_kb(tmp_path / 'a1', [article_row('outside', 'one'), article_row("chosen'concept")])
    a2 = write_kb(tmp_path / 'a2', [article_row('outside', 'conflicting')])
    rows = list(combined_input_records([a1, a2], concepts=["chosen'concept"]))
    assert [r['concept'] for r in rows] == ["chosen'concept"]
    assert list(combined_input_records([a1, a2], concepts=[])) == []


def test_identical_article_publication_dedupes(tmp_path):
    a1, a2 = tmp_path / "r1", tmp_path / "r2"
    row = article_row("万年青")
    a1 = write_kb(a1, [row])
    a2 = write_kb(a2, [row])
    merged = list(combined_input_records([a1, a2], []))
    assert len(merged) == 1


def test_same_sha_supports_multiple_concepts_not_dropped(tmp_path):
    """同一张图服务多个概念：视觉材料按各自概念保留，不按 SHA 丢关系。"""
    sha = "c" * 64
    visual = tmp_path / "visual_run"
    visual = write_kb(visual, [visual_row("概念甲", sha), visual_row("概念乙", sha)])
    merged = list(combined_input_records([], [visual]))
    assert len(merged) == 2
    assert all(len(r["visual_materials"]) == 1 for r in merged)
    assert {r["visual_materials"][0]["publication"]["sha256"] for r in merged} == {sha}


def test_iter_material_rows_run_dir_provenance(tmp_path):
    run = tmp_path / "run"
    run = write_kb(run, [article_row("概念")])
    gen, identity = iter_material_rows(run)
    rows = list(gen)
    assert len(rows) == 1
    assert identity["dataset_ref"]["lance_version"] == 1


def test_lance_visual_release_delivers_pixels_without_run_directory(tmp_path, monkeypatch):
    import hashlib
    from PIL import Image
    from tools.lake_migration.visual_release_export import export_visual_release
    from preparation.operaters.inputs import material_record
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(tmp_path / 'lake'))
    path = tmp_path / 'image.png'
    Image.new('RGB', (32, 24), 'red').save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = tmp_path / 'visual.jsonl'
    meta.write_text(json.dumps({'concept': 'A', 'image_id': 'I1', 'sha256': sha, 'path': str(path),
        'format': 'PNG', 'publication_status': 'reviewed', 'concept_review': {'decision': 'keep', 'reason': 'visible'},
        'visual_support': {'supports': 'red surface', 'region': 'whole image'},
        'source': {'content_url': 'test://fixture'}, 'image_metadata': {}}) + '\n')
    from preparation.operaters.inputs import resolve_source
    ref = export_visual_release(meta, 'visual-test',source_ref=resolve_source(tmp_path/'lake','legacy_images')[0])
    merged = list(combined_input_records([], [{'uri': ref.relative_uri, 'version': ref.lance_version}]))
    assert len(merged) == 1 and len(merged[0]['visual_materials']) == 1
    delivered = material_record(merged[0])
    assert len(delivered['materials']) == 1
    assert delivered['materials'][0]['asset']['sha256'] == sha
    assert list(iter_material_rows({"dataset_ref":ref.to_dict(),"release_id":"visual-test"})[0])[0]['concept'] == 'A'
    spec = {"dataset_ref": ref.to_dict(), "release_id": "visual-test"}
    assert [r['concept'] for r in iter_material_rows(spec, concepts=['A'])[0]] == ['A']
    assert list(iter_material_rows(spec, concepts=["not'present"])[0]) == []
