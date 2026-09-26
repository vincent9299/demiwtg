"""正式视觉发布 exporter（R3/R4）契约测试：字段真实性、发布绑定、指纹敏感。"""
import json
import shutil

import pytest

from tools.lake_migration.visual_release_export import export_visual_release as _export
from preparation.tests.conftest import raw_metadata_snapshot

def export_visual_release(*args,**kwargs):
    root=kwargs["datasets_root"]
    kwargs["source_ref"]=raw_metadata_snapshot(root,[c*64 for c in "abc"])
    return _export(*args,**kwargs)


def make_meta(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def candidate(sha, concept="概念", *, decision="keep", status="reviewed", fmt="PNG", support="支持A"):
    return {"sha256": sha, "concept": concept, "format": fmt,
            "concept_review": {"decision": decision, "reason": None if decision == "keep" else "无关"},
            "publication_status": status, "visual_support": {"supports": support}}


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path))
    return tmp_path


def test_real_fields_and_publication_binding(lake, tmp_path):
    """keep 但最终 not_published 的记录不得放行；真实格式/行号/来源。"""
    meta = tmp_path / "meta.jsonl"
    make_meta(meta, [
        candidate("a" * 64, status="reviewed", fmt="WEBP"),
        candidate("b" * 64, status="not_published", fmt="PNG"),   # keep 但未发布
        candidate("c" * 64, decision="unrelated", status="not_published", fmt=None),
    ])
    ref = export_visual_release(meta, "rls_fields", datasets_root=lake)
    import lance

    table = lance.dataset(str(lake / ref.relative_uri)).to_table().to_pylist()
    by_sha = {r["sha256"]: {**r, **r["concept_assessments"][0]} for r in table}
    assert by_sha["a" * 64]["published"] is True
    assert json.loads(by_sha["a" * 64]["observation_json"])["format"] == "WEBP"
    assert by_sha["b" * 64]["published"] is False, "keep 决策不等于发布，不得越过 publication_status"
    assert by_sha["b" * 64]["review_status"] == "keep"
    assert json.loads(by_sha["c" * 64]["observation_json"])["format"] is None, "未知格式显式缺失"
    assert by_sha["a" * 64]["provenance"]["source_row"] == 1 and by_sha["c" * 64]["provenance"]["source_row"] == 3
    assert by_sha["a" * 64]["release_ids"] == ["rls_fields"]
    assert str(meta) in by_sha["a" * 64]["provenance"]["source_file"]


def test_published_list_binding_and_mismatch(lake, tmp_path):
    meta = tmp_path / "meta.jsonl"
    make_meta(meta, [candidate("a" * 64), candidate("b" * 64, status="not_published")])
    pub = tmp_path / "published.jsonl"
    pub.write_text(json.dumps({"concept": "概念", "sha256": "a" * 64}) + "\n")
    ref = export_visual_release(meta, "rls_bind", published_jsonl=pub, datasets_root=lake)
    import lance

    table = lance.dataset(str(lake / ref.relative_uri)).to_table().to_pylist()
    assert sum(a["published"] for r in table for a in r["concept_assessments"]) == 1
    # 清单与 meta 状态不一致 → 拒绝导出
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"concept": "概念", "sha256": "b" * 64}) + "\n")
    with pytest.raises(ValueError, match="publication binding mismatch"):
        export_visual_release(meta, "rls_bad", published_jsonl=bad, datasets_root=lake)


def test_fingerprint_binds_support_changes_same_location_conflicts(lake, tmp_path):
    """R4 回归：仅修改 visual_support，同 release_id 再导出必须报冲突。"""
    meta = tmp_path / "meta.jsonl"
    make_meta(meta, [candidate("a" * 64, support="支持A")])
    ref1 = export_visual_release(meta, "rls_fp", datasets_root=lake)
    # 完全相同输入重放 → 同引用幂等
    ref2 = export_visual_release(meta, "rls_fp", datasets_root=lake)
    assert ref1.lance_version == ref2.lance_version
    # 只改支持范围 → 同位置输入变化 → 冲突
    make_meta(meta, [candidate("a" * 64, support="支持B（修改后）")])
    with pytest.raises(ValueError, match="different fingerprint"):
        export_visual_release(meta, "rls_fp", datasets_root=lake)
    # 修正版用新 release_id 另行发布
    ref3 = export_visual_release(meta, "rls_fp_v2", previous_release_id="rls_fp", datasets_root=lake)
    assert ref3.relative_uri == "demiwtg/preparation/datasets/images.lance"
    assert ref3.lance_version > ref1.lance_version


def test_cross_concept_publication_is_not_interchangeable(lake, tmp_path):
    meta, pub = tmp_path / "meta.jsonl", tmp_path / "pub.jsonl"
    make_meta(meta, [candidate("a" * 64, concept="A"), candidate("a" * 64, concept="B")])
    make_meta(pub, [{"concept": "A", "sha256": "a" * 64}])
    with pytest.raises(ValueError, match="publication binding mismatch"):
        export_visual_release(meta, "wrong-concept", published_jsonl=pub, datasets_root=lake)
