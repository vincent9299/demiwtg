"""collect_v2 基础设施层：按源限速、有界重试、并行控制。

契约（用户拍板，见 .qoder/handoff_collect_v2.md §2.3 / §4.5）：
- 按源限速，尽量快但避免被封：两档初值——官方 API 档 2 req/s 并发 2；
  爬虫档 0.5 req/s 并发 1；全局并发上限 8。
- 分类重试：确定性失败（400/401/403/404/410、域名非法）不重试直接抛出；
  瞬态失败（超时/连接重置/429/5xx）重试 3 次、固定间隔 1s，不做指数退避。
- 零业务逻辑：不出现 instance/候选/blobs 概念，只提供通用机制。

对外原语：
- request(source, method, url, ...)   限速 + 分类重试的 HTTP 请求
- stream(source, method, url, ...)    流式版 request（下载算子用，字节封顶在调用方）
- WorkPool(limit)                     全局工作池（并发任务数封顶）
- SourceGate / RateLimiter            供算子按源取用的限流原语
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

# ---------------------------------------------------------------------------
# 配置（两档初值，跑起来按封禁反馈再调）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceLimits:
    rate: float        # 每秒请求数
    concurrency: int   # 该源最大在途请求数


# 官方 API 档：尽量快，按文档/礼仪限制
_API_SOURCES = ("wikimedia", "wikimedia_zh", "inaturalist", "fandom")
# 搜索爬虫档：反爬源，保守
_CRAWLER_SOURCES = ("baidu", "bing", "toutiao", "so360", "huaban_api")

SOURCE_LIMITS: dict[str, SourceLimits] = {
    **{s: SourceLimits(rate=2.0, concurrency=2) for s in _API_SOURCES},
    **{s: SourceLimits(rate=0.5, concurrency=1) for s in _CRAWLER_SOURCES},
}

GLOBAL_CONCURRENCY = 8   # 全局工作池并发上限
MAX_RETRIES = 3          # 瞬态失败重试次数（不含首次）
RETRY_INTERVAL = 1.0     # 重试固定间隔（秒），不做指数退避

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


# ---------------------------------------------------------------------------
# 异常与失败分类
# ---------------------------------------------------------------------------

class InfraError(Exception):
    """基础设施层异常基类。"""


class DeterministicError(InfraError):
    """确定性失败（403/404/域名非法等）：不重试，调用方认缺。"""


class TransientExhaustedError(InfraError):
    """瞬态失败且有界重试已用尽。"""


def classify_status(status: int) -> str:
    """HTTP 状态码分类：ok / transient / deterministic。"""
    if status < 400:
        return "ok"
    if status == 429 or status >= 500:
        return "transient"
    return "deterministic"


def _in_chain(exc: BaseException, target: type) -> bool:
    cur: Optional[BaseException] = exc
    while cur is not None:
        if isinstance(cur, target):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def classify_network_error(exc: Exception) -> Optional[str]:
    """网络异常分类：deterministic / transient；不认识返回 None（原样抛出）。"""
    if isinstance(exc, httpx.TimeoutException):
        return "transient"
    if isinstance(exc, httpx.ConnectError):
        # 域名解析失败 = 域名非法，确定性失败
        if _in_chain(exc, socket.gaierror):
            return "deterministic"
        return "transient"
    if isinstance(exc, httpx.NetworkError):
        return "transient"
    return None


# ---------------------------------------------------------------------------
# 限速原语
# ---------------------------------------------------------------------------

class RateLimiter:
    """最小间隔限速器：同源请求按 1/rate 秒最小间隔串行放行。"""

    def __init__(self, rate: float):
        self._interval = 1.0 / rate
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_at = now + self._interval


class SourceGate:
    """每源闸门：并发信号量 + 限速器。slot() 内发请求。"""

    def __init__(self, limits: SourceLimits):
        self.limits = limits
        self._sem = asyncio.Semaphore(limits.concurrency)
        self._rl = RateLimiter(limits.rate)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._sem:
            await self._rl.acquire()
            yield


_gates: dict[str, SourceGate] = {}


def gate_for(source: str) -> SourceGate:
    """按源名取闸门（惰性创建）。未登记源直接报错，不给默认限速。"""
    gate = _gates.get(source)
    if gate is None:
        limits = SOURCE_LIMITS.get(source)
        if limits is None:
            raise ValueError(f"源 {source!r} 未在限速表 SOURCE_LIMITS 登记")
        gate = _gates[source] = SourceGate(limits)
    return gate


# ---------------------------------------------------------------------------
# 全局工作池
# ---------------------------------------------------------------------------

class WorkPool:
    """全局工作池：在途任务总数封顶；submit 返回 task，join 等全部完成。"""

    def __init__(self, limit: int = GLOBAL_CONCURRENCY):
        self._sem = asyncio.Semaphore(limit)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, coro) -> asyncio.Task:
        task = asyncio.create_task(self._guarded(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _guarded(self, coro):
        async with self._sem:
            return await coro

    async def join(self) -> None:
        # 循环以覆盖等待期间新提交的任务
        while self._tasks:
            await asyncio.wait(list(self._tasks))


# ---------------------------------------------------------------------------
# 带限速与分类重试的 HTTP 请求
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """进程级共享 HTTP 客户端（惰性创建）。冒烟可先 set_client 注入。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    return _client


