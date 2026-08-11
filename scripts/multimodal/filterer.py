"""候选筛选（docs 第 6.1 节两阶段原则·阶段二属性检查）。

逐项检查并返回 (通过, 拒绝原因)。配额（标签目标数量）与总预算由管线维护，
因为需要跨候选的计数/累计字节。
"""

from __future__ import annotations

import urllib.parse
from typing import Tuple

from .models import Candidate
from .config import EffectiveConfig


def _norm(text: str) -> str:
    return (text or "").lower().replace("-", " ").strip()


def _url_safe(url: str, allowed_suffixes) -> Tuple[bool, str]:
    if not url:
        return False, "缺少 content_url"
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        return False, f"非 https 链接: {url}"
    host = p.netloc.lower()
    if not any(host == s or host.endswith("." + s) for s in allowed_suffixes):
        return False, f"host 未授权: {host}"
    return True, ""


def filter_candidate(c: Candidate, cfg: EffectiveConfig,
                     allowed_suffixes) -> Tuple[bool, str]:
    """返回 (通过, 拒绝原因)。原因在拒绝时给出明确说明。"""

    # 回源状态：M1 候选均来自 Wikimedia API 元数据，已完成来源验证
    # （若未来来源无法回源验证，应在此返回 False）

    # URL 安全
    ok, reason = _url_safe(c.content_url, allowed_suffixes)
    if not ok:
        return False, reason

    # MIME
    if not c.declared_mime:
        return False, "缺少 MIME 声明"
    if c.declared_mime not in cfg.mime_allowlist:
        return False, f"MIME 不在白名单: {c.declared_mime}"

    # 宽高
    if c.declared_width is None or c.declared_height is None:
        return False, "缺少宽高声明"
    if c.declared_width < cfg.min_width or c.declared_height < cfg.min_height:
        return False, (
            f"宽高不足: {c.declared_width}x{c.declared_height} "
            f"< {cfg.min_width}x{cfg.min_height}"
        )

    # 单文件大小（声明缺失时放行，交由下载器流式封顶）
    if c.declared_size is not None and c.declared_size > cfg.max_file_bytes:
        return False, f"单文件超上限: {c.declared_size} > {cfg.max_file_bytes}"

    # 许可证（原样声明，归一化子串匹配；不推断版本）
    if not c.license_raw:
        return False, "缺少许可证声明"
    nlic = _norm(c.license_raw)
    hit = any(_norm(a) in nlic for a in cfg.license_allowlist)
    if not hit:
        return False, f"许可证不在白名单: {c.license_raw}"

    return True, ""
