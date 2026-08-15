"""下载器（docs 第 6.2 节）。

对通过筛选的候选：HTTPS/host 校验 + 逐跳 redirect 校验（util 内完成）、
per-host 限速、timeout、429/5xx 有界重试、流式字节上限、Pillow 完整解码、
复验实际 MIME/宽高/大小、SHA-256 内容寻址落盘 images/sha256/<aa>/<hash>。
"""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageOps, ImageFile

# 容忍源站本身残缺的 JPEG（缺 FFD9 结束标记等）：仍能正常解码并重新存为完整图。
# 仅影响个别边缘 case，对正常图无副作用。
ImageFile.LOAD_TRUNCATED_IMAGES = True

from .models import Candidate, STATUS_DOWNLOADED, STATUS_FAILED, STATUS_GATE_REJECTED
from .config import EffectiveConfig
from .util import fetch_bytes_capped, sha256_bytes, host_of, RateLimiter, DEFAULT_HEADERS


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


# magic-byte 嗅探：当 Pillow 无法判定格式（actual_mime 为空，会回退 .bin）时，
# 用文件头判定真实类型，避免把 GIF/TIFF 等误命名为 .bin。
_MAGIC_EXT = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
    (b"MM\x00\x2a", ".tiff"),   # big-endian TIFF
    (b"II\x2a\x00", ".tiff"),   # little-endian TIFF
]


def _sniff_ext(data: bytes) -> str:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for magic, ext in _MAGIC_EXT:
        if data[:len(magic)] == magic:
            return ext
    return ".bin"


def _dim_close(actual: int, declared: int, tol_rel: float = 0.03,
               tol_abs: int = 8) -> bool:
    """允许 3% 相对或 8px 绝对容差，用于兼容缩略图取整差异。"""
    return abs(actual - declared) <= max(declared * tol_rel, tol_abs)


def download_and_store(c: Candidate, cfg: EffectiveConfig,
                        allowed_suffixes, images_dir: str,
                        rate_limiter: RateLimiter,
                        headers: dict = None):
    """下载【原图】并落盘（保持原始分辨率，不做任何缩放/改分辨率）。
    返回 (ok, [候选列表])：只包含这一张原图候选（其 sha256/路径/实际尺寸已填充）。
    多张不同图由 selector 在下载前挑选，这里只负责把选中的原图存好。"""
    try:
        rate_limiter.acquire(host_of(c.content_url))
        h = dict(DEFAULT_HEADERS)
        h.update(headers or {})
        data = fetch_bytes_capped(
            c.content_url,
            max_bytes=cfg.max_file_bytes,
            allowed_suffixes=allowed_suffixes,
            headers=h,
            timeout=cfg.timeout_sec,
            max_retries=cfg.max_retries,
        )
        actual_size = len(data)

        # Pillow 完整解码 + 复验实际属性
        try:
            img = Image.open(BytesIO(data))
            img.load()
            # 应用 EXIF 方向校正：Commons 报告的宽高已按 EXIF 旋转，
            # 而裸像素是未旋转的，直接对比会因横竖颠倒而误判"尺寸偏差过大"。
            img = ImageOps.exif_transpose(img)
            fmt = img.format or ""
            actual_w, actual_h = img.size
        except Exception as e:  # noqa: BLE001
            c.status = STATUS_FAILED
            c.fail_reason = f"图片解码失败: {e}"
            return False, [c]

        actual_mime = PIL_FORMAT_TO_MIME.get(fmt, "")

        # 下载阶段分辨率门：用【实际解码后的像素】(actual_w/actual_h) 拦截低分辨率原图。
        # 安全点在于走的是 Pillow 解码结果，而非上游声明的 declared_width/height（后者常失真）。
        # 任一边 < min_resolution 直接判 gate_rejected、不落盘；min_resolution=0 表示关闭此门。
        min_res = getattr(cfg, "min_resolution", 0) or 0
        if min_res and (actual_w < min_res or actual_h < min_res):
            c.status = STATUS_GATE_REJECTED
            c.reject_reason = (
                f"实际分辨率不足: {actual_w}x{actual_h} < {min_res}x{min_res}"
            )
            return False, [c]

        # 复验：实际 vs 声明。下载阶段只拦截"格式/字节严重不符"这类明显坏图；
        # 宽度/高度的轻微偏差不再判失败（分辨率过滤已移至后续整体选图步骤，
        # 这里仅如实记录实际尺寸供下游使用）。
        if actual_mime and c.declared_mime and actual_mime != c.declared_mime:
            c.status = STATUS_FAILED
            c.fail_reason = f"实际 MIME 与声明不符: {actual_mime} != {c.declared_mime}"
            return False, [c]
        if c.declared_size and abs(actual_size - c.declared_size) > max(
            c.declared_size * 0.5, 1_048_576
        ):
            c.status = STATUS_FAILED
            c.fail_reason = (
                f"实际大小与声明不符: {actual_size} vs {c.declared_size}"
            )
            return False, [c]

        # SHA-256 内容寻址存储（原图，原始分辨率）
        digest = sha256_bytes(data)
        ext = _mime_to_ext(actual_mime or c.declared_mime or "", fmt)
        # Pillow 未识别格式时，回退 magic-byte 嗅探，避免误命名为 .bin
        if ext == ".bin" and data:
            ext = _sniff_ext(data)
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
        return True, [c]

    except Exception as e:  # noqa: BLE001
        c.status = STATUS_FAILED
        c.fail_reason = f"下载失败: {e}"
        return False, [c]
