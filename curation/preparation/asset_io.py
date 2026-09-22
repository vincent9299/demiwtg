"""V2 图片入口：SHA 是身份，Lance Blob 是唯一字节源。

历史路径只用于溯源和推断扩展名；路径型工具使用可删除的物化缓存。
缺失、损坏、读取失败分别报告，不退回历史文件。
"""
from __future__ import annotations

from pathlib import Path

from collect.assets import (
    AssetCorrupted, AssetError, AssetMissing, AssetReadError,
    default_reader,
)

__all__ = [
    "AssetCorrupted", "AssetError", "AssetMissing", "AssetReadError",
    "asset_bytes", "asset_file", "asset_resolution", "bytes_unchanged",
]


def bytes_unchanged(path, sha256) -> bool:
    """冻结字节复核：路径提示或湖内读取成功且内容 SHA 一致。

    供 observed/断言类校验使用；缺失、损坏、读失败均返回 False，
    不抛错（调用方据此标记 changed_or_missing）。
    """
    try:
        data, _source = asset_bytes(path, sha256)
        return data is not None
    except (AssetMissing, AssetCorrupted, AssetReadError, AssetError, OSError, ValueError):
        return False


def asset_resolution(path, sha256, ext=None):
    """The historical path is provenance only. Authoritative pixels live in Lance."""
    return default_reader().resolve(sha256, ext)


def asset_bytes(path, sha256, ext=None):
    """读取并验证字节；返回 (bytes, source 身份 dict)。"""
    resolution = asset_resolution(path, sha256, ext)
    if resolution.status == "missing":
        raise AssetMissing(f"asset bytes not found: {sha256}")
    if resolution.status == "corrupt":
        raise AssetCorrupted(
            f"Image bytes changed: expected {sha256}, got {resolution.actual_sha256}")
    if resolution.status == "read_error":
        raise AssetReadError(resolution.error)
    return resolution.data, resolution.source


def asset_file(path, sha256, ext=None):
    """Rebuild a disposable file for tools that require a local path."""
    suffix = ext or (Path(path).suffix.lstrip('.').lower() if path else None)
    return default_reader().materialize(sha256, suffix or 'bin')
