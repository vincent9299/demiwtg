"""安全 HTTP 与通用工具（标准库实现，无第三方依赖）。

覆盖 docs/多模态图片采集系统_需求与设计.md 第 6.2 节下载器要求：
HTTPS/host 校验、逐跳 redirect 合法性校验、timeout、429/5xx 有界重试、
流式字节上限、SHA-256、HTML 去标签。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_HEADERS = {
    # Wikimedia 要求 UA 含联系方式/项目标识，否则可能被限流（HTTP 429）。
    "User-Agent": "demiwtg-multimodal/1.0 (https://github.com/vincent9299/demiwtg; image collection research; contact via repo issues)",
    "Accept": "*/*",
}

MAX_HOPS = 6
RETRYABLE = {429, 500, 502, 503, 504}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向，由调用方逐跳校验后再决定跟随。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def validate_url(url: str, allowed_suffixes) -> urllib.parse.ParseResult:
    """校验 scheme 为 https，且 host 在 allowed_suffixes 内（精确或子域）。"""
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"拒绝非 https 链接: {url}")
    host = p.netloc.lower()
    if not any(host == s or host.endswith("." + s) for s in allowed_suffixes):
        raise ValueError(f"拒绝未授权 host: {host} (来自 {url})")
    return p


def _retry_after_sec(headers, attempt: int) -> float:
    """解析 Retry-After（秒或 HTTP 日期）；缺省退避 2^attempt 封顶 30s。"""
    if headers:
        ra = headers.get("Retry-After")
        if ra:
            try:
                return max(0.0, float(ra))
            except ValueError:
                pass
    return min(2 ** attempt, 30)


def _open_with_redirects(url: str, headers: dict, timeout: int,
                          max_retries: int, allowed_suffixes) -> urllib.request.addinfourl:
    last_url = url
    attempt = 0
    while True:
        validate_url(last_url, allowed_suffixes)
        req = urllib.request.Request(last_url, headers=headers)
        try:
            resp = _OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE and attempt < max_retries:
                attempt += 1
                time.sleep(_retry_after_sec(getattr(e, "headers", None), attempt))
                continue
            raise
        if resp.status < 300:
            return resp
        loc = resp.headers.get("Location")
        if not loc:
            raise urllib.error.HTTPError(
                last_url, resp.status, "redirect without Location", resp.headers, None
            )
        last_url = urllib.parse.urljoin(last_url, loc)  # 下一跳继续校验


def fetch_json(url: str, *, allowed_suffixes, params: dict = None,
               headers: dict = None, timeout: int = 30,
               max_retries: int = 3) -> dict:
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})
    resp = _open_with_redirects(url, h, timeout, max_retries, allowed_suffixes)
    body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def fetch_bytes_capped(url: str, max_bytes: int, *, allowed_suffixes,
                       headers: dict = None, timeout: int = 30,
                       max_retries: int = 3) -> bytes:
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})
    resp = _open_with_redirects(url, h, timeout, max_retries, allowed_suffixes)
    buf = bytearray()
    remaining = max_bytes
    while True:
        chunk = resp.read(min(65536, remaining))
        if not chunk:
            break
        buf.extend(chunk)
        remaining -= len(chunk)
        if remaining <= 0:
            extra = resp.read(1)
            if extra:
                raise ValueError(f"内容超过字节上限 {max_bytes}")
            break
    return bytes(buf)


def host_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


class RateLimiter:
    """per-host 最小间隔限速（文档 6.2）。"""

    def __init__(self, min_interval_sec: float = 1.0):
        self.min_interval = max(0.0, min_interval_sec)
        self._last = {}
        self._lock = threading.Lock()

    def acquire(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        while True:
            with self._lock:
                now = time.time()
                last = self._last.get(host)
                if last is None or (now - last) >= self.min_interval:
                    self._last[host] = time.time()
                    return
                wait = self.min_interval - (now - last)
            time.sleep(max(wait, 0))


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text) -> str:
    """去除 HTML 标签并反转义实体，用于提取作者/Credit 纯文本。"""
    if not text:
        return text if isinstance(text, str) else ""
    return html.unescape(_TAG_RE.sub("", text)).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
