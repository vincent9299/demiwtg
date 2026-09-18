"""wikidata 线编排(纯声明,2026-09-10):truthy dump → 概念属性 → 骨干增肥。

与 flow_kb.py(Phase 1 正文线)的关系:本线是 Phase 2 后半——概念骨干
(qid_concepts.jsonl)缺的 P18/P373(配图指针)与 P31/P279/P361/P527
(关系边)只在维基数据里,此处一趟过滤抽齐并增肥归并。生命周期为
一次性批处理(dump 快照),故独立入口不并进 flow.py 主组合。

管线(过滤段,map_async 推模式):
  from_iter(iter_truthy)     拼接字节流 → bzcat → grep 六谓词预滤
                             (段文件字节精确按序拼接;单流 bz2 无随机访问)
  → WikidataFilterStage      QID 集合成员判定 + NT 值解包(线程池并发)
  → PropsSinkStage           props.jsonl 追加(中间产物,enrich 幂等)

增肥段(纯同步一次性):enrich() 见 operators/wikidata.py。

用法:
  PYTHONPATH=<仓库根> python3 flow_wikidata.py filter \
      --qids <qid_concepts.jsonl> --out props.jsonl \
      --concat-cmd "cat seg0 seg1; ssh pipeline-a cat ...; ..."
  PYTHONPATH=<仓库根> python3 flow_wikidata.py enrich \
      --concepts <qid_concepts.jsonl> --props props.jsonl \
      --out-enriched qid_concepts.fat.jsonl --out-graph qid_graph.jsonl
"""

from __future__ import annotations

import argparse
import os

# 环境代理残留清除(flow.py 同款口径:建客户端之前必须清掉)
for _k in list(os.environ):
    if "proxy" in _k.lower():
        del os.environ[_k]

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="wikidata 线:概念属性抽取编排")
    sub = p.add_subparsers(dest="mode", required=True)
    pf = sub.add_parser("filter", help="truthy 字节流 → props.jsonl")
    pf.add_argument("--qids", required=True, help="qid_concepts.jsonl(成员集)")
    pf.add_argument("--out", required=True, help="props.jsonl 输出")
    pf.add_argument("--concat-cmd", required=True,
                    help="按序输出原始 bz2 字节的 shell 命令"
                         "(本机段 cat + 节点段 ssh cat)")
    pf.add_argument("--concurrency", type=int, default=2)
    pf.add_argument("--log-every", type=int, default=200000)
    pe = sub.add_parser("enrich", help="概念骨干 × props → 增肥+图")
    pe.add_argument("--concepts", required=True)
    pe.add_argument("--props", required=True)
    pe.add_argument("--out-enriched", required=True)
    pe.add_argument("--out-graph", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    from operators.wikidata import (PropsSinkStage, WikidataFilterStage,
                                     enrich, iter_truthy, load_qid_set)

    if args.mode == "enrich":
        r = enrich(args.concepts, args.props, args.out_enriched,
                   args.out_graph)
        print(f"[wikidata] 增肥完成:概念 {r['concepts']:,}(有主图 "
              f"{r['with_p18']:,}),图边 {r['edges']:,}")
        return

    qids = load_qid_set(args.qids)
    print(f"[wikidata] 概念成员集 {len(qids):,}", flush=True)

    def factory():
        return iter_truthy(args.concat_cmd)

    from demiflow.standalone import local_data
    filt = WikidataFilterStage(qids)
    filt.concurrency = args.concurrency
    filt.queue_depth = args.concurrency * 4
    sink = PropsSinkStage(args.out)
    stats = (local_data()
             .from_iter(factory)
             .map_async(filt)
             .map_async(sink)
             .run_stream(log_every=args.log_every))
    print(f"[wikidata] 过滤完成:落盘 {sink.sunk:,} 属性行;"
          f"引擎口径:{stats.summary()}")


if __name__ == "__main__":
    main()
