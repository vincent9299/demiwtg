#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 IP 实例自动生成英文查询别名，提升 Commons 召回率。

策略：
  - 读取 data/ip_instances.json 的全部叶子实例（归一化去重）。
  - 已有手工别名（data/ip_query_aliases.json）优先保留，不覆盖。
  - 其余实例批量调用 Google Translate(gtx) 端点翻译为英文，作为 Commons 查询词。
  - 已为纯 ASCII（英文/拉丁）名的实例不再翻译，原样保留。
  - 写出合并后的别名表（{实例名: 英文查询词}）。

批量模式下每个 HTTP 请求携带多个名字（按行分隔），端点按行返回，
段数与输入一一对应；若某批段数不符则对该批回退为逐条翻译。

用法：
  python3 scripts/build_aliases.py
  -> data/ip_query_aliases.json （原地合并，手工条目保留）
"""

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
UA = {"User-Agent": "Mozilla/5.0 (alias-builder)"}


def _norm(name: str) -> str:
    s = name.strip().replace("《", "").replace("》", "")
    s = re.sub(r"（.*?）", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    return s.strip()


def _mk_opener():
    ctx = None
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


_OPENER = _mk_opener()


def translate_batch(names, retries=3):
    """names: list[str] -> list[str]，与输入等长；失败项回退原名。"""
    q = urllib.parse.urlencode({
        "client": "gtx", "sl": "zh-CN", "tl": "en",
        "dt": "t", "q": "\n".join(names),
    })
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{ENDPOINT}?{q}", headers=UA)
            with _OPENER.open(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            segs = data[0]
            out = []
            for s in segs:
                if s and s[0]:
                    out.append(s[0].strip())
                else:
                    out.append("")
            if len(out) == len(names):
                return [o if o else n for o, n in zip(out, names)]
            # 段数不符：逐条回退翻译该批
            return [translate_one(n) for n in names]
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    # 整批失败：逐条兜底
    return [translate_one(n) for n in names]


def translate_one(name, retries=2):
    for attempt in range(retries):
        try:
            q = urllib.parse.urlencode({
                "client": "gtx", "sl": "zh-CN", "tl": "en",
                "dt": "t", "q": name,
            })
            req = urllib.request.Request(f"{ENDPOINT}?{q}", headers=UA)
            with _OPENER.open(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            segs = data[0]
            txt = "".join(s[0] for s in segs if s and s[0]).strip()
            return txt or name
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return name


def is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", default=os.path.join(ROOT, "data", "ip_instances.json"))
    ap.add_argument("--alias", default=os.path.join(ROOT, "data", "ip_query_aliases.json"))
    ap.add_argument("--batch", type=int, default=50,
                    help="每批翻译的名字数量")
    ap.add_argument("--delay", type=float, default=0.2,
                    help="每批之间的间隔（秒）")
    args = ap.parse_args()

    inst = json.load(open(args.instances, encoding="utf-8"))["instances"]
    uniq = sorted({_norm(it) for items in inst.values() for it in items})

    manual = {}
    if os.path.exists(args.alias):
        manual = {_norm(k): v for k, v in json.load(open(args.alias, encoding="utf-8")).items()}

    merged = dict(manual)
    todo = [n for n in uniq if n not in merged]
    print(f"唯一实例: {len(uniq)} | 手工别名: {len(manual)} | 需自动翻译: {len(todo)}")

    auto = {}
    skipped_ascii = 0
    n = len(todo)
    for i in range(0, n, args.batch):
        chunk = todo[i:i + args.batch]
        if all(is_ascii(x) for x in chunk):
            for x in chunk:
                auto[x] = x
            skipped_ascii += len(chunk)
        else:
            res = translate_batch(chunk)
            for x, tr in zip(chunk, res):
                if is_ascii(x):
                    auto[x] = x
                    skipped_ascii += 1
                else:
                    auto[x] = tr if (tr and tr != x) else x
        if (i // args.batch) % 5 == 0:
            print(f"  ... 已处理 {min(i + args.batch, n)}/{n}")
        time.sleep(args.delay)

    merged.update(auto)
    with open(args.alias, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    auto_translated = sum(1 for k, v in auto.items() if v != k and not is_ascii(k))
    print(f"写出别名总数: {len(merged)}")
    print(f"  其中手工保留: {len(manual)}")
    print(f"  其中自动翻译成功(≠原名): {auto_translated}")
    print(f"  其中纯ASCII原样保留: {skipped_ascii}")
    print(f"  翻译失败/无变化回退中文: {len(auto) - auto_translated - skipped_ascii}")
    print(f"-> {args.alias}")


if __name__ == "__main__":
    main()
