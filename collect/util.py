"""安全 HTTP 与通用工具（标准库实现，无第三方依赖）。

下载器职责（历史设计文档已归档）：
HTTPS/host 校验、逐跳 redirect 合法性校验、timeout、429/5xx 有界重试、
流式字节上限、SHA-256、HTML 去标签。
"""

from __future__ import annotations

import fcntl
import hashlib
import html
import http.cookiejar
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

# 命名冲突防护：以 `python3 collect/cli.py` 方式运行时 sys.path[0] 是 collect/
# 目录，collect/queue.py 会遮蔽 stdlib queue。stream/bulk 的临时摘除+恢复方案
# 挡不住延迟导入——ThreadPoolExecutor.__init__ 内部才 `import queue`，届时遮蔽
# 已恢复。本模块是 ThreadPoolExecutor 的首个使用方，在此一次性摘除遮蔽路径
#（collect.queue 的导入均走 `from collect.queue import ...` 全限定，不受影响），
# 并把 stdlib queue 钉进 sys.modules。
_here = os.path.dirname(os.path.abspath(__file__))
for _p in [p for p in sys.path if p and os.path.abspath(p) == _here]:
    sys.path.remove(_p)
if "queue" in sys.modules and getattr(sys.modules["queue"], "__file__", "") and \
        os.path.abspath(sys.modules["queue"].__file__) == os.path.join(_here, "queue.py"):
    del sys.modules["queue"]
import queue as stdqueue  # noqa: F401,E402  钉住 stdlib queue，防延迟导入再被遮蔽

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

# TLS 环境自适应：部分沙箱网络有 MITM 代理（自签 CA），其证书装在系统 CA 库；
# Python 发行版自带 certifi 不含该 CA → 全部 https 校验失败。此处回退到系统 CA 库。
_CA_FALLBACK = "/etc/ssl/certs/ca-certificates.crt"
if os.path.exists(_CA_FALLBACK):
    os.environ.setdefault("SSL_CERT_FILE", _CA_FALLBACK)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _CA_FALLBACK)


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
    """校验 scheme 为 https，且 host 在 allowed_suffixes 内（精确或子域）。

    allowed_suffixes 为 None 时仅做 scheme 校验（https-only），用于未授权爬虫源：
    其图片 host 不可枚举，但下载仍要求 https，且后续由 Pillow 解码把关只存真实图片。
    """
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"拒绝非 https 链接: {url}")
    if allowed_suffixes is None:
        return p
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
                          max_retries: int, allowed_suffixes,
                          opener=_OPENER) -> urllib.request.addinfourl:
    last_url = url
    attempt = 0
    redirects = 0
    while True:
        validate_url(last_url, allowed_suffixes)
        req = urllib.request.Request(last_url, headers=headers)
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                # 手动跟随重定向：逐跳校验目标 host（SSRF 防护）。
                loc = e.headers.get("Location")
                if loc and redirects < MAX_HOPS:
                    last_url = urllib.parse.urljoin(last_url, loc)
                    redirects += 1
                    continue
                raise
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
        redirects += 1
        if redirects >= MAX_HOPS:
            raise urllib.error.HTTPError(
                last_url, resp.status, "too many redirects", resp.headers, None
            )


def _open_retry(url: str, headers: dict, timeout: int, max_retries: int,
                allowed_suffixes, opener=_OPENER) -> urllib.request.addinfourl:
    """在 _open_with_redirects 之上再包一层：对连接层瞬断（SSL EOF / URLError）
    做有界重试，避免单条检索/下载因网络抖动整体失败。HTTP 错误交给内部重试。"""
    last = None
    for attempt in range(max_retries + 1):
        try:
            return _open_with_redirects(url, headers, timeout, max_retries,
                                        allowed_suffixes, opener=opener)
        except urllib.error.HTTPError:
            raise  # HTTP 状态错误由内部按 Retry-After 处理，不再外层重试
        except urllib.error.URLError as e:  # 含 SSL SSLEOFError
            last = e
            if attempt < max_retries:
                time.sleep(min(2 ** (attempt + 1), 30))
                continue
            raise last
    if last is not None:
        raise last
    raise urllib.error.URLError("未知连接错误")


def _build_cookie_opener():
    """构造一个带 cookie jar 的 opener（独立于全局 _OPENER，避免跨源 cookie 串味）。"""
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPCookieProcessor(cj)), cj


def fetch_json_cookie(url: str, *, warmup_url: str = None, warmup_headers: dict = None,
                      allowed_suffixes, params: dict = None, headers: dict = None,
                      timeout: int = 30, max_retries: int = 3) -> dict:
    """同 fetch_json，但先访问 warmup_url 预热 cookie（如百度首页 BAIDUID），再带 cookie
    请求目标——用于破解反爬（百度图片 acjson 的 antiFlag:1 Forbid spider access）。"""
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)
    opener, _ = _build_cookie_opener()
    if warmup_url:
        try:
            _run_with_deadline(
                lambda: _open_with_redirects(
                    warmup_url, dict(DEFAULT_HEADERS) | (warmup_headers or {}),
                    timeout, 0, None, opener=opener),
                _hard_of(timeout))
        except Exception:  # noqa: BLE001  # 预热失败不致命，仍尝试主请求
            pass
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})

    def _do():
        resp = _open_retry(url, h, timeout, max_retries, allowed_suffixes, opener=opener)
        return resp.read().decode("utf-8", "replace")

    body = _run_with_deadline(_do, _hard_of(timeout))
    return json.loads(body)


