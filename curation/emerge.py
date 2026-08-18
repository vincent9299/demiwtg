#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emerge.py —— taxonomy 涌现缺口分析（数据有树无，试点流水线）。

设计：自采数据被 taxonomy 查询词过滤过，直接聚类只会重现已有概念（循环
论证）。破局点是两池对照：
- 异常池（kb_match <= LOW_KM）：体系没能解释的异类图，新概念提议主来源；
- 对照组（kb_match >= HIGH_KM 抽样）：聚类后理应能对齐回现有实例，
  对齐率是 embedding+聚类质量的自检指标。

流水线（每步独立、断点续跑）：
    embed   caption 文本 embedding → state/emerge/{index_<pool>.jsonl, emb_<pool>_*.npz}
    cluster UMAP 降维 + HDBSCAN 聚类 → state/emerge/clusters_<pool>.jsonl
    name    LLM 给簇命名概念（27B，llm_common）→ name_cache.jsonl
    align   与 instances.json 对齐三分类 new/existing/noise → align_cache.jsonl
    report  汇总差异报告 → state/emerge/report.json（人审入树，不自动写体系）

embedding 后端（环境变量切换）：
    EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL
        OpenAI 兼容 /v1/embeddings 端点（如 DashScope text-embedding-v4）；
        BASE_URL 与 API_KEY 缺省回退 LLM_BASE_URL / LLM_API_KEY
    未配 EMBED_MODEL 且装了 sentence_transformers 时回退本地
    BAAI/bge-small-zh-v1.5 CPU 推理（--backend local 强制）

约定（AGENTS.md）：代码归 curation/；产物全部落 state/emerge/（2.3 已登记）；
images.jsonl 只读；blobs 不碰（首轮纯文本聚类，不读图字节）。

用法：
    python3 curation/emerge.py embed [--pool both] [--limit N]
    python3 curation/emerge.py cluster [--pool both]
    python3 curation/emerge.py name [--pool anom] [--min-size N]
    python3 curation/emerge.py align [--pool anom] [--instances PATH]
    python3 curation/emerge.py report
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根入 sys.path
from taxonomy import llm_common  # noqa: E402

POOLS = ("anom", "ctrl")
LOW_KM = 3          # 异常池阈值：kb_match <= 3
HIGH_KM = 7         # 对照组阈值：kb_match >= 7
CTRL_SAMPLE = 5000  # 对照组抽样上限（确定性抽样）
MIN_CLUSTER_SIZE = 10
N_REPR = 5          # 每簇取几条代表 caption 喂 LLM
HTTP_TIMEOUT = 60
HTTP_BATCH = 16     # embedding API 每批条数
HTTP_WORKERS = 4    # embedding 并发线程数
ALIGN_TOPK = 3      # 对齐时给 LLM 的候选实例数
DESC_CHARS = 120    # 候选实例 desc 截断
ANCHOR_CHARS = 150  # anchor_path 截断（防 LLM 跑飞）


def repo_root_from_meta(meta: Path) -> Path:
    """AGENTS.md 约定：仓库根由 --meta（默认 data/dataset/meta）向上三级推导。"""
    return meta.resolve().parent.parent.parent


def emerge_dir(meta: Path) -> Path:
    d = repo_root_from_meta(meta) / "state" / "emerge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def norm(s: str) -> str:
    return "".join(str(s).lower().split())


# ---------------------------------------------------------------------------
# embedding 后端
# ---------------------------------------------------------------------------

def http_embed(texts: list, base_url: str, api_key: str, model: str) -> list:
    """OpenAI 兼容 /v1/embeddings 批量调用；4 次指数退避重试。"""
    url = base_url.rstrip("/") + "/embeddings"
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % api_key})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            return [d["embedding"] for d in
                    sorted(data["data"], key=lambda x: x["index"])]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError("embedding API 连续失败: %s" % last)


_ST_MODEL = None


