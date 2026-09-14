"""合流线编排(纯声明,2026-09-10):wiki 语料素材 → 统一 docs 池。

与 flow_kb.py(素材生产)/flow_wikidata.py(骨干增肥)的关系:本线是
原计划的"合流"收口——素材层唯一消费者,顺序读一遍,清洗落池记账。

管线(全部 map_async 推模式):
  from_iter(iter_corpus_rows)  parts gz 流 + page_id→qid 挂接
  → WikiCleanStage             wikitext 清洗+切段+质量门(线程池并发)
  → QidDocsSinkStage           pages/<aa>/<sha(url)>.md + qid_docs.jsonl
                               (AppendManifestStore,幂等续跑零重复)

运行(训练机器上,PYTHONPATH=<仓库根>):
  python3 flow_docs.py --parts "kb_parts/pages-*.jsonl.gz" \
      --concepts meta/qid_concepts.fat.jsonl \
      --pages-root <datasets>/demiwtg/pages --manifest meta/qid_docs.jsonl
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import time

for _k in list(os.environ):
    if "proxy" in _k.lower():
        del os.environ[_k]

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="合流线:wiki 语料 → docs 池")
    p.add_argument("--parts", required=True,
                   help="语料 parts glob(按文件名排序顺序读)")
    p.add_argument("--concepts", required=True,
                   help="增肥版 qid_concepts.jsonl(建 page_id→qid 映射)")
    p.add_argument("--pages-root", required=True, help="pages 池根")
    p.add_argument("--manifest", required=True, help="qid_docs.jsonl 路径")
    p.add_argument("--limit", type=int, default=0, help="最多行数(冒烟)")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--log-every", type=int, default=100000)
    return p.parse_args()


def build_qid_maps(concepts_path: str) -> dict[str, dict[int, str]]:
    """增肥概念 → {lang: {page_id: qid}}(挂接用,~8.7M 条)。"""
    maps: dict[str, dict[int, str]] = {"en": {}, "zh": {}}
    with open(concepts_path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for lang in ("en", "zh"):
                side = row.get(lang)
                if side and side.get("page_id"):
                    maps[lang][side["page_id"]] = row["qid"]
    return maps


def iter_corpus_rows(parts: list[str], maps: dict[str, dict[int, str]]):
    """parts 顺序流 → 挂 qid 的语料行(from_iter 的 factory 体)。"""

    def factory():
        for path in parts:
            with gzip.open(path, "rt", encoding="utf-8",
                           errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    qid = maps.get(row.get("lang"), {}).get(
                        row.get("page_id"))
                    if qid:
                        row["qid"] = qid
                    yield row

    return factory


def main() -> None:
    args = parse_args()
    from operators.wiki_clean import QidDocsSinkStage, WikiCleanStage
    from demiflow.standalone import local_data

    parts = sorted(glob.glob(args.parts))
    if not parts:
        raise SystemExit(f"parts glob 无匹配:{args.parts}")
    print(f"[docs] {len(parts)} 个 part;建 qid 映射…", flush=True)
    t0 = time.time()
    maps = build_qid_maps(args.concepts)
    print(f"[docs] 映射就绪 en={len(maps['en']):,} zh={len(maps['zh']):,}"
          f"({time.time() - t0:.0f}s)", flush=True)

    clean = WikiCleanStage()
    clean.concurrency = args.concurrency
    clean.queue_depth = args.concurrency * 2
    sink = QidDocsSinkStage(pages_root=args.pages_root,
                            manifest=args.manifest)
    src = local_data().from_iter(
        iter_corpus_rows(parts, maps))
    if args.limit:
        src = src.limit(args.limit)
    stats = (src
             .map_async(clean)
             .map_async(sink)
             .run_stream(log_every=args.log_every))
    print(f"[docs] 合流完成:落盘 {sink.sunk:,} 文档,认缺 "
          f"{clean.skipped:,}(重定向/消歧义/过短/无QID);"
          f"引擎口径:{stats.summary()}")


if __name__ == "__main__":
    main()
