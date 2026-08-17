# -*- coding: utf-8 -*-
"""探测（六步闭环第 3 步，纯确定性代码，无 LLM）——探测前置原则的执行者。

对 discover 产出的提案做廉价实测：robots.txt 尊重 → 检索端点真实请求 →
规则裁决。真实响应样本随裁决落盘，是 P2 synth 合成完整 spec 的最重要上下文
（不让 LLM 猜响应结构）。

裁决集（闭集，规则判定）：
- ok            JSON 且能取到非空条目（附响应样本）
- empty         JSON 但 0 条目（端点活着，查询词/参数待调）
- blocked       HTTP 401/403/429/5xx（反爬/鉴权）
- not_json      响应非 JSON（HTML 页/JS 渲染/端点猜错）
- unreachable   连接失败/超时/DNS
- robots_blocked robots.txt 明确 Disallow 检索路径（不再请求端点）

预算：每提案硬上限 3 个请求（robots 1 + 查询 2）、单请求 15s、总时长 60s。
产物：probe_report.jsonl（append-only 事件账本）+ 提案文件 status 回写。

用法：python3 collect/cli.py probe [--name NAME] [--reprobe]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
import urllib.error
import urllib.parse

from . import auto_dir
from ..http_scope import NetworkScope, RuntimeLimits, ScopedHttp

# 条目容器候选键（闭集；命中即视为检索结果列表）
_ITEM_KEYS = ("results", "data", "items", "hits", "posts", "images", "docs")
PROBE_QUERIES = 2
PROBE_LIMITS = RuntimeLimits(timeout_sec=15, max_retries=1,
                             min_interval_sec=1.0,
                             max_requests_per_run=1 + PROBE_QUERIES,
                             deadline_sec=60)


def _robots_disallowed(http: ScopedHttp, host: str, url: str) -> bool:
    """robots.txt 粗查：Disallow 行前缀匹配检索路径（失败视为未禁止）。"""
    try:
        text = http.get_text("https://%s/robots.txt" % host)
    except Exception:  # noqa: BLE001
        return False
    path = urllib.parse.urlparse(url).path or "/"
    hit_ua = False
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("user-agent:"):
            hit_ua = s.split(":", 1)[1].strip() == "*" or not hit_ua
        elif hit_ua and s.lower().startswith("disallow:"):
            p = s.split(":", 1)[1].strip()
            if p and path.startswith(p):
                return True
    return False


def _find_items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in _ITEM_KEYS:
            v = data.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):   # 如 {"data": {"items": [...]}}
                for k2 in _ITEM_KEYS:
                    if isinstance(v.get(k2), list):
                        return v[k2]
    return []


def probe_proposal(prop: dict) -> dict:
    """对单个提案执行探测，返回裁决事件（dict）。"""
    p = prop["proposal"]
    host = (p.get("api_host") or "").lower()
    url = p.get("search_url_draft") or ""
    ev = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
          "name": p.get("name"), "cluster": prop.get("cluster"),
          "verdict": "unreachable", "http_status": None,
          "items": 0, "latency_sec": 0.0, "detail": ""}
    scope = NetworkScope(api_hosts=(host,))
    http = ScopedHttp(scope, PROBE_LIMITS)
    t0 = time.time()
    try:
        if _robots_disallowed(http, host, url):
            ev["verdict"], ev["detail"] = "robots_blocked", "robots.txt Disallow"
            return ev
        for q in list(p.get("sample_queries") or [])[:PROBE_QUERIES]:
            draft = p.get("params_draft") or {}
            params = ({k: (v.replace("{query}", q) if isinstance(v, str) else v)
                       for k, v in draft.items()} if draft else {"q": q})
            try:
                data = http.get_json(url, params=params)
            except urllib.error.HTTPError as e:
                ev.update(verdict="blocked", http_status=e.code,
                          detail="HTTP %d (query=%r)" % (e.code, q))
                return ev
            except (json.JSONDecodeError, ValueError):
                ev.update(verdict="not_json",
                          detail="响应非 JSON (query=%r)，疑似 HTML/JS 渲染" % q)
                return ev
            items = _find_items(data)
            if items:
                sample = json.dumps(items[0], ensure_ascii=False)
                ev.update(verdict="ok", items=len(items),
                          probe_sample=sample[:1200],
                          sample_keys=sorted(items[0].keys())[:40]
                          if isinstance(items[0], dict) else [],
                          detail="query=%r" % q)
                return ev
            ev.update(verdict="empty", detail="query=%r" % q)
        return ev
    except urllib.error.HTTPError as e:
        ev.update(verdict="blocked", http_status=e.code, detail="HTTP %d" % e.code)
        return ev
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        ev.update(verdict="unreachable", detail=str(e)[:200])
        return ev
    finally:
        ev["latency_sec"] = round(time.time() - t0, 2)
        ev["requests"] = http.meter.requests


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect probe",
                                 description="提案探测（预算内实测 + 规则裁决）")
    ap.add_argument("--meta", default="data/dataset/meta")
    ap.add_argument("--name", default=None, help="只探测指定提案")
    ap.add_argument("--reprobe", action="store_true",
                    help="重探已有裁决的提案（默认跳过）")
    args = ap.parse_args(argv)

    adir = auto_dir(args.meta)
    prop_dir = os.path.join(adir, "proposals")
    if not os.path.isdir(prop_dir):
        raise SystemExit("无提案目录 %s；先运行: python3 collect/cli.py discover" % prop_dir)
    report_path = os.path.join(adir, "probe_report.jsonl")

    files = sorted(f for f in os.listdir(prop_dir) if f.endswith(".json"))
    if args.name:
        files = [f for f in files if f[:-5] == args.name]
    n = 0
    for fn in files:
        path = os.path.join(prop_dir, fn)
        with open(path, encoding="utf-8") as f:
            prop = json.load(f)
        if prop.get("status") in ("probed_ok", "probed_fail") and not args.reprobe:
            print("[skip] %s 已探测（status=%s）" % (fn[:-5], prop["status"]))
            continue
        print("[probe] %s ..." % fn[:-5], end=" ", flush=True)
        ev = probe_proposal(prop)
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        prop["status"] = "probed_ok" if ev["verdict"] == "ok" else "probed_fail"
        prop["last_probe"] = ev
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prop, f, ensure_ascii=False, indent=1)
        n += 1
        print("%s（%d 条目，%.1fs，%d 请求；%s）" % (
            ev["verdict"], ev["items"], ev["latency_sec"],
            ev.get("requests", 0), ev["detail"] or "-"))

    if n == 0:
        print("没有可探测的提案（--reprobe 可重探）。")
    else:
        print("完成：探测 %d 个，事件账本 %s（人工过目；晋升闸门在 P2 verify）"
              % (n, report_path))


if __name__ == "__main__":
    main()
