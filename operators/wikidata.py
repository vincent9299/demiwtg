"""kb 线 Phase 2 后半算子(2026-09-10):维基数据精简文件 → 概念属性。

链路定位:wikidata truthy dump(官方导出,全部实体的"属性→值"最简
三元组,~43GB bz2)→ 概念骨干增肥(P18 主图/P373 Commons 分类)+
关系边(P31 实例/P279 上位/P361 组成/P527 包含)。dump 单流 bz2 无
随机访问,分字节段并行下载后按序拼接流式过滤(段文件字节精确)。

行契约(demiflow 原生 dict 行):
- 源头行(iter_truthy 产出,from_iter factory):一行 NT 三元组原文
  `<http://www.wikidata.org/entity/Q64> <.../direct/P18> "Berlin.jpg" .`
  (上游 grep 预滤已只留六谓词行,byte 流由 --concat-cmd 提供)
- 属性行(WikidataFilterStage 产出):{qid:"Q64", prop:"P18",
  value:"Berlin.jpg"}——对象为实体时 value 为 "Q515" 形态

机制与策略分工:机制在 demiflow(from_iter 惰性流/run_stream 背压/
StreamStage 并发声明);策略在本文件(谓词表/QID 集合成员判定/
NT 值解包/增肥归并口径)。幂等:filter 产物为中间件,enrich 侧按
qid dict 归并天然去重,重跑零重复。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from typing import Iterable, Iterator

from demiflow.data.plan import StreamStage

# 六谓词分工:P18/P373 增肥概念骨干;P31/P279/P361/P527 顺路产关系边
IMAGE_PROPS = ("P18", "P373")
GRAPH_PROPS = ("P31", "P279", "P361", "P527")
KEEP_PROPS = IMAGE_PROPS + GRAPH_PROPS

_NT_RE = re.compile(
    rb'<http://www\.wikidata\.org/entity/Q(\d+)> '
    rb'<http://www\.wikidata\.org/prop/direct/(P\d+)> (.+) \.\s*$')
_ENTITY_VAL_RE = re.compile(rb'^<http://www\.wikidata\.org/entity/Q(\d+)>$')
_NT_ESCAPES = {'\\"': '"', "\\\\": "\\", "\\n": "\n"}


def iter_truthy(concat_cmd: str) -> Iterator[dict]:
    """拼接字节流命令 → NT 行流(from_iter 的 factory 体)。

    concat_cmd 输出原始 bz2 字节(段按序拼接),本 factory 负责
    bzcat + grep 六谓词预滤(C 速,砍 ~85% 行量再进 Python)。
    """
    pipe = (f"({concat_cmd}) | bzcat | "
            "grep -aE 'prop/direct/P(18|279|31|361|373|527)>'")
    proc = subprocess.Popen(["bash", "-c", pipe], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    for raw in proc.stdout:
        yield {"line": raw}


class WikidataFilterStage(StreamStage):
    """NT 行 → 属性行(QID 集合成员判定 + 值解包)。"""

    label = "wikidata_filter"

    def __init__(self, qids: set[int]):
        self._qids = qids
        self.seen_props = 0

    async def __call__(self, row: dict):
        m = _NT_RE.match(row["line"].rstrip())
        if not m:
            return None
        prop = m.group(2).decode()
        if prop not in KEEP_PROPS or int(m.group(1)) not in self._qids:
            return None
        val_raw = m.group(3)
        em = _ENTITY_VAL_RE.match(val_raw)
        if em:
            value = "Q" + em.group(1).decode()
        else:
            value = val_raw.decode("utf-8", "replace").strip('"')
            for k, v in _NT_ESCAPES.items():
                value = value.replace(k, v)
        self.seen_props += 1
        return {"qid": "Q" + m.group(1).decode(), "prop": prop, "value": value}


class PropsSinkStage(StreamStage):
    """属性行 → jsonl 追加(中间产物;enrich dict 归并保证幂等)。"""

    label = "props_sink"
    concurrency = 1            # 单写者,顺序追加

    def __init__(self, path: str):
        self._f = open(path, "a", encoding="utf-8")
        self.sunk = 0

    async def __call__(self, row: dict):
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.sunk += 1
        if self.sunk % 200000 == 0:
            self._f.flush()
        return row

    async def aclose(self):
        self._f.flush()
        self._f.close()


def load_qid_set(concepts_path: str) -> set[int]:
    """qid_concepts.jsonl → QID 数字集合(过滤成员判定用,省内存存 int)。"""
    qids: set[int] = set()
    with open(concepts_path, encoding="utf-8") as f:
        for line in f:
            try:
                qids.add(int(json.loads(line)["qid"][1:]))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return qids


def enrich(concepts_path: str, props_path: str, out_enriched: str,
           out_graph: str, graph_report_every: int = 5000000) -> dict:
    """概念骨干 × 属性表 → 增肥版概念 + 关系边(纯同步批处理,一次性)。

    props 先全量载入(图边流式落盘,顺路标 in_corpus=to 是否在概念集);
    概念行逐条归并 p18/p373。重跑幂等:输出整文件重写,输入不变则
    输出不变。
    """
    qids = load_qid_set(concepts_path)   # in_corpus 判定基准=概念全集
    img: dict[int, dict[str, list]] = {}
    n_edges = 0
    with open(props_path, encoding="utf-8") as pf, \
            open(out_graph, "w", encoding="utf-8") as gf:
        for line in pf:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            qn = int(r["qid"][1:])
            if r["prop"] in IMAGE_PROPS:
                key = "p18" if r["prop"] == "P18" else "p373"
                img.setdefault(qn, {"p18": [], "p373": []})[key].append(
                    r["value"])
            else:
                to_n = int(r["value"][1:]) if r["value"].startswith("Q") \
                    else -1
                gf.write(json.dumps({
                    "from": r["qid"], "pred": r["prop"], "to": r["value"],
                    "in_corpus": to_n in qids,
                }, ensure_ascii=False) + "\n")
                n_edges += 1
                if n_edges % graph_report_every == 0:
                    print(f"[enrich] 图边已落 {n_edges}", flush=True)
    n_concepts = n_with_image = 0
    with open(concepts_path, encoding="utf-8") as cf, \
            open(out_enriched, "w", encoding="utf-8") as of:
        for line in cf:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = img.get(int(row["qid"][1:]))
            if d:
                row["p18"], row["p373"] = d["p18"], d["p373"]
                if d["p18"]:
                    n_with_image += 1
            else:
                row["p18"], row["p373"] = [], []
            of.write(json.dumps(row, ensure_ascii=False) + "\n")
            n_concepts += 1
    return {"concepts": n_concepts, "with_p18": n_with_image,
            "edges": n_edges}