def local_embed(texts: list) -> list:
    """sentence-transformers bge-small-zh-v1.5 CPU 兜底（懒加载，多核批量）。"""
    global _ST_MODEL
    if _ST_MODEL is None:
        import torch
        torch.set_num_threads(min(16, os.cpu_count() or 4))
        from sentence_transformers import SentenceTransformer
        _ST_MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _ST_MODEL.encode(texts, normalize_embeddings=True, batch_size=64,
                            show_progress_bar=False).tolist()


def make_embedder(args):
    """返回 (embed_fn, 后端描述)；http 优先，--backend local 强制本地。"""
    if getattr(args, "backend", "auto") != "local":
        model = os.environ.get("EMBED_MODEL", "")
        if model:
            base = (os.environ.get("EMBED_BASE_URL")
                    or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
            key = os.environ.get("EMBED_API_KEY") or os.environ.get("LLM_API_KEY", "")
            if not base or not key:
                raise SystemExit("EMBED_MODEL 已设但缺少端点/密钥："
                                 "EMBED_BASE_URL(或 LLM_BASE_URL) 与 "
                                 "EMBED_API_KEY(或 LLM_API_KEY)")
            return (lambda ts: http_embed(ts, base, key, model)), \
                "http:%s/%s" % (base, model)
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        raise SystemExit(
            "无可用 embedding 后端：设置 EMBED_MODEL（+ EMBED_BASE_URL/"
            "EMBED_API_KEY，缺省回退 LLM_*）走 API；或 pip install "
            "sentence-transformers 走本地 CPU")
    return local_embed, "local:bge-small-zh-v1.5"


# ---------------------------------------------------------------------------
# embed：images.jsonl → 两池 caption embedding
# ---------------------------------------------------------------------------

def _load_index(edir: Path, pool: str) -> list:
    path = edir / ("index_%s.jsonl" % pool)
    rows = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _load_embeddings(edir: Path, pool: str) -> "np.ndarray | None":
    """按文件名序拼接分块 npz（断点续跑的落盘形态）。"""
    parts = sorted(edir.glob("emb_%s_*.npz" % pool))
    if not parts:
        return None
    return np.concatenate([np.load(p)["emb"] for p in parts])


def embed(args):
    meta = Path(args.meta)
    edir = emerge_dir(meta)
    fn, backend = make_embedder(args)
    print("embedding 后端: %s" % backend)

    pools = POOLS if args.pool == "both" else (args.pool,)
    done = {p: {r["sha256"] for r in _load_index(edir, p)} for p in pools}
    counts = {p: len(done[p]) for p in pools}
    todo = {p: [] for p in pools}

    # 对照组候选：一次扫描收集高匹配 sha256；确定性抽样 = 排序取前 CTRL_SAMPLE
    ctrl_sel = None
    need_ctrl = "ctrl" in pools
    if need_ctrl:
        hi = []
        with open(meta / "images.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                km = r.get("kb_match")
                if km is not None and km >= HIGH_KM and (r.get("caption") or "").strip():
                    hi.append(r["sha256"])
        ctrl_sel = set(sorted(hi)[:CTRL_SAMPLE])
        print("对照组候选 %d 张，抽样上限 %d" % (len(hi), CTRL_SAMPLE))

    n_rows = 0
    with open(meta / "images.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n_rows += 1
            km = rec.get("kb_match")
            cap = (rec.get("caption") or "").strip()
            sha = rec.get("sha256")
            if km is None or not cap or not sha:
                continue
            for p in pools:
                if p == "anom" and km <= LOW_KM:
                    pass
                elif p == "ctrl" and km >= HIGH_KM and sha in ctrl_sel:
                    pass
                else:
                    continue
                if sha in done[p] or args.limit and counts[p] + len(todo[p]) >= args.limit:
                    continue
                todo[p].append({"sha256": sha, "caption": cap, "kb_match": km,
                                "instances": rec.get("instances") or []})

    print("主清单 %d 行；本轮待 embed：%s（已存量 %s）" % (
        n_rows, {p: len(todo[p]) for p in pools},
        {p: counts[p] for p in pools}))
    total = {p: counts[p] + len(todo[p]) for p in pools}

    from concurrent.futures import ThreadPoolExecutor
    for p in pools:
        if not todo[p]:
            continue
        idx_f = open(edir / ("index_%s.jsonl" % p), "a", encoding="utf-8")
        seq = len(list(edir.glob("emb_%s_*.npz" % p)))
        ex = ThreadPoolExecutor(max_workers=HTTP_WORKERS)
        buf_rows: list = []
        buf_vecs: list = []

        def flush():
            """攒满即落盘：index 追加写 + embedding 新分块（断点续跑粒度）。"""
            nonlocal seq
            if not buf_rows:
                return
            for r in buf_rows:
                idx_f.write(json.dumps(r, ensure_ascii=False) + "\n")
            idx_f.flush()
            np.savez_compressed(
                edir / ("emb_%s_%04d.npz" % (p, seq)),
                emb=np.asarray(buf_vecs, dtype=np.float32))
            seq += 1
            print("[%s] 已 embed %d / %d" % (p, counts[p] + len(buf_rows),
                                              total[p]),
                  flush=True)
            counts[p] += len(buf_rows)
            buf_rows.clear()
            buf_vecs.clear()

        try:
            for i in range(0, len(todo[p]), HTTP_BATCH * HTTP_WORKERS):
                wave = todo[p][i:i + HTTP_BATCH * HTTP_WORKERS]
                batches = [wave[j:j + HTTP_BATCH]
                           for j in range(0, len(wave), HTTP_BATCH)]
                futures = [(b, ex.submit(fn, [r["caption"] for r in b]))
                           for b in batches]
                for b, fut in futures:
                    buf_vecs.extend(fut.result())
                    buf_rows.extend(b)
                if len(buf_rows) >= 2000:
                    flush()
            flush()
        finally:
            ex.shutdown(wait=True)
            idx_f.close()
    print("embed 完成。")


# ---------------------------------------------------------------------------
# cluster：UMAP + HDBSCAN
# ---------------------------------------------------------------------------

def cluster(args):
    edir = emerge_dir(Path(args.meta))
    pools = POOLS if args.pool == "both" else (args.pool,)
    for p in pools:
        rows = _load_index(edir, p)
        emb = _load_embeddings(edir, p)
        if not rows or emb is None or len(emb) != len(rows):
            print("[%s] 缺 embedding 产物（rows=%d emb=%s），先跑 embed；跳过" % (
                p, len(rows), "无" if emb is None else len(emb)))
            continue
        print("[%s] %d 条向量，UMAP 降维中（多核，不固定种子）..." % (p, len(rows)))
        import umap
        from sklearn.cluster import HDBSCAN
        red = umap.UMAP(n_components=10, metric="cosine",
                        n_neighbors=15).fit_transform(emb)
        print("[%s] HDBSCAN 聚类中（min_cluster_size=%d）..." % (
            p, args.min_cluster_size))
        labels = HDBSCAN(min_cluster_size=args.min_cluster_size,
                         min_samples=5).fit_predict(red)
        clusters = {}
        for idx, lab in enumerate(labels):
            if lab == -1:
                continue
            clusters.setdefault(int(lab), []).append(idx)
        out = sorted(
            ({"pool": p, "cluster_id": cid, "size": len(m), "members": m}
             for cid, m in clusters.items()),
            key=lambda c: -c["size"])
        for i, c in enumerate(out):
            c["cluster_id"] = i            # 重编号：按规模降序，稳定可读
        path = edir / ("clusters_%s.jsonl" % p)
        with open(path, "w", encoding="utf-8") as f:
            for c in out:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        n_noise = int((labels == -1).sum())
        print("[%s] 簇 %d 个（成员 %d），噪声点 %d（占 %.1f%%）→ %s" % (
            p, len(out), sum(c["size"] for c in out), n_noise,
            100 * n_noise / len(labels), path))


# ---------------------------------------------------------------------------
# name：LLM 给簇命名概念
# ---------------------------------------------------------------------------

_NAME_SYSTEM = """你是图片数据集的标签体系策展专家。给你同一个聚类簇里的若干条
图片画面描述（caption），它们来自「与已知实体标签匹配度很低」或「高匹配度」
的图片池。请归纳这个簇共同描绘概念，严格按 JSON 输出：
{"name":"概念名（中文实体/主题名，4-12字）","desc":"50-100字概念界定，说明该簇画面的共同主题",
"coherent":true或false,"reason":"30字内判断依据"}
若这些 caption 彼此无关、只是噪声凑在一起，coherent 置 false 且 name 输出"噪声"。"""


def _representatives(edir: Path, pool: str, members: list,
                     rows: list, emb: "np.ndarray") -> list:
    """离簇质心最近的 N_REPR 条成员索引。"""
    sub = emb[members]
    cent = sub.mean(axis=0)
    d = ((sub - cent) ** 2).sum(axis=1)
    near = [members[i] for i in np.argsort(d)[:N_REPR]]
    return [rows[i]["caption"] for i in near]


def name_clusters(args):
    edir = emerge_dir(Path(args.meta))
    pools = POOLS if args.pool == "both" else (args.pool,)
    if not args.dry_run:
        llm_common.require_api_key()
    client = None if args.dry_run else llm_common.make_client()
    cache = llm_common.JsonlCache(str(edir / "name_cache.jsonl"))
    done = set() if args.overwrite else cache.done_keys()
    n_ok = n_skip = 0
    for p in pools:
        cpath = edir / ("clusters_%s.jsonl" % p)
        if not cpath.exists():
            print("[%s] 缺 clusters 文件，先跑 cluster；跳过" % p)
            continue
        rows = _load_index(edir, p)
        emb = _load_embeddings(edir, p)
        with open(cpath, encoding="utf-8") as f:
            clusters = [json.loads(l) for l in f if l.strip()]
        for c in clusters:
            if c["size"] < args.min_size:
                continue
            key = "%s:%d" % (p, c["cluster_id"])
            if key in done:
                n_skip += 1
                continue
            caps = _representatives(edir, p, c["members"], rows, emb)
            user = ("池：%s（%s）\n簇规模：%d\n代表 caption：\n%s" % (
                p, "异常池 kb_match<=3" if p == "anom" else "对照组 kb_match>=7",
                c["size"], "\n".join("- %s" % x for x in caps)))
            if args.dry_run:
                print("=" * 60)
                print("[dry-run] key=%s\n-- user --\n%s" % (key, user))
                continue
            try:
                out = llm_common.generate(client, _NAME_SYSTEM, user,
                                          use_responses=llm_common.want_responses()) or {}
            except Exception as e:  # noqa: BLE001
                print("[warn] %s LLM 失败: %s" % (key, e))
                cache.append(key, {"error": str(e)}, ok=False)
                continue
            rec = {"name": str(out.get("name") or "").strip()[:40],
                   "desc": str(out.get("desc") or "").strip()[:300],
                   "coherent": bool(out.get("coherent", True)),
                   "reason": str(out.get("reason") or "").strip()[:100],
                   "pool": p, "cluster_id": c["cluster_id"], "size": c["size"]}
            cache.append(key, rec, ok=True)
            n_ok += 1
            print("[%s] 簇#%d(size=%d) → %s" % (p, c["cluster_id"], c["size"],
                                                 rec["name"] or "?"))
    print("命名完成：新增 %d，跳过（缓存）%d。" % (n_ok, n_skip))


# ---------------------------------------------------------------------------
# align：与 instances.json 对齐三分类
# ---------------------------------------------------------------------------

_ALIGN_SYSTEM = """你是标签体系策展专家。一个图片聚类簇已被命名成一个概念，
请判断它与现有标签体系的关系，严格按 JSON 输出：
{"verdict":"new|existing|noise",
"matched_instance":"verdict=existing 时填对应的现有实例名，否则空串",
"anchor_path":"verdict=new 时建议锚定的体系路径（格式形如 根 / 一级 / 二级），可参考候选实例的 taxonomy_paths，拿不准就写最可能的一级分支",
"confidence":0到1的小数,"reason":"40字内依据"}
判定标准：existing=该概念就是候选中的某个现有实体（含别名/俗称）；
new=确是有价值的新概念且候选里没有；noise=无意义杂烩或不构成概念。"""


def _instance_embed_texts(insts: list) -> list:
    """实例检索文本：name + 别名（前3）。"""
    out = []
    for it in insts:
        parts = [it["name"]] + list(it.get("aliases") or [])[:3]
        out.append("；".join(parts))
    return out


def _load_instance_emb(edir: Path, insts: list, fn) -> "np.ndarray":
    """实例名 embedding 缓存（全量重算只发生一次，之后读盘）。"""
    path = edir / "instance_emb.npz"
    stamp = "%d:%s" % (len(insts), insts[0]["name"] if insts else "")
    if path.exists():
        d = np.load(path, allow_pickle=True)
        if str(d.get("stamp", "")) == stamp and len(d["emb"]) == len(insts):
            return d["emb"]
    print("计算 %d 个实例的 embedding（一次性，缓存 instance_emb.npz）..." %
          len(insts))
    texts = _instance_embed_texts(insts)
    embs = []
    for i in range(0, len(texts), HTTP_BATCH):
        embs.extend(fn(texts[i:i + HTTP_BATCH]))
        if (i // HTTP_BATCH) % 50 == 0:
            print("  实例 embedding %d / %d" % (min(i + HTTP_BATCH, len(texts)),
                                                len(texts)))
    arr = np.asarray(embs, dtype=np.float32)
    np.savez_compressed(path, emb=arr, stamp=stamp)
    return arr


def _align_llm(client, cluster_name: dict, caps: list, cands: list,
               use_responses: bool) -> dict:
    cand_txt = "\n".join(
        "- %s（别名：%s；路径：%s；简介：%s）" % (
            c["name"], "/".join(c.get("aliases") or [])[:20] or "-",
            " | ".join((c.get("taxonomy_paths") or [])[:2]),
            (c.get("desc") or "")[:DESC_CHARS])
        for c in cands)
    user = ("簇概念：%s\n概念界定：%s\n簇规模：%d\n代表 caption：\n%s\n\n"
            "现有体系中最相近的候选实例：\n%s" % (
                cluster_name["name"], cluster_name["desc"],
                cluster_name["size"],
                "\n".join("- %s" % x for x in caps[:3]), cand_txt or "（无）"))
    # generate 失败时的兜底：llm_common.extract_json 已能剥代码块/杂文；
    # 这里再对含 verdict 的子串做一次提取（对齐字段可能被多余大括号包裹）
    out = llm_common.generate(client, _ALIGN_SYSTEM, user,
                              use_responses=use_responses)
    if out is None:
        return {}
    if "verdict" in out:
        return out
    return {}


def align(args):
    edir = emerge_dir(Path(args.meta))
    root = repo_root_from_meta(Path(args.meta))
    pools = POOLS if args.pool == "both" else (args.pool,)

    with open(args.instances, encoding="utf-8") as f:
        insts = json.load(f).get("instances") or []
    by_norm = {}
    for it in insts:
        by_norm.setdefault(norm(it["name"]), []).append(it)
        for a in it.get("aliases") or []:
            by_norm.setdefault(norm(a), []).append(it)

    fn, backend = make_embedder(args)
    inst_emb = _load_instance_emb(edir, insts, fn)
    inst_norm = np.linalg.norm(inst_emb, axis=1, keepdims=True) + 1e-9

    if not args.dry_run:
        llm_common.require_api_key()
    client = None if args.dry_run else llm_common.make_client()
    cache = llm_common.JsonlCache(str(edir / "align_cache.jsonl"))
    done = set() if args.overwrite else cache.done_keys()
    n_ok = n_skip = 0
    for p in pools:
        npath = edir / "name_cache.jsonl"
        if not npath.exists():
            print("缺 name_cache.jsonl，先跑 name；跳过")
            return
        rows = _load_index(edir, p)
        emb = _load_embeddings(edir, p)
        for key, named in cache_records_of(edir, "name_cache.jsonl").items():
            if named.get("pool") != p or not named.get("coherent", True):
                continue
            c = _cluster_by_id(edir, p, named["cluster_id"])
            if c is None or c["size"] < args.min_size:
                continue
            if key in done:
                n_skip += 1
                continue
            # 1) 确定性捷径：名称/别名精确命中
            hits = by_norm.get(norm(named["name"])) or []
            # 2) embedding 最近邻候选
            q = _cluster_centroid(emb, c["members"])
            sims = (inst_emb @ q) / (inst_norm * (np.linalg.norm(q) + 1e-9))
            topk = [insts[i] for i in np.argsort(-sims)[:ALIGN_TOPK]]
            if hits:
                verdict = {"verdict": "existing",
                           "matched_instance": hits[0]["name"],
                           "anchor_path": "", "confidence": 0.95,
                           "reason": "名称/别名精确命中现有实例"}
            else:
                caps = _representatives(edir, p, c["members"], rows, emb)
                if args.dry_run:
                    print("=" * 60)
                    print("[dry-run] key=%s 簇=%s topk=%s" % (
                        key, named["name"], [t["name"] for t in topk]))
                    continue
                try:
                    verdict = _align_llm(client, named, caps, topk,
                                         llm_common.want_responses())
                except Exception as e:  # noqa: BLE001
                    print("[warn] %s 对齐 LLM 失败: %s" % (key, e))
                    cache.append(key, {"error": str(e)}, ok=False)
                    continue
            rec = {"key": key, "pool": p, "cluster_id": named["cluster_id"],
                   "size": c["size"], "concept": named["name"],
                   "concept_desc": named["desc"],
                   "verdict": str(verdict.get("verdict") or "").strip(),
                   "matched_instance": str(verdict.get("matched_instance") or "").strip(),
                   "anchor_path": str(verdict.get("anchor_path") or "").strip()[:ANCHOR_CHARS],
                   "confidence": verdict.get("confidence"),
                   "reason": str(verdict.get("reason") or "").strip()[:120],
                   "topk": [{"name": t["name"], "sim": round(float(sims[i]), 3)}
                            for i, t in zip(np.argsort(-sims)[:ALIGN_TOPK], topk)]}
            if rec["verdict"] not in ("new", "existing", "noise"):
                rec["verdict"] = "noise"
                rec["reason"] = "LLM 输出非法 verdict，按噪声处理"
            cache.append(key, rec, ok=True)
            n_ok += 1
            print("[%s] 簇#%d %s → %s%s" % (
                p, named["cluster_id"], named["name"], rec["verdict"],
                "(%s)" % rec["matched_instance"] if rec["matched_instance"] else ""))
    print("对齐完成：新增 %d，跳过（缓存）%d。" % (n_ok, n_skip))


def cache_records_of(edir: Path, fname: str) -> dict:
    return llm_common.JsonlCache(str(edir / fname)).records()


def _cluster_by_id(edir: Path, pool: str, cid: int):
    path = edir / ("clusters_%s.jsonl" % pool)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c["cluster_id"] == cid:
                return c
    return None


def _cluster_centroid(emb: "np.ndarray", members: list) -> "np.ndarray":
    return emb[members].mean(axis=0)


# ---------------------------------------------------------------------------
# report：汇总差异报告
# ---------------------------------------------------------------------------

def report(args):
    edir = emerge_dir(Path(args.meta))
    apath = edir / "align_cache.jsonl"
    if not apath.exists():
        raise SystemExit("缺 align_cache.jsonl，先跑 align")
    recs = []
    with open(apath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ok") and isinstance(r.get("rec"), dict) \
                    and r["rec"].get("verdict"):
                recs.append(r["rec"])

    by_pool = {}
    for p in POOLS:
        pr = [r for r in recs if r.get("pool") == p]
        stat = {}
        for v in ("new", "existing", "noise"):
            vr = [r for r in pr if r["verdict"] == v]
            stat[v] = {"clusters": len(vr), "images": sum(r["size"] for r in vr)}
        total_imgs = sum(r["size"] for r in pr) or 1
        by_pool[p] = {"stats": stat,
                      "align_rate": round(stat["existing"]["images"] / total_imgs, 3),
                      "records": pr}

    proposals = sorted((r for r in by_pool["anom"]["records"]
                        if r["verdict"] == "new"),
                       key=lambda r: -r["size"])
    # 样例 sha256：从 index 里按簇成员取前几条（报告审阅用）
    memb = {}
    cpath = edir / "clusters_anom.jsonl"
    if cpath.exists():
        with open(cpath, encoding="utf-8") as f:
            for line in f:
                c = json.loads(line)
                memb[c["cluster_id"]] = c["members"]
    rowsl = _load_index(edir, "anom")
    for r in proposals:
        idxs = (memb.get(r["cluster_id"]) or [])[:5]
        r["sample_shas"] = [rowsl[i]["sha256"] for i in idxs if i < len(rowsl)]

    out = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "pools": {p: {"stats": by_pool[p]["stats"],
                         "align_rate": by_pool[p]["align_rate"]}
                     for p in POOLS},
           "new_proposals": proposals}
    out_path = edir / "report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("涌现差异报告: %s" % out_path)
    for p in POOLS:
        s = by_pool[p]["stats"]
        print("池 %s: new %d簇/%d图 | existing %d簇/%d图 | noise %d簇/%d图 | 对齐率 %.1f%%" % (
            p, s["new"]["clusters"], s["new"]["images"],
            s["existing"]["clusters"], s["existing"]["images"],
            s["noise"]["clusters"], s["noise"]["images"],
            100 * by_pool[p]["align_rate"]))
    print("\nTop 新概念提议（异常池，按簇规模降序）：")
    for r in proposals[:15]:
        print("  [%4d图] %s —— %s | 锚定建议: %s | 置信 %.2f" % (
            r["size"], r["concept"], (r["concept_desc"] or "")[:50],
            r["anchor_path"] or "?", float(r.get("confidence") or 0)))
    if len(proposals) > 15:
        print("  ...（其余 %d 条见报告文件）" % (len(proposals) - 15))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="emerge",
                                 description="taxonomy 涌现缺口分析（数据有树无）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--meta", type=Path, default=Path("data/dataset/meta"),
                        help="数据湖 meta 目录（默认 data/dataset/meta）")

    pe = sub.add_parser("embed", help="caption embedding（两池）")
    common(pe)
    pe.add_argument("--pool", choices=POOLS + ("both",), default="both")
    pe.add_argument("--limit", type=int, default=0, help="每池最多 embed N 条（冒烟用）")
    pe.add_argument("--backend", choices=("auto", "local"), default="auto")

    pc = sub.add_parser("cluster", help="UMAP + HDBSCAN 聚类")
    common(pc)
    pc.add_argument("--pool", choices=POOLS + ("both",), default="both")
    pc.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE)

    pn = sub.add_parser("name", help="LLM 给簇命名概念")
    common(pn)
    pn.add_argument("--pool", choices=POOLS + ("both",), default="both")
    pn.add_argument("--min-size", type=int, default=MIN_CLUSTER_SIZE)
    pn.add_argument("--overwrite", action="store_true")
    pn.add_argument("--dry-run", action="store_true")

    pa = sub.add_parser("align", help="与 instances.json 对齐三分类")
    common(pa)
    pa.add_argument("--pool", choices=POOLS + ("both",), default="both")
    pa.add_argument("--instances", default="data/taxonomy/instances.json")
    pa.add_argument("--min-size", type=int, default=MIN_CLUSTER_SIZE)
    pa.add_argument("--overwrite", action="store_true")
    pa.add_argument("--dry-run", action="store_true")
    pa.add_argument("--backend", choices=("auto", "local"), default="auto")

    pr = sub.add_parser("report", help="汇总差异报告")
    common(pr)

    args = ap.parse_args(argv)
    {"embed": embed, "cluster": cluster, "name": name_clusters,
     "align": align, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
