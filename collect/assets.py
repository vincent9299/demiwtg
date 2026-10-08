"""Collection-owned adapter for image metadata and independent object URIs.

Current rows carry image_uri. Frozen legacy snapshots remain readable through
their external Lance descriptors; new published references are ordinary URIs.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import re

class AssetError(ValueError):
    """资产读取契约破坏（缺失、损坏、参数非法）。

    继承 ValueError：消费端既有 (ValueError, OSError) 捕获语义保持不变
    ——损坏显式抛错、缺失可捕获降级，两者不互相吞并。"""


class AssetMissing(AssetError):
    pass


class AssetCorrupted(AssetError):
    pass


class AssetReadError(AssetError):
    pass


@dataclass(frozen=True)
class AssetResolution:
    """一次资产解析的完整结果：状态 + 字节 + 来源身份（不含缓存路径）。"""

    sha256: str
    ext: str | None
    status: str  # ok | missing | corrupt | read_error
    data: bytes | None = None
    byte_size: int | None = None
    actual_sha256: str | None = None
    error: str | None = None
    source: dict = field(default_factory=dict)  # mode/table/lance_version/path



class AssetReader:
    def __init__(self, uri=None, *, datasets_root=None, column='data', id_column='sha256',
                 version=None, storage_options=None, verify=True):
        from project import resolve_root
        from .material_schema import IMAGES_URI
        self.datasets_root = Path(datasets_root) if datasets_root is not None else resolve_root()
        uri = uri or self.datasets_root / IMAGES_URI
        if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',s) for s in (column,id_column)):
            raise ValueError('Invalid Blob column name')
        self.uri=str(uri);self.column=column;self.id_column=id_column
        self.version=version;self.storage_options=dict(storage_options or {})
        self.verify=verify

    def resolve(self,sha256,ext=None,*,version=None):
        if not isinstance(sha256,str) or not re.fullmatch(r'[0-9a-f]{64}',sha256):
            raise AssetError('sha256 must be 64 lowercase hex characters')
        pinned=self.version if version is None else version
        source={'mode':'lake','table':self.uri}
        if urlsplit(self.uri).scheme=='' and not Path(self.uri).exists():
            return AssetResolution(sha256,ext,'missing',source=source)
        try:
            import lance
            ds=lance.dataset(self.uri,version=pinned,storage_options=self.storage_options or None)
            source['lance_version']=ds.version;source[self.id_column]=sha256
            columns = [self.id_column] + (['image_uri'] if 'image_uri' in ds.schema.names else [])
            rows=ds.scanner(columns=columns,filter=f"{self.id_column} = '{sha256}'",with_row_id=True).to_table()
            if rows.num_rows==0:return AssetResolution(sha256,ext,'missing',source=source)
            if rows.num_rows!=1:raise AssetReadError('Duplicate content IDs in Blob table')
            uri = rows['image_uri'][0].as_py() if 'image_uri' in columns else None
            if uri:
                from demiflow.objects import open_object
                with open_object(uri) as stream:
                    value = stream.read()
                source['image_uri'] = uri
            elif self.column in ds.schema.names:
                blob=ds.take_blobs(self.column,ids=[rows['_rowid'][0].as_py()])[0]
                if blob is None:return AssetResolution(sha256,ext,'missing',source=source)
                try:
                    value=blob if isinstance(blob,bytes) else blob.read()
                finally:
                    if not isinstance(blob, bytes):
                        blob.close()
            else:
                return AssetResolution(sha256,ext,'missing',source=source)
        except Exception as exc:
            return AssetResolution(sha256,ext,'read_error',error=str(exc),source=source)
        actual=hashlib.sha256(value).hexdigest()
        if self.verify and actual!=sha256:
            return AssetResolution(sha256,ext,'corrupt',byte_size=len(value),actual_sha256=actual,source=source)
        return AssetResolution(sha256,ext,'ok',data=value,byte_size=len(value),actual_sha256=actual,source=source)

    def read(self,sha256,ext=None,*,version=None):
        result=self.resolve(sha256,ext,version=version)
        if result.status=='missing':raise AssetMissing('Blob bytes not found: '+sha256)
        if result.status=='corrupt':raise AssetCorrupted('Blob content SHA mismatch: '+sha256)
        if result.status=='read_error':raise AssetReadError(result.error)
        return result.data,result.source

    def read_bytes(self,sha256,ext=None):return self.read(sha256,ext)[0]

    def publish(self, sha256):
        """采集生产者流式导出本表对象；已有独立对象直接校验后交付。"""
        import lance
        from demiflow.objects import LocalObjectStore, ObjectRef
        store = LocalObjectStore(self.datasets_root / 'objects')
        store.reference(sha256)  # 查询前验证内容哈希。
        dataset = lance.dataset(self.uri, version=self.version, storage_options=self.storage_options or None)
        columns = [self.id_column] + (['image_uri'] if 'image_uri' in dataset.schema.names else [])
        rows = dataset.to_table(columns=columns, filter=f"{self.id_column} = '{sha256}'", with_row_id=True)
        if rows.num_rows != 1:
            raise AssetMissing('Image must resolve exactly one source row: ' + sha256)
        uri = rows['image_uri'][0].as_py() if 'image_uri' in columns else None
        if uri:
            ref = ObjectRef(uri, sha256)
            ref.verify()
            return ref
        if self.column not in dataset.schema.names:
            raise AssetMissing('Image has no stored object: ' + sha256)
        blob = dataset.take_blobs(self.column, ids=rows['_rowid'].to_pylist())[0]
        if blob is None:
            raise AssetMissing('Image has no stored bytes: ' + sha256)
        try:
            return store.put_stream(blob, sha256=sha256)
        finally:
            blob.close()

from .material_schema import IMAGES_URI
