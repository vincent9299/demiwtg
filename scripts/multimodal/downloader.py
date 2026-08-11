"""下载器（docs 第 6.2 节）。

对通过筛选的候选：HTTPS/host 校验 + 逐跳 redirect 校验（util 内完成）、
per-host 限速、timeout、429/5xx 有界重试、流式字节上限、Pillow 完整解码、
复验实际 MIME/宽高/大小、SHA-256 内容寻址落盘 images/sha256/<aa>/<hash>。
"""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image

from .models import Candidate, STATUS_DOWNLOADED, STATUS_FAILED
from .config import EffectiveConfig
from .util import fetch_bytes_capped, sha256_bytes, host_of, RateLimiter


PIL_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _mime_to_ext(mime: str, fmt: str) -> str:
    if mime in MIME_TO_EXT:
        return MIME_TO_EXT[mime]
    if fmt and fmt.lower() in ("jpeg", "png", "gif", "webp", "bmp", "tiff"):
        return "." + fmt.lower()
    return ".bin"


def _dim_close(actual: int, declared: int, tol_rel: float = 0.03,
               tol_abs: int = 8) -> bool:
    """允许 3% 相对或 8px 绝对容差，用于兼容缩略图取整差异。"""
    return abs(actual - declared) <= max(declared * tol_rel, tol_abs)


def download_and_store(c: Candidate, cfg: EffectiveConfig,
                        allowed_suffixes, images_dir: str,
                        rate_limiter: RateLimiter) -> bool:
    """下载并落盘；成功返回 True（已写入 candidate 的 sha256/local_path/actual_*），
    失败返回 False（已写入 candidate.fail_reason，状态置为 failed）。"""
    try:
        rate_limiter.acquire(host_of(c.content_url))
        data = fetch_bytes_capped(
            c.content_url,
            max_bytes=cfg.max_file_bytes,
            allowed_suffixes=allowed_suffixes,
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        actual_size = len(data)

        # Pillow 完整解码 + 复验实际属性
        try:
            img = Image.open(BytesIO(data))
            img.load()
            fmt = img.format or ""
            actual_w, actual_h = img.size
        except Exception as e:  # noqa: BLE001
            c.status = STATUS_FAILED
            c.fail_reason = f"图片解码失败: {e}"
            return False

        actual_mime = PIL_FORMAT_TO_MIME.get(fmt, "")

        # 复验：实际 vs 声明（不一致按策略拒绝，不推断）
        if actual_mime and c.declared_mime and actual_mime != c.declared_mime:
            c.status = STATUS_FAILED
            c.fail_reason = f"实际 MIME 与声明不符: {actual_mime} != {c.declared_mime}"
            return False
        # 宽高允许小幅容差：Wikimedia 缩略图渲染宽度与 API 返回的 thumbwidth
        # 可能差几个像素（取整/算法差异），但比例一致；这里仅拒绝明显偏差
        # （错图、错误页、比例不符），不要求逐像素相等。
        if c.declared_width and not _dim_close(actual_w, c.declared_width):
            c.status = STATUS_FAILED
            c.fail_reason = f"实际宽与声明偏差过大: {actual_w} vs {c.declared_width}"
            return False
        if c.declared_height and not _dim_close(actual_h, c.declared_height):
            c.status = STATUS_FAILED
            c.fail_reason = f"实际高与声明偏差过大: {actual_h} vs {c.declared_height}"
            return False
        if c.declared_size and abs(actual_size - c.declared_size) > max(
            c.declared_size * 0.5, 1_048_576
        ):
            c.status = STATUS_FAILED
            c.fail_reason = (
                f"实际大小与声明不符: {actual_size} vs {c.declared_size}"
            )
            return False

        # SHA-256 内容寻址存储
        digest = sha256_bytes(data)
        ext = _mime_to_ext(actual_mime or c.declared_mime or "", fmt)
        out_dir = os.path.join(images_dir, digest[:2])
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, digest + ext)
        with open(out_path, "wb") as f:
            f.write(data)

        c.sha256 = digest
        c.local_path = out_path
        c.actual_mime = actual_mime or c.declared_mime
        c.actual_width = actual_w
        c.actual_height = actual_h
        c.actual_size = actual_size
        c.status = STATUS_DOWNLOADED
        return True

    except Exception as e:  # noqa: BLE001
        c.status = STATUS_FAILED
        c.fail_reason = f"下载失败: {e}"
        return False