def set_client(client: httpx.AsyncClient) -> None:
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def request(
    source: str,
    method: str,
    url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    **kwargs,
) -> httpx.Response:
    """对 source 发一次受限速、带分类重试的请求，成功返回响应。

    - 确定性失败：抛 DeterministicError，不重试；
    - 瞬态失败：固定间隔重试 MAX_RETRIES 次后用尽抛 TransientExhaustedError；
    - 未识别异常：原样抛出，不归类。
    """
    gate = gate_for(source)
    http = client or get_client()
    last_exc: Optional[Exception] = None
    last_status: Optional[int] = None

    for attempt in range(MAX_RETRIES + 1):
        async with gate.slot():
            try:
                resp = await http.request(method, url, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 需要分类后决定重试与否
                verdict = classify_network_error(exc)
                if verdict is None:
                    raise
                if verdict == "deterministic":
                    raise DeterministicError(f"{source} {url}: 域名/连接确定性失败") from exc
                last_exc, last_status = exc, None
            else:
                verdict = classify_status(resp.status_code)
                if verdict == "ok":
                    return resp
                if verdict == "deterministic":
                    raise DeterministicError(f"{source} {url}: HTTP {resp.status_code}")
                last_exc, last_status = None, resp.status_code
        # 瞬态失败：固定间隔后重试
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_INTERVAL)

    detail = f"HTTP {last_status}" if last_status else repr(last_exc)
    raise TransientExhaustedError(
        f"{source} {url}: 瞬态失败重试用尽（{MAX_RETRIES} 次）最后状态 {detail}"
    ) from last_exc


@asynccontextmanager
async def stream(
    source: str,
    method: str,
    url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    **kwargs,
) -> AsyncIterator[httpx.Response]:
    """流式版 request：分类/重试只作用于建流与首包状态码。

    建流成功后把响应交给调用方流式读取（调用方负责字节封顶），
    读出阶段的网络异常原样上抛，不重试（下载重头再来代价大，认缺即可）。
    """
    gate = gate_for(source)
    http = client or get_client()
    last_exc: Optional[Exception] = None
    last_status: Optional[int] = None

    for attempt in range(MAX_RETRIES + 1):
        async with gate.slot():
            try:
                req = http.build_request(method, url, **kwargs)
                resp = await http.send(req, stream=True)
            except Exception as exc:  # noqa: BLE001 - 需要分类后决定重试与否
                verdict = classify_network_error(exc)
                if verdict is None:
                    raise
                if verdict == "deterministic":
                    raise DeterministicError(f"{source} {url}: 域名/连接确定性失败") from exc
                last_exc, last_status = exc, None
            else:
                verdict = classify_status(resp.status_code)
                if verdict == "ok":
                    try:
                        yield resp
                    finally:
                        await resp.aclose()
                    return
                await resp.aclose()
                if verdict == "deterministic":
                    raise DeterministicError(f"{source} {url}: HTTP {resp.status_code}")
                last_exc, last_status = None, resp.status_code
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_INTERVAL)

    detail = f"HTTP {last_status}" if last_status else repr(last_exc)
    raise TransientExhaustedError(
        f"{source} {url}: 瞬态失败重试用尽（{MAX_RETRIES} 次）最后状态 {detail}"
    ) from last_exc
