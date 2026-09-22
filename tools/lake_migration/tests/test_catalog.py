"""Migration tools consume the public demiflow Lance API."""
import demiflow.lance.registry as platform_registry

from demiflow.lance.registry import (
    REGISTRY_RELATIVE, Catalog, CatalogConflict,
)


def test_reexports_are_the_platform_implementation():
    assert Catalog is platform_registry.Catalog
    assert CatalogConflict is platform_registry.CatalogConflict
    # 登记表相对位置是在册数据的既有契约，不得漂移
    assert REGISTRY_RELATIVE == platform_registry.REGISTRY_RELATIVE == (
        "registry/datasets.lance"
    )


def test_smoke_register_and_read_back(tmp_path):
    from demiflow.lance.refs import DatasetRef

    catalog = Catalog(tmp_path)
    ref = DatasetRef(
        dataset_id="demiwtg/x", relative_uri="demiwtg/x/out.lance",
        lance_version=1, schema_name="generic", schema_version="v1",
        schema_hash="sha256:" + "0" * 64, row_count=1,
    )
    catalog.register(ref)
    assert catalog.latest("demiwtg/x") == ref
    catalog.register(ref)  # 幂等
    assert len(catalog.registered()) == 1
