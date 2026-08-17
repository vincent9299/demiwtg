"""Tier A 声明式通用适配器：JSON spec 驱动的 SourceAdapter 实现。

一个类消化所有「单请求 GET JSON API」型来源；LLM 合成层产出的是 spec 而不是代码，
可静态校验、可 diff 修复、无执行任意代码风险（概念对齐 demiscout Source：
seed = 按别名池派生检索任务；handle = 一次检索请求 + 抽取 records）。

Tier A 表达边界（P2 覆盖度验证结论）：单请求 + 纯投影。需要多步请求链
（wikimedia 检索+imageinfo 联接、inaturalist taxa→observations）、URL 正则
派生、HTML 剥离的源属 Tier C 领域，保持手写代码模式，不强行 spec 化。

spec 结构（collect/specs/<name>.v<N>.json；生成源在 state/collect/specs/）：
{
  "spec_version": 1, "name": ..., "kind": "directed|general",
  "source_kind": "目录|数据集|领域社区|搜索引擎|未授权来源",
  "authorized": bool, "lang": "en|zh|both", "capabilities": [...],
  "network_scope": {"api_hosts": [...], "media_hosts": [...]},
  "limits": {timeout_sec/max_retries/max_response_bytes/min_interval_sec/...},
  "headers": {...},                       # 非敏感头（敏感头由 ScopedHttp 拒绝）
  "search": {
    "url": "...", "params": {"k": "{query}", ...},
    "query_transform": "none|lower_underscore",
    "page_size": {"param": "limit", "factor": 6, "floor": 10, "cap": 40}
  },
  "record": {
    "items": "$ | $.results[*]",          # 条目位置（mini JSONPath）
    "max_records": 40,
    "filters": [{"path": "...", "in": [...] | "exists": true | "startswith": "..."}],
    "fields": {                           # Candidate 字段抽取（见下）
      "asset_id|content_url|landing_url|width|height|size|mime|author|credit|license|score"
    }
  }
}

字段取值三种形态：
- "path"               mini JSONPath；支持 " | " 回退链（$.file_url | $.sample_url）
- {"template": "..."}  {key} 引用条目顶层键（支持点号 a.b）；缺失渲染为空串
- {"const": "..."}     常量
变换（可与 path/template 组合）：transform ∈ first/int/str/mime_by_ext；
mime_by_ext 专用于 MIME：先查 by 字段值映射，未中回退 fallback_ext_of URL 扩展名。
"""

from __future__ import annotations

import json
import re
import urllib.error
from typing import Any, List, Optional

from .config import Job
from .http_scope import NetworkScope, RuntimeLimits, ScopedHttp, UsageMeter
from .models import Candidate, STATUS_CANDIDATE
from .sources.base import SourceAdapter

# Candidate 投影字段（spec record.fields 的合法键）
_FIELD_KEYS = ("asset_id", "content_url", "landing_url", "width", "height",
               "size", "mime", "author", "credit", "license", "score")

_MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff",
}

# 连接层异常（触发熔断）；HTTPError 不熔断（交由 pipeline 记 search_fail）。
_CONN_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