def fetch_text_cookie(url: str, *, warmup_url: str = None, warmup_headers: dict = None,
                      allowed_suffixes, params: dict = None, headers: dict = None,
                      timeout: int = 30, max_retries: int = 3,
                      max_bytes: int = 8 * 1024 * 1024) -> str:
    """同 fetch_text，但先预热 cookie 再请求（破解反爬）。"""
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)
    opener, _ = _build_cookie_opener()
    if warmup_url:
        try:
            _run_with_deadline(
                lambda: _open_with_redirects(
                    warmup_url, dict(DEFAULT_HEADERS) | (warmup_headers or {}),
                    timeout, 0, None, opener=opener),
                _hard_of(timeout))
        except Exception:  # noqa: BLE001
            pass
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})

    def _do():
        resp = _open_retry(url, h, timeout, max_retries, allowed_suffixes, opener=opener)
        raw = resp.read(max_bytes)
        enc = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(enc, "replace")

    return _run_with_deadline(_do, _hard_of(timeout))


def fetch_json(url: str, *, allowed_suffixes, params: dict = None,
               headers: dict = None, timeout: int = 30,
               max_retries: int = 3) -> dict:
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})

    def _do():
        resp = _open_retry(url, h, timeout, max_retries, allowed_suffixes)
        return resp.read().decode("utf-8", "replace")

    body = _run_with_deadline(_do, _hard_of(timeout))
    return json.loads(body)


def fetch_bytes_capped(url: str, max_bytes: int, *, allowed_suffixes,
                       headers: dict = None, timeout: int = 30,
                       max_retries: int = 3) -> bytes:
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})

    def _do():
        resp = _open_retry(url, h, timeout, max_retries, allowed_suffixes)
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

    return _run_with_deadline(_do, _hard_of(timeout))


def fetch_text(url: str, *, allowed_suffixes, params: dict = None,
               headers: dict = None, timeout: int = 30,
               max_retries: int = 3, max_bytes: int = 8 * 1024 * 1024) -> str:
    """抓取文本页面（HTML/JSON），用于搜索引擎/社区站爬虫（未授权源）。"""
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)
    h = dict(DEFAULT_HEADERS)
    h.update(headers or {})

    def _do():
        resp = _open_retry(url, h, timeout, max_retries, allowed_suffixes)
        raw = resp.read(max_bytes)
        enc = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(enc, "replace")

    return _run_with_deadline(_do, _hard_of(timeout))


def host_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _run_with_deadline(fn, hard_sec: float):
    """整体请求硬超时：open+read 全程计入。socket timeout 只约束单次 recv，
    慢滴答（头部/正文逐字节喂）可让请求无限挂起；这里到点直接抛 TimeoutError，
    挂死的工作线程（daemon）连同连接被放弃，由对端关闭回收。"""
    result: list = [None]
    exc: list = [None]
    done = threading.Event()

    def _worker():
        try:
            result[0] = fn()
        except BaseException as e:  # noqa: BLE001
            exc[0] = e
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if not done.wait(max(hard_sec, 1.0)):
        raise TimeoutError(f"请求超过硬超时 {hard_sec:.0f}s（已放弃挂死连接）")
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def _hard_of(timeout: int) -> float:
    return max(45.0, float(timeout) * 3)


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


class RangeDownloadError(RuntimeError):
    """分段下载致命错误（无 Content-Length / sha256 验收不符等）。

    缓存与账本已清理；调用方决定重试、换镜像或放弃。"""


