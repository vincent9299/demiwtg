"""中介化 HTTP 与预算值类型（概念对齐 Demiurge demiscout，见架构设计）。

核心原则：源代码（尤其是 LLM 生成的 spec/代码）不持有直接网络能力，
所有请求经 ScopedHttp 中介——host 白名单（NetworkScope 双池）、敏感头托管、
per-host 限速、响应字节上限与请求预算全部由中介强制。

- NetworkScope：api_hosts（检索端点）与 media_hosts（图床）分池声明；
  media_hosts 允许 "*" 通配（媒体 host 不可枚举的聚合源）。
- RuntimeLimits：一等预算对象；超限抛 BudgetExceeded（类型化，上层可识别）。
- UsageMeter：线程安全用量计数（requests/response_bytes），供 run 统计与健康账本。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
from dataclasses import dataclass, field

from .util import DEFAULT_HEADERS, RateLimiter, host_of, validate_url


class NetworkPolicyError(Exception):
    """域外 host / 非法 scheme / 敏感头注入等策略违规。"""


class BudgetExceeded(Exception):
    """运行时预算耗尽（请求数/响应字节/截止时间）。"""


# 敏感头由运行时托管，spec/源代码不得注入（防伪造身份/盗用凭据）。
_CONTROLLED_HEADERS = frozenset(
    {"cookie", "authorization", "proxy-authorization", "set-cookie"})


@dataclass(frozen=True)
class NetworkScope:
    """源级 host 白名单：api（检索）与 media（下载）分池。

    host 匹配沿用现有语义：精确或子域（host == s 或 endswith("." + s)）；
    media_hosts 中的 "*" 表示放行任意 https 媒体 host（聚合源图床不可枚举）。
    """

    api_hosts: tuple = ()
    media_hosts: tuple = ()

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkScope":
        return cls(
            api_hosts=tuple((d or {}).get("api_hosts") or ()),
            media_hosts=tuple((d or {}).get("media_hosts") or ()),
        )

    def _match(self, host: str, pool: tuple) -> bool:
        return any(host == s or host.endswith("." + s) for s in pool)

    def allows_api(self, url: str) -> bool:
        p = _parsed(url)
        return p.scheme == "https" and self._match(p.netloc.lower(), self.api_hosts)

    def allows_media(self, url: str) -> bool:
        p = _parsed(url)
        if p.scheme != "https":
            return False
        if "*" in self.media_hosts:
            return True
        return self._match(p.netloc.lower(), self.media_hosts)

    @property
    def allowed_suffixes(self) -> tuple:
        """合并池（兼容现有 downloader/filterer 的 allowed_suffixes 语义）。"""
        out = list(self.api_hosts)
        for h in self.media_hosts:
            if h != "*" and h not in out:
                out.append(h)
        return tuple(out)


def _parsed(url: str):
    import urllib.parse
    return urllib.parse.urlparse(url)


@dataclass(frozen=True)
class RuntimeLimits:
    """源级预算与节流（一等值对象；0/None = 不启用该项）。"""

    timeout_sec: int = 30
    max_retries: int = 3
    max_response_bytes: int = 8 * 1024 * 1024
    min_interval_sec: float = 1.0      # 检索请求 per-source 最小间隔
    max_requests_per_run: int = 0      # 单 run 检索请求上限（0=不限；探测/合成用）
    deadline_sec: int = 0              # 中介存活总时长上限（0=不限；探测/合成用）

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimeLimits":
        base = cls()
        kw = {k: d[k] for k in cls.__dataclass_fields__ if d and k in d}  # type: ignore[attr-defined]
        return cls(**{**base.__dict__, **kw})


@dataclass
class UsageMeter:
    """线程安全用量计数（进程级；落 run 统计）。"""

    requests: int = 0
    response_bytes: int = 0
    started_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note(self, resp_bytes: int) -> None:
        with self._lock:
            self.requests += 1
            self.response_bytes += resp_bytes

    def snapshot(self) -> dict:
        with self._lock:
            return {"requests": self.requests,
                    "response_bytes": self.response_bytes,
                    "elapsed_sec": round(time.time() - self.started_at, 1)}


class ScopedHttp:
    """源级中介 HTTP 客户端：白名单 + 限速 + 预算 + 敏感头托管。

    spec 驱动适配器只拿到本对象，无法绕过策略直连网络。
    """

    def __init__(self, scope: NetworkScope, limits: RuntimeLimits,
                 meter: UsageMeter = None):
        self.scope = scope
        self.limits = limits
        self.meter = meter or UsageMeter()
        self._rate = RateLimiter(limits.min_interval_sec)
        self._started_at = time.time()

    def _check_api_host(self, url: str) -> None:
        validate_url(url, self.scope.api_hosts or None)  # https + 白名单（SSRF 防护）
        if self.scope.api_hosts and not self.scope.allows_api(url):
            raise NetworkPolicyError(f"域外 api host: {host_of(url)}")

    def _check_headers(self, headers: dict) -> None:
        for k in (headers or {}):
            if k.lower() in _CONTROLLED_HEADERS:
                raise NetworkPolicyError(f"spec 不允许注入敏感头: {k}")

    def _check_budget(self) -> None:
        lim = self.limits
        if lim.max_requests_per_run and self.meter.requests >= lim.max_requests_per_run:
            raise BudgetExceeded(
                f"检索请求数达到预算上限 {lim.max_requests_per_run}")
        if lim.deadline_sec and (time.time() - self._started_at) > lim.deadline_sec:
            raise BudgetExceeded(
                f"运行时长达到预算上限 {lim.deadline_sec}s")

    def get_text(self, url: str, *, params: dict = None,
                 headers: dict = None) -> str:
        """白名单内 GET 拿原始文本（robots.txt/文档页等非 JSON 探测）。"""
        self._check_api_host(url)
        self._check_headers(headers)
        self._check_budget()
        self._rate.acquire(host_of(url))
        from .util import fetch_text
        text = fetch_text(
            url, allowed_suffixes=self.scope.api_hosts or None,
            params=params, headers=headers,
            timeout=self.limits.timeout_sec,
            max_retries=self.limits.max_retries,
            max_bytes=self.limits.max_response_bytes)
        self.meter.note(len(text.encode("utf-8", "ignore")))
        return text

    def get_json(self, url: str, *, params: dict = None,
                 headers: dict = None):
        """白名单内 GET 并解析 JSON；响应字节超限由 fetch_text 抛 ValueError。"""
        text = self.get_text(url, params=params, headers=headers)
        return json.loads(text)
