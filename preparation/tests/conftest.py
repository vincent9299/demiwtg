"""Every pipeline test has an isolated lake, including migration/replay tests."""
import pytest


@pytest.fixture(autouse=True)
def isolated_lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path / "datasets"))


@pytest.fixture(autouse=True)
def ingest_generated_fixture_images(isolated_lake, monkeypatch):
    """Test source images are ingested to an isolated raw Blob shard at creation.

    Files remain only as test input/provenance. Production readers never see a
    test-only fallback, and changing/deleting those files cannot alter the lake.
    """
    from PIL import Image
    from pathlib import Path
    import hashlib
    import lance
    import pyarrow as pa
    from project import resolve_root
    from collect.material_schema import IMAGES
    original = Image.Image.save

    def save(self, fp, *args, **kwargs):
        result = original(self, fp, *args, **kwargs)
        if not isinstance(fp, (str, Path)):
            return result
        raw = Path(fp).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        uri = resolve_root() / 'demiwtg/collect/datasets/images.lance'
        uri.parent.mkdir(parents=True, exist_ok=True)
        if uri.exists() and lance.dataset(str(uri)).count_rows(filter=f"sha256 = '{sha}'"):
            return result
        from collect.material_writer import write_images
        write_images(resolve_root(),[{'sha256':sha,'ext':Path(fp).suffix.lstrip('.'),
            'byte_size':len(raw),'storage_mode':'lance_blob','data':raw,
            'concepts':[],'sources':[],'availability':'available','resolution':None}])
        return result

    monkeypatch.setattr(Image.Image, 'save', save)


def raw_metadata_snapshot(root, shas):
    """Explicit raw records for tests that exercise labels without pixel decoding."""
    from collect.material_writer import write_images
    from collect.material_schema import IMAGES_URI
    from demiflow.lance.registry import Catalog
    write_images(root,[{'sha256':sha,'ext':'unknown','byte_size':0,'storage_mode':'lance_blob',
        'data':None,'availability':'metadata_only','concepts':[],'sources':[],'resolution':None} for sha in shas])
    return max((r for r in Catalog(root).registered() if r.relative_uri==IMAGES_URI),key=lambda r:r.lance_version)