def _dl_range(url: str, start: int, end: int, path: str, timeout: int = 300) -> None:
    """分段下载 [start, end) 写到 path 的对应偏移（Range + seek 写），有界重试 3 次。"""
    h = dict(DEFAULT_HEADERS)
    h["Range"] = f"bytes={start}-{end - 1}"
    req = urllib.request.Request(url, headers=h)
    for attempt in range(3):
        try:
            got = 0
            with urllib.request.urlopen(req, timeout=timeout) as resp, \
                    open(path, "r+b") as f:
                f.seek(start)
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    f.write(chunk)
            if got != end - start:
                # CDN 重试可能切到不同边缘返回短读/错段，宁重下不静默
                raise IOError(f"分段长度不符: {got} != {end - start}")
            return
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def range_download_file(url: str, path: str, want_sha: str = "", threads: int = 8,
                        limit_bytes: int = 0, timeout: int = 300,
                        label: str = "range-dl") -> int:
    """并行分段下载大文件（分段账本续传 + sha256 验收）。返回下载总字节。

    - 完成标记 path.done 记录“字节数 sha256”：两者都匹配才复用，否则重下；
    - 分段账本 path.segs（pid + start:end）：续传只补缺段，不整文件重下；
    - sha256 验收防分段下载静默损坏：不符删缓存与账本，抛 RangeDownloadError；
    - limit_bytes 截断（试点用）不验 sha：done 记空 sha，全量跑自然重下。
    """
    done = path + ".done"
    if os.path.exists(done) and os.path.exists(path):
        try:
            with open(done) as f:
                parts = f.read().split()
            if int(parts[0]) == os.path.getsize(path) and \
                    (not want_sha or (len(parts) > 1 and parts[1] == want_sha)):
                return os.path.getsize(path)
        except (ValueError, OSError, IndexError):
            pass
    # 探总长（HEAD）
    hreq = urllib.request.Request(url, method="HEAD", headers=dict(DEFAULT_HEADERS))
    with urllib.request.urlopen(hreq, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
    if not total:
        raise RangeDownloadError(f"[{label}] HEAD 无 Content-Length: {url}")
    if limit_bytes:
        total = min(total, limit_bytes)
    seg_done = path + ".segs"
    if os.path.exists(path) and os.path.getsize(path) != total:
        os.remove(path)          # 上次下载目标不同，重下
        if os.path.exists(seg_done):
            os.remove(seg_done)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.truncate(total)
    chunk = max(4 * 1024 * 1024, total // (threads * 16) if total else 0)
    spans = [(s, min(s + chunk, total)) for s in range(0, total, chunk)]
    # 分段完成账本：上次跑挂后续传只补缺段；pid 不匹配（进程换命）则作废。
    # 最终以 sha256 验收兜底，账本误记不会引入脏数据
    done_spans = set()
    try:
        with open(seg_done) as f:
            parts = f.read().split()
        if parts and parts[0] == str(os.getpid()):
            pass  # pid 巧合重叠不可信，作废
        elif parts:
            done_spans = {(int(a), int(b)) for a, b in
                          (p.split(":") for p in parts[1:] if ":" in p)}
    except (OSError, ValueError):
        done_spans = set()
    seg_lock = threading.Lock()
    seg_f = open(seg_done, "a", encoding="utf-8")
    if not done_spans:
        seg_f.truncate(0)
        seg_f.write(f"{os.getpid()}\n")
        seg_f.flush()
    n_done = [0]
    lock = threading.Lock()
    t0 = time.time()

    def _job(span):
        if span in done_spans:
            with lock:
                n_done[0] += 1
            return
        _dl_range(url, span[0], span[1], path, timeout)
        with seg_lock:
            seg_f.write(f"{span[0]}:{span[1]}\n")
            seg_f.flush()
        with lock:
            n_done[0] += 1
            if n_done[0] % 16 == 0 or n_done[0] == len(spans):
                done_mb = n_done[0] * chunk / 1e6
                dt = max(time.time() - t0, 1)
                print(f"[{label}] 缓存 {min(done_mb, total / 1e6):.0f}/{total / 1e6:.0f} MB "
                      f"({done_mb / dt:.1f} MB/s)", flush=True)

    todo = [s for s in spans if s not in done_spans]
    print(f"[{label}] 分段下载：{total / 1e6:.0f} MB / {len(spans)} 段 / "
          f"{threads} 线程"
          + (f"（续传：已完成 {len(done_spans)} 段，只下 {len(todo)} 段）"
             if todo and done_spans else ""), flush=True)
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(_job, todo))
    seg_f.close()
    # sha256 验收（防分段下载静默损坏）：不过则删缓存报错，杜绝 .done 误标；
    # limit_bytes 截断试点本就不完整，不验
    got_sha = ""
    if want_sha and not limit_bytes:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                blk = f.read(8 << 20)
                if not blk:
                    break
                h.update(blk)
        got_sha = h.hexdigest()
        if got_sha != want_sha:
            os.remove(path)
            if os.path.exists(seg_done):
                os.remove(seg_done)   # 账本作废：重下必须从零来
            raise RangeDownloadError(
                f"[{label}] sha256 不符（分段下载被污染），已删除，重跑重下: {path}")
    if os.path.exists(seg_done):
        os.remove(seg_done)           # 验收通过，账本使命结束
    with open(done, "w") as f:
        f.write(f"{total} {got_sha}\n")
    print(f"[{label}] 就绪 {total / 1e6:.0f} MB → {path} ({time.time() - t0:.0f}s)",
          flush=True)
    return total


@contextmanager
def meta_lock(meta_dir: str):
    """跨进程排他锁：串行化对 data/dataset/meta 主清单/索引的写入（分片并发安全）。"""
    if not meta_dir:
        yield
        return
    os.makedirs(meta_dir, exist_ok=True)
    with open(os.path.join(meta_dir, ".meta.lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