# ---------------------------------------------------------------------------
# mini JSONPath（闭集：$ / $.a.b / [*] 仅末段 / " | " 回退链）
# ---------------------------------------------------------------------------
def _lookup(item: Any, dotted: str) -> Any:
    cur = item
    for part in (dotted or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def resolve_path(item: Any, path: str) -> Any:
    """解析 " | " 回退链；每段为 $.a.b 或 $（根）。首个非空值胜出
    （空串/空列表视为缺失，对齐旧适配器 `a or b` 语义）。"""
    for seg in (path or "").split("|"):
        seg = seg.strip()
        if not seg:
            continue
        if seg == "$":
            v = item
        elif seg.endswith("[*]"):
            v = _lookup(item, seg[2:-3].lstrip("."))
            if isinstance(v, list) and v:
                return v
            v = None
        else:
            v = _lookup(item, seg[2:].lstrip(".") if seg.startswith("$.") else seg)
        if v is not None and v != "" and v != []:
            return v
    return None


def iter_items(data: Any, items_path: str) -> List[dict]:
    if not items_path or items_path.strip() == "$":
        seq = data
    else:
        seq = resolve_path(data, items_path)
    if isinstance(seq, dict):
        seq = [seq]
    return [x for x in (seq or []) if isinstance(x, dict)]


# ---------------------------------------------------------------------------
# 字段抽取
# ---------------------------------------------------------------------------
_TPL_KEY = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def render_template(tpl: str, item: dict) -> str:
    def _sub(m):
        v = _lookup(item, m.group(1))
        return "" if v is None else str(v)
    return _TPL_KEY.sub(_sub, tpl).strip()


def _ext_of_url(url: str) -> str:
    return ((url or "").rsplit(".", 1)[-1].split("?")[0] or "").lower()


def extract_field(item: dict, fspec) -> Any:
    """按字段 spec（str 路径 / {path|template|const|transform...}）抽取值。"""
    if fspec is None:
        return None
    if isinstance(fspec, str):
        return resolve_path(item, fspec)
    if not isinstance(fspec, dict):
        return None
    transform = fspec.get("transform")

    if "const" in fspec:
        v = fspec["const"]
    elif "template" in fspec:
        v = render_template(fspec["template"], item)
        v = v or None
    elif "path" in fspec:
        v = resolve_path(item, fspec["path"])
    else:
        v = None

    if transform == "first":
        if isinstance(v, list):
            v = (v[0] if v else None)
    elif transform == "int":
        v = _to_int(v)
    elif transform == "str":
        v = None if v is None else str(v)
    elif transform == "mime_by_ext":
        by = resolve_path(item, fspec.get("by", "")) if fspec.get("by") else None
        m = dict(_MIME_BY_EXT)
        m.update(fspec.get("map") or {})
        key = str(by or "").lower()
        v = m.get(key) or _MIME_BY_EXT.get(
            _ext_of_url(str(resolve_path(item, fspec.get("fallback_ext_of", "")) or "")), "")
        v = v or None
    return v


def _to_int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _passes_filters(item: dict, filters) -> bool:
    for f in (filters or []):
        v = resolve_path(item, f.get("path", ""))
        if "in" in f:
            if v not in (f["in"] or []):
                return False
        if f.get("exists"):
            if v is None or v == "":
                return False
        if "startswith" in f:
            if not isinstance(v, str) or not v.startswith(f["startswith"]):
                return False
    return True


# ---------------------------------------------------------------------------
# query 变换（闭集词表；新词表项在代码里登记，不允许 spec 注入任意逻辑）
# ---------------------------------------------------------------------------
def transform_query(q: str, name: str) -> str:
    if name in (None, "", "none"):
        return q or ""
    if name == "lower_underscore":
        return re.sub(r"\s+", "_", (q or "").strip().lower())
    raise ValueError(f"未知 query_transform: {name}")


# ---------------------------------------------------------------------------
# 通用适配器
# ---------------------------------------------------------------------------
class GenericSpecAdapter(SourceAdapter):
    """spec 驱动的 SourceAdapter；对 pipeline 与手写适配器完全同构。"""

    def __init__(self, spec: dict, http: Optional[ScopedHttp] = None,
                 meter: Optional[UsageMeter] = None):
        self.spec = spec
        self.name = spec["name"]
        self.source_kind = spec.get("source_kind", "")
        self.lang = spec.get("lang", "en")
        self.is_authorized = bool(spec.get("authorized", False))  # 默认未授权
        self.scope = NetworkScope.from_dict(spec.get("network_scope"))
        self.limits = RuntimeLimits.from_dict(spec.get("limits"))
        # 兼容现有 downloader/filterer：合并双池作为 allowed_suffixes
        self.allowed_suffixes = self.scope.allowed_suffixes
        self.headers = dict(spec.get("headers") or {})
        self.http = http or ScopedHttp(self.scope, self.limits, meter)
        self._conn_dead = False   # 连接级熔断（对齐原 openverse 行为）

    # ---- seed：由 pipeline 的别名池驱动（lang 决定用哪个池），每次一个检索任务 ----
    def search(self, job: Job) -> List[dict]:
        if self._conn_dead:
            return []
        s = self.spec.get("search") or {}
        q = transform_query(job.query, s.get("query_transform"))
        if not q:
            return []
        params = {}
        for k, v in (s.get("params") or {}).items():
            params[k] = v.replace("{query}", q) if isinstance(v, str) else v
        ps = s.get("page_size")
        if ps and ps.get("param"):
            n = (job.effective.target_count or 4) * int(ps.get("factor", 6))
            params[ps["param"]] = min(int(ps.get("cap", 20)),
                                      max(int(ps.get("floor", 10)), n))
        try:
            data = self.http.get_json(s["url"], params=params,
                                      headers=self.headers or None)
        except urllib.error.HTTPError:
            raise  # HTTP 层失败不熔断（限流 403 等是瞬态），交 pipeline 记 search_fail
        except _CONN_ERRORS as e:
            if not self._conn_dead:
                self._conn_dead = True
                print(f"[warn] {self.name} 连接失败，本次运行已熔断"
                      f"（重启进程后恢复）: {e}", flush=True)
            return []
        # HTTPError/BudgetExceeded/NetworkPolicyError 向上抛，由 pipeline 记失败

        rec = self.spec.get("record") or {}
        out: List[dict] = []
        max_records = int(rec.get("max_records") or 40)
        for item in iter_items(data, rec.get("items", "$")):
            if not _passes_filters(item, rec.get("filters")):
                continue
            fields = {k: extract_field(item, fspec)
                      for k, fspec in (rec.get("fields") or {}).items()
                      if k in _FIELD_KEYS}
            if not fields.get("content_url"):
                continue
            fields["_item"] = item
            out.append(fields)
            if len(out) >= max_records:
                break
        return out

    # ---- handle：records 确定性投影为 Candidate（无 LLM、无代码执行）----
    def to_candidate(self, raw: dict, job: Job) -> Candidate:
        q = job.query or ""
        return Candidate(
            source=self.name,
            source_kind=self.source_kind,
            asset_id=str(raw.get("asset_id") or raw["content_url"]),
            instance=job.instance,
            query=q,
            query_lang="zh" if _is_cjk(q) else "en",
            landing_url=raw.get("landing_url") or "",
            content_url=raw["content_url"],
            declared_mime=raw.get("mime"),
            declared_width=_to_int(raw.get("width")),
            declared_height=_to_int(raw.get("height")),
            declared_size=_to_int(raw.get("size")),
            author=raw.get("author"),
            credit=raw.get("credit"),
            license_raw=raw.get("license") or "未知",
            source_authorized=self.is_authorized,
            source_score=raw.get("score"),
            evidence={"item": raw.get("_item")},
            status=STATUS_CANDIDATE,
        )


def _is_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in (s or ""))


def load_spec_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not spec.get("name"):
        raise ValueError(f"spec 缺少 name: {path}")
    if not (spec.get("network_scope") or {}).get("api_hosts"):
        raise ValueError(f"spec 必须声明 network_scope.api_hosts: {path}")
    if not (spec.get("record") or {}).get("fields", {}).get("content_url"):
        raise ValueError(f"spec 必须声明 record.fields.content_url: {path}")
    return spec
