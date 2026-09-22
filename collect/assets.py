"""Business binding for the authoritative image table; no shard or file fallback."""
from pathlib import Path
from functools import lru_cache
from demiflow.lance.assets import (
    AssetCorrupted,AssetError,AssetMissing,AssetReadError,AssetResolution,BlobAssetReader,
)
from .material_schema import IMAGES_URI


class AssetReader(BlobAssetReader):
    def __init__(self,*,verify=True,datasets_root=None,version=None):
        from project import resolve_root
        self.datasets_root=Path(datasets_root) if datasets_root is not None else resolve_root()
        super().__init__(self.datasets_root/IMAGES_URI,id_column='sha256',verify=verify,
                         version=version,cache_dir=self.datasets_root/'_staging/asset_cache')


@lru_cache(maxsize=8)
def reader_for_root(root):return AssetReader(datasets_root=root)


def default_reader():
    from project import resolve_root
    return reader_for_root(resolve_root())
