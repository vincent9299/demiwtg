"""Project root configuration and authoritative Lance asset layout tests."""
import hashlib
import os

import pytest

from project import (
    DATASETS_ROOT_ENV, default_root, resolve_root,
)
from demiflow.lance.refs import DatasetRef
import demiflow.lance.refs as platform_refs


def make_ref(**overrides):
    fields = dict(
        dataset_id="demiwtg/visual/materials",
        relative_uri="demiwtg/releases/visual_materials/r1/materials.lance",
        lance_version=3,
        schema_name="visual_materials",
        schema_version="v1",
        schema_hash="sha256:" + "0" * 64,
        row_count=12,
    )
    fields.update(overrides)
    return DatasetRef(**fields)


# --------------------------------------------------------------------- config

def test_default_root_at_workspace_top_level():
    root = default_root()
    assert root.is_absolute()
    from project import PROJECT_ROOT
    assert root == PROJECT_ROOT.parent
    assert root / "demiwtg" == PROJECT_ROOT
    assert (root / "demiwtg").name == "demiwtg"


def test_resolve_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(DATASETS_ROOT_ENV, str(tmp_path / "lake"))
    assert resolve_root() == tmp_path / "lake"


def test_resolve_root_explicit_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(DATASETS_ROOT_ENV, str(tmp_path / "from-env"))
    assert resolve_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_resolve_root_rejects_relative():
    with pytest.raises(ValueError):
        resolve_root("relative/datasets")


# ----------------------------------------------------------------- re-export

def test_refs_reexport_is_platform_implementation():
    assert DatasetRef is platform_refs.DatasetRef


# ------------------------------------------------------------- 适配绑定

def write_blob(blobs_root, payload: bytes, ext: str = "jpg") -> str:
    sha = hashlib.sha256(payload).hexdigest()
    target = blobs_root / sha[:2] / f"{sha}.{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return sha


def test_reader_binds_lake_even_before_the_first_table(tmp_path):
    from collect.assets import IMAGES_URI, AssetReader, AssetMissing
    reader = AssetReader(datasets_root=tmp_path)
    assert reader.uri == str(tmp_path / IMAGES_URI)
    assert reader.datasets_root == tmp_path
    sha = write_blob(tmp_path/'blobs', b'old-file')
    with pytest.raises(AssetMissing): reader.read_bytes(sha, 'jpg')
