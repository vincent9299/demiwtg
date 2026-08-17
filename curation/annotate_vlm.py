#!/usr/bin/env python3
"""
annotate_vlm.py —— 基于本地 vLLM（Qwen3.8-27B 多模态）的图片知识打标 pipeline。

职责：对 data/dataset/meta/images.jsonl 中的图片逐图打标，产出三个字段：
    kb_match   0-10  图片内容与实例知识（名称/别名/desc）的匹配度
    richness   0-10  图片信息丰富度（主体细节、构图、场景、表现力）
    caption    str   详细中文描述（80-200 字）

流程（两阶段，派生物先进 state、真相才进 meta）：
    run    VLM 打标 → state/annotate_vlm/results.jsonl（按 sha256 断点续跑，
           仅成功解析的记录算"完成"，失败/解析错误的下次运行自动重试）
    stream 常驻打标：消费打标队列 state/annotate_vlm/queue.sqlite3（collect/stream.py
           下载成功即投递；启动时回填存量无标注图），成功逐批合并进 images.jsonl
    apply  在 meta_lock 下把三字段原子合并进 images.jsonl（tmp 写入 + rename，
           只加字段不动其余字段；collect 对主清单只追写不重写，合并结果不会被冲掉）
    report 分布统计 + 低分样例 / caption 抽样

约定（AGENTS.md）：
- 输入只读 data/dataset/meta/images.jsonl 与 data/dataset/blobs/（不改动字节）
- 实例知识来自 data/taxonomy/instances.json（name → desc/aliases 查表）
- 中间产物写顶层 state/annotate_vlm/（不进 meta/、不进 git）
- prompt 只给实体本身（名称/别名/desc），不给分类路径：匹配度是「图 ↔ 实体」的判断；
  路径语义恰当性由独立的 taxonomy 策展任务负责

用法：
    python3 curation/annotate_vlm.py run                     # 全量（断点续跑）
    python3 curation/annotate_vlm.py run --instance 人鱼 --limit 20   # 冒烟
    python3 curation/annotate_vlm.py stream                  # 常驻：消费打标队列（含存量回填）
    python3 curation/annotate_vlm.py report
    python3 curation/annotate_vlm.py apply
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

from PIL import Image

try:
    import httpx
except ImportError:
    sys.exit("缺少 httpx：python3 -m pip install httpx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collect.util import meta_lock

DEFAULT_ENDPOINT = "http://localhost:8000/v1/chat/completions"
DEFAULT_MODEL = "qwen3.8-27b"

MAX_INST_PER_IMG = 3      # 一图多实例时最多带几个实体的知识进 prompt
DESC_CHARS = 250          # desc 截断长度（全量约 150-350 字）
CAPTION_MIN = 40          # caption 低于该字数视为解析失败（重试）
ADJUST_SEC = 90           # 自适应并发评估窗口（秒）

SYSTEM_PROMPT = (
    "你是 IP 图片数据集的打标专家。对每张图结合所给实体知识完成三项标注，"
    "严格按 JSON 输出，不要输出其他内容。\n"
    '格式：{"kb_match":0-10的整数,"richness":0-10的整数,"caption":"详细中文描述"}\n'
    "kb_match（实体匹配度）：图中内容与所述实体的吻合程度。\n"
    "  9-10=主体即该实体且核心特征完全吻合；7-8=主体吻合但细节/版本有出入；"
    "4-6=相关但主体不明确（周边、局部、二创、示意图）；1-3=几乎无关；0=完全无关。\n"
    "richness（信息丰富度）：与实体无关，只看图片本身的视觉信息量。\n"
    "  9-10=主体突出且细节丰富，构图完整有场景/语境，风格表现力强"
    "（精细插画、官方海报、高质量场景图）；\n"
    "  7-8=主体清晰、细节较多，有一定场景或设计元素；\n"
    "  5-6=主体可辨但画面简单（素色背景、单一元素、常规截图）；\n"
    "  3-4=信息偏少（严重裁剪、大面积留白、轮廓模糊、图标式简化）；\n"
    "  0-2=几乎无信息（纯色、极简线条、接近空白、画质严重退化）。\n"
    "caption（详细描述）：80-200字中文，客观描述画面：主体及其外观特征、姿态或动作、"
    "场景与背景、风格与媒介（插画/照片/截图/周边实物等）。不要复述实体知识，"
    "不要写评价性套话。"
)

USER_PROMPT_TPL = "以下是该图应描绘的实体信息：\n{blocks}\n请标注这张图。"

# 从模型回复中提取 JSON 对象（容忍 thinking 前缀/```json 包裹）
_JSON_RE = re.compile(r"\{[^{}]*\"kb_match\"[^{}]*\}", re.S)


def repo_root_from_meta(meta: Path) -> Path:
    """AGENTS.md 约定：仓库根由 --meta（默认 data/dataset/meta）向上三级推导。"""
    return meta.resolve().parent.parent.parent


def load_manifest(meta_dir: Path) -> list[dict]:
    rows = []
    with open(meta_dir / "images.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_instance_kb(root: Path) -> dict:
    """data/taxonomy/instances.json → {name: {"desc":..., "aliases":[...]}}。

    契约（AGENTS.md 1.5）：name 全局唯一，一条记录挂多个路径，直接按名取。
    """
    path = root / "data" / "taxonomy" / "instances.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    kb: dict[str, dict] = {}
    for it in doc.get("instances", []):
        name = it.get("name", "")
        if not name:
            continue
        kb[name] = {
            "desc": (it.get("desc") or "").strip(),
            "aliases": [str(a).strip() for a in (it.get("aliases") or [])
                        if str(a).strip()],
        }
    return kb


def build_blocks(names: list[str], kb: dict) -> str:
    """把图上的实例渲染成 prompt 知识块（最多 MAX_INST_PER_IMG 个）。"""
    blocks = []
    for nm in names[:MAX_INST_PER_IMG]:
        rec = kb.get(nm) or {"desc": "", "aliases": []}
        lines = [f"实体：{nm}"]
        if rec["aliases"]:
            lines.append("别名：" + "、".join(rec["aliases"][:5]))
        desc = rec["desc"]
        if desc:
            lines.append("知识：" + (desc[:DESC_CHARS] + ("…" if len(desc) > DESC_CHARS else "")))
        else:
            lines.append("知识：（暂无，仅凭实体名称判断）")
        blocks.append("\n".join(lines))
    if not blocks:
        blocks.append("实体：未知（该图未打实例标签，kb_match 按画面自身内容给 0）")
    return "\n---\n".join(blocks)


def load_done(out_path: Path) -> set[str]:
    """只有成功解析的记录才算完成；失败/解析错误的下次自动重试。"""
    done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("ok") and not d.get("parse_error"):
                    done.add(d["sha256"])
    return done


def encode_image(ds_root: Path, row: dict, max_edge: int) -> str | None:
    """读 blob → 缩放到最长边 max_edge → JPEG base64。失败返回 None。"""
    path = ds_root / row["path"]
    if not path.exists():
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = max_edge / max(w, h)
            if scale < 1.0:
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 打标队列（SQLite，state/annotate_vlm/queue.sqlite3）：队列归属本模块（消费方），
# collect/stream.py 只是生产者（下载成功即投递，同 sha256 去重）。结构为
# collect 的 DownloadQueue 轻量同构：pending/claimed/done/failed + stale 回收。
# ---------------------------------------------------------------------------
QUEUE_STALE_SEC = 300        # claimed 超过该时长视为 stale，回收重排
QUEUE_MAX_ATTEMPTS = 3       # 失败次数上限：用尽标 failed（可人工重投）


def annotate_queue_path(meta_dir) -> Path:
    p = repo_root_from_meta(Path(meta_dir)) / "state" / "annotate_vlm" / "queue.sqlite3"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _qconnect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS items (
            sha256 TEXT PRIMARY KEY,
            instances TEXT NOT NULL DEFAULT '[]',
            blob_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            claimed_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0)""")
    conn.commit()
    return conn


def enqueue_annotate(meta_dir, sha256: str, instances: list,
                     blob_rel_path: str) -> None:
    """生产者契约（collect/stream.py 下载成功处调用）：投递待打标图，同 sha256 去重。

    blob_rel_path 相对 data/dataset/（blobs/<aa>/<sha>.<ext>，与 images.jsonl 的
    path 字段同基准）。失败抛异常由调用方告警，存量回填兜底。
    """
    if not sha256:
        return
    conn = _qconnect(annotate_queue_path(meta_dir))
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO items "
                "(sha256, instances, blob_path, updated_at) VALUES (?,?,?,?)",
                (sha256, json.dumps(instances or [], ensure_ascii=False),
                 blob_rel_path or "", time.time()))
    finally:
        conn.close()


class AnnotateQueue:
    """消费方句柄（stream 模式专用；单 asyncio 线程内调用，无跨线程并发）。"""

    def __init__(self, db_path: Path):
        self.conn = _qconnect(db_path)

    def enqueue(self, sha256: str, instances: list, blob_path: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO items "
                "(sha256, instances, blob_path, updated_at) VALUES (?,?,?,?)",
                (sha256, json.dumps(instances or [], ensure_ascii=False),
                 blob_path or "", time.time()))

    def claim(self) -> dict | None:
        """原子取一件待打标任务（BEGIN IMMEDIATE 串行领取）；无则 None。"""
        now = time.time()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT sha256, instances, blob_path, attempts FROM items "
                "WHERE status='pending' ORDER BY updated_at LIMIT 1").fetchone()
            if row is None:
                self.conn.execute("COMMIT")
                return None
            self.conn.execute(
                "UPDATE items SET status='claimed', claimed_at=?, updated_at=? "
                "WHERE sha256=?", (now, now, row[0]))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return {"sha256": row[0], "instances": json.loads(row[1] or "[]"),
                "path": row[2], "attempts": row[3]}

    def mark_done(self, sha256: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE items SET status='done', updated_at=? WHERE sha256=?",
                (time.time(), sha256))

    def release(self, sha256: str) -> None:
        """失败回退：attempts+1；达上限标 failed，否则回 pending 重试。"""
        with self.conn:
            self.conn.execute(
                "UPDATE items SET attempts=attempts+1, "
                "status=CASE WHEN attempts+1 >= ? THEN 'failed' ELSE 'pending' END, "
                "updated_at=? WHERE sha256=?",
                (QUEUE_MAX_ATTEMPTS, time.time(), sha256))

    def requeue_stale(self) -> int:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE items SET status='pending' WHERE status='claimed' "
                "AND claimed_at < ?", (time.time() - QUEUE_STALE_SEC,))
            return cur.rowcount

    def known_shas(self) -> set:
        rows = self.conn.execute("SELECT sha256 FROM items").fetchall()
        return {r[0] for r in rows}

    def counts(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM items GROUP BY status").fetchall()
        return dict(rows)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


def parse_annotation(text: str) -> dict:
    m = _JSON_RE.search(text)
    if not m:
        return {"ok": False, "parse_error": True, "raw": text[:200]}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "parse_error": True, "raw": m.group(0)[:200]}

    def clamp(v):
        try:
            return max(0, min(10, int(v)))
        except (TypeError, ValueError):
            return None

    km, ri = clamp(d.get("kb_match")), clamp(d.get("richness"))
    cap = str(d.get("caption") or "").strip()
    if km is None or ri is None or len(cap) < CAPTION_MIN:
        return {"ok": False, "parse_error": True, "raw": m.group(0)[:200]}
    return {"ok": True, "kb_match": km, "richness": ri, "caption": cap}


async def call_vlm(client: httpx.AsyncClient, endpoint: str, model: str,
                   b64: str, blocks: str, max_tokens: int,
                   retries: int) -> tuple[dict, int]:
    """调 vLLM，返回 (解析后的标注, 尝试次数)。"""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        # Qwen3.8 默认开 thinking，会把 token 预算耗在推理链上；批量打标关掉
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text",
                 "text": USER_PROMPT_TPL.format(blocks=blocks)},
            ]},
        ],
    }
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            r = await client.post(endpoint, json=payload, timeout=120)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return parse_annotation(content), attempt
        except Exception as e:  # noqa: BLE001 - 网络/服务端错误统一退避重试
            last_err = f"{type(e).__name__}: {e}"[:300]
            await asyncio.sleep(min(2 ** attempt, 15))
    return {"ok": False, "error": last_err}, retries


class AdaptiveGate:
    """在飞请求闸门：worker 池按上限开足，实际并发由 target 限制，可运行中调整。"""

    def __init__(self, target: int):
        self.target = target
        self.active = 0
        self.cond = asyncio.Condition()

    async def acquire(self):
        async with self.cond:
            while self.active >= self.target:
                await self.cond.wait()
            self.active += 1

    async def release(self):
        async with self.cond:
            self.active -= 1
            self.cond.notify()

    async def set_target(self, n: int):
        async with self.cond:
            self.target = n
            self.cond.notify_all()


async def adapt_controller(gate: AdaptiveGate, counter: dict, args):
    """吞吐爬山：每窗口比较 img/s，涨则沿当前方向加减并发，跌则反向。"""
    step, direction = args.adapt_step, +1
    last_done, last_t = 0, time.time()
    while counter["done"] < counter["total"]:
        await asyncio.sleep(ADJUST_SEC)
        now = time.time()
        rate = (counter["done"] - last_done) / max(now - last_t, 1e-6)
        last_done, last_t = counter["done"], now
        if counter["done"] >= counter["total"]:
            break
        if rate > counter.get("best_rate", 0):
            counter["best_rate"] = rate          # 有效，沿原方向继续
        else:
            direction = -direction               # 回落，反向探索
        new_target = max(args.min_concurrency,
                         min(args.max_concurrency, gate.target + direction * step))
        if new_target != gate.target:
            await gate.set_target(new_target)
            print(f"[adapt] 窗口速率 {rate:.2f} img/s（历史最佳 "
                  f"{counter.get('best_rate', 0):.2f}），并发 "
                  f"{gate.target} -> {new_target}", flush=True)


async def worker(name: str, queue: asyncio.Queue, client: httpx.AsyncClient,
                 args, kb: dict, gate: AdaptiveGate, out_f, counter: dict,
                 lock: asyncio.Lock):
    while True:
        row = await queue.get()
        try:
            if row is None:
                return
            t0 = time.time()
            rec = {"sha256": row["sha256"], "path": row["path"],
                   "instances": row.get("instances", [])}
            encoded = encode_image(args.dataset, row, args.max_edge)
            if encoded is None:
                rec.update({"ok": False, "error": "image_unreadable"})
            else:
                blocks = build_blocks(row.get("instances") or [], kb)
                await gate.acquire()
                try:
                    ann, attempts = await call_vlm(
                        client, args.endpoint, args.model, encoded,
                        blocks, args.max_tokens, args.retries)
                finally:
                    await gate.release()
                rec.update(ann)
                rec["attempts"] = attempts
                rec["elapsed_ms"] = int((time.time() - t0) * 1000)
            async with lock:
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out_f.flush()
                counter["done"] += 1
                if rec.get("ok"):
                    counter["ok"] += 1
                    counter["km_sum"] += rec["kb_match"]
                if counter["done"] % args.log_every == 0:
                    rate = counter["done"] / max(time.time() - counter["t0"], 1e-6)
                    eta = (counter["total"] - counter["done"]) / max(rate, 1e-6)
                    avg = counter["km_sum"] / max(counter["ok"], 1)
                    print(f"[{counter['done']}/{counter['total']}] "
                          f"ok={counter['ok']} 平均kb_match={avg:.1f} "
                          f"{rate:.1f} img/s ETA {eta/60:.1f} min", flush=True)
        finally:
            queue.task_done()


async def run(args, kb: dict):
    rows = load_manifest(args.meta)
    if args.instance:
        rows = [r for r in rows if args.instance in (r.get("instances") or [])]
    total_before = len(rows)

    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out_path)
    pending = [r for r in rows if r["sha256"] not in done]
    print(f"清单 {total_before} 张，已完成 {len(done)}，本次待处理 {len(pending)}")

    if args.refresh:
        pending = rows
        print(f"--refresh：忽略已完成记录，全量重跑 {len(pending)} 张")
    if args.limit > 0:
        pending = pending[: args.limit]
    if not pending:
        print("没有需要打标的图片")
        return

    # refresh 模式先清空旧结果，避免同 sha 多行混淆
    mode = "w" if args.refresh else "a"
    with open(out_path, mode, encoding="utf-8") as out_f:
        counter = {"done": 0, "ok": 0, "km_sum": 0,
                   "total": len(pending), "t0": time.time()}
        lock = asyncio.Lock()
        gate = AdaptiveGate(min(args.concurrency, args.max_concurrency))
        queue: asyncio.Queue = asyncio.Queue(maxsize=args.max_concurrency * 4)

        limits = httpx.Limits(max_connections=args.max_concurrency + 4,
                              max_keepalive_connections=args.max_concurrency)
        async with httpx.AsyncClient(limits=limits) as client:
            workers = [asyncio.create_task(
                worker(f"w{i}", queue, client, args, kb, gate, out_f,
                       counter, lock))
                for i in range(args.max_concurrency)]
            adapt = asyncio.create_task(adapt_controller(gate, counter, args))
            for r in pending:
                await queue.put(r)
            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers)
            adapt.cancel()

    avg = counter["km_sum"] / max(counter["ok"], 1)
    print(f"完成：成功 {counter['ok']}/{counter['done']}，平均 kb_match={avg:.2f}")
    print(f"结果写入 {out_path}；人工抽查满意后执行 apply 合并进 images.jsonl")


def load_results(out_path: Path) -> dict:
    """同 sha 取最后一行（重试后的新结果覆盖旧失败）。"""
    recs: dict[str, dict] = {}
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            recs[d["sha256"]] = d
    return recs


def merge_results(meta_dir: Path, ann: dict) -> int:
    """在 meta_lock 下把 ann {sha256: 成功标注} 原子合并进 images.jsonl
    （只加 kb_match/richness/caption 三字段，其余不动）。返回合并条数。
    apply 子命令与 stream 常驻模式共用同一实现。"""
    if not ann:
        return 0
    mpath = meta_dir / "images.jsonl"
    tmp = meta_dir / "images.jsonl.tmp_annotate"
    n_merged = 0
    with meta_lock(str(meta_dir)):
        with open(mpath, encoding="utf-8") as f, \
                open(tmp, "w", encoding="utf-8") as out_f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                d = ann.get(row["sha256"])
                if d:
                    row["kb_match"] = d["kb_match"]
                    row["richness"] = d["richness"]
                    row["caption"] = d["caption"]
                    n_merged += 1
                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, mpath)
    return n_merged


def apply(args):
    if not args.out.exists():
        sys.exit(f"结果文件不存在：{args.out}（先 run）")
    ann = {sha: d for sha, d in load_results(args.out).items()
           if d.get("ok") and not d.get("parse_error")}
    print(f"可用标注 {len(ann)} 条")
    if not ann:
        sys.exit("没有可合并的成功记录")
    n_merged = merge_results(args.meta, ann)
    print(f"已合并 {n_merged} 条进 {args.meta / 'images.jsonl'}"
          "（meta_lock 下原子替换，其余字段未动）")


# ---------------------------------------------------------------------------
# stream 子命令：常驻消费打标队列（与 collect stream 构成流水线第三级）
# ---------------------------------------------------------------------------
STREAM_FLUSH_SEC = 60        # 合并缓冲最长滞留时间


def backfill(aq: AnnotateQueue, args) -> int:
    """存量回填：images.jsonl 中无 kb_match 且不在队列/已完成集的图补入队。
    （--no-backfill 关闭；新流程下载即投递，本函数只兼顾存量）"""
    known = aq.known_shas()
    done_jsonl = load_done(args.out)
    n = 0
    for r in load_manifest(args.meta):
        sha = r.get("sha256")
        if not sha or sha in known or sha in done_jsonl:
            continue
        if r.get("kb_match") is not None:
            continue
        aq.enqueue(sha, r.get("instances") or [], r.get("path") or "")
        n += 1
    return n


async def stream_feeder(aq: AnnotateQueue, out_q: asyncio.Queue,
                        stop_evt: threading.Event):
    """从 SQLite 队列取件喁 asyncio worker 池；队列空时短眠等待新投递。"""
    while not stop_evt.is_set():
        item = aq.claim()
        if item is None:
            await asyncio.sleep(2.0)
            continue
        await out_q.put(item)


async def stream_worker(queue: asyncio.Queue, client: httpx.AsyncClient,
                        args, kb: dict, gate: AdaptiveGate, out_f,
                        counter: dict, lock: asyncio.Lock,
                        aq: AnnotateQueue, merge_buf: dict):
    while True:
        item = await queue.get()
        try:
            if item is None:
                return
            t0 = time.time()
            row = {"sha256": item["sha256"], "path": item["path"],
                   "instances": item["instances"]}
            rec = dict(row)
            encoded = encode_image(args.dataset, row, args.max_edge)
            if encoded is None:
                rec.update({"ok": False, "error": "image_unreadable"})
            else:
                blocks = build_blocks(item["instances"], kb)
                await gate.acquire()
                try:
                    ann, attempts = await call_vlm(
                        client, args.endpoint, args.model, encoded,
                        blocks, args.max_tokens, args.retries)
                finally:
                    await gate.release()
                rec.update(ann)
                rec["attempts"] = attempts
                rec["elapsed_ms"] = int((time.time() - t0) * 1000)
            async with lock:
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")   # 审计+断点
                out_f.flush()
                counter["done"] += 1
                if rec.get("ok"):
                    counter["ok"] += 1
                    counter["km_sum"] += rec["kb_match"]
                    merge_buf[item["sha256"]] = rec      # 待合并缓冲
                    aq.mark_done(item["sha256"])
                else:
                    aq.release(item["sha256"])           # 回队重试/达上限 failed
                if counter["done"] % args.log_every == 0:
                    rate = counter["done"] / max(time.time() - counter["t0"], 1e-6)
                    avg = counter["km_sum"] / max(counter["ok"], 1)
                    print(f"[stream] 已处理 {counter['done']}，ok={counter['ok']} "
                          f"平均kb_match={avg:.1f} {rate:.2f} img/s，"
                          f"队列 {aq.counts()}", flush=True)
        finally:
            queue.task_done()


async def stream_flusher(args, merge_buf: dict, lock: asyncio.Lock,
                         counter: dict, stop_evt: threading.Event):
    """每 flush_every 条或 STREAM_FLUSH_SEC 秒把合并缓冲写进 images.jsonl。"""
    last = time.time()
    while True:
        await asyncio.sleep(5.0)
        async with lock:
            due = merge_buf and (len(merge_buf) >= args.flush_every
                                 or time.time() - last >= STREAM_FLUSH_SEC)
            if not due:
                if stop_evt.is_set() and not merge_buf:
                    return
                continue
            buf = dict(merge_buf)      # 原地清空调用方缓冲（不能重绑定）
            merge_buf.clear()
        if buf:
            n = merge_results(args.meta, buf)
            counter["merged"] += n
            last = time.time()
            print(f"[stream] 合并 {n} 条标注进 images.jsonl（累计 "
                  f"{counter['merged']}）", flush=True)


async def stream_main(args, kb: dict):
    aq = AnnotateQueue(annotate_queue_path(args.meta))
    n_stale = aq.requeue_stale()
    if n_stale:
        print(f"[stream] 回收 stale 领取 {n_stale} 件", flush=True)
    if not args.no_backfill:
        n = backfill(aq, args)
        if n:
            print(f"[stream] 存量回填 {n} 张无标注图入队", flush=True)
    print(f"[stream] 打标队列状态：{aq.counts()}", flush=True)

    stop_evt = threading.Event()

    def _sig(signum, _frame):
        print(f"[stream] 收到信号 {signum}，收尾中…", flush=True)
        stop_evt.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    counter = {"done": 0, "ok": 0, "km_sum": 0, "merged": 0,
               "total": 10 ** 12, "t0": time.time(), "best_rate": 0}
    gate = AdaptiveGate(min(args.concurrency, args.max_concurrency))
    queue: asyncio.Queue = asyncio.Queue(maxsize=args.max_concurrency * 2)
    merge_buf: dict = {}
    lock = asyncio.Lock()

    limits = httpx.Limits(max_connections=args.max_concurrency + 4,
                          max_keepalive_connections=args.max_concurrency)
    with open(args.out, "a", encoding="utf-8") as out_f:
        async with httpx.AsyncClient(limits=limits) as client:
            workers = [asyncio.create_task(stream_worker(
                queue, client, args, kb, gate, out_f, counter, lock,
                aq, merge_buf)) for _ in range(args.max_concurrency)]
            feeder = asyncio.create_task(stream_feeder(aq, queue, stop_evt))
            flusher = asyncio.create_task(
                stream_flusher(args, merge_buf, lock, counter, stop_evt))
            adapt = asyncio.create_task(adapt_controller(gate, counter, args))
            while not stop_evt.is_set():
                await asyncio.sleep(1.0)
            feeder.cancel()
            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers, return_exceptions=True)
            adapt.cancel()
            await asyncio.gather(flusher, return_exceptions=True)
    aq.close()
    avg = counter["km_sum"] / max(counter["ok"], 1)
    print(f"[stream] 已停机：本次处理 {counter['done']}（ok {counter['ok']}，"
          f"平均 kb_match={avg:.2f}），合并 {counter['merged']} 条进 images.jsonl")



def report(args):
    if not args.out.exists():
        sys.exit(f"结果文件不存在：{args.out}")
    recs = load_results(args.out)
    ok = [d for d in recs.values() if d.get("ok") and not d.get("parse_error")]
    bad = [d for d in recs.values() if not d.get("ok") or d.get("parse_error")]
    print(f"累计记录 {len(recs)}：成功 {len(ok)}，失败/待重试 {len(bad)}")
    if not ok:
        return

    km_dist: dict[int, int] = {}
    ri_dist: dict[int, int] = {}
    for d in ok:
        km_dist[d["kb_match"]] = km_dist.get(d["kb_match"], 0) + 1
        ri_dist[d["richness"]] = ri_dist.get(d["richness"], 0) + 1
    for title, dist in (("kb_match 分布", km_dist), ("richness 分布", ri_dist)):
        print(title + "：")
        for k in sorted(dist):
            n = dist[k]
            print(f"  {k:>2}  {'#' * max(1, n * 40 // len(ok))} {n}")

    low = sorted(ok, key=lambda d: d["kb_match"])[:5]
    print(f"\nkb_match 最低样例（前 {len(low)}，疑似误配图）：")
    for d in low:
        print(f"  [{d['kb_match']}] {d['sha256'][:16]} instances={d.get('instances')}")
        print(f"       caption: {d['caption'][:70]}")
    cap_lens = sorted(len(d["caption"]) for d in ok)
    print(f"\ncaption 字数：中位 {cap_lens[len(cap_lens)//2]}，"
          f"最短 {cap_lens[0]}，最长 {cap_lens[-1]}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--meta", type=Path, default=Path("data/dataset/meta"),
                        help="meta 目录（默认 data/dataset/meta）")
        default_root = repo_root_from_meta(Path("data/dataset/meta"))
        sp.add_argument("--out", type=Path,
                        default=default_root / "state" / "annotate_vlm" / "results.jsonl",
                        help="结果 JSONL（默认 state/annotate_vlm/results.jsonl）")

    pr = sub.add_parser("run", help="执行打标")
    add_common(pr)
    pr.add_argument("--dataset", type=Path, default=Path("data/dataset"),
                    help="数据湖根目录（默认 data/dataset）")
    pr.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="vLLM chat completions 地址")
    pr.add_argument("--model", default=DEFAULT_MODEL)
    pr.add_argument("--concurrency", type=int, default=16, help="初始并发（自适应起点）")
    pr.add_argument("--min-concurrency", type=int, default=8, help="自适应下限")
    pr.add_argument("--max-concurrency", type=int, default=48, help="自适应上限（worker 池大小）")
    pr.add_argument("--adapt-step", type=int, default=4, help="每次调整的并发步长")
    pr.add_argument("--max-edge", type=int, default=1280, help="送模型前最长边缩放阈值")
    pr.add_argument("--max-tokens", type=int, default=600)
    pr.add_argument("--retries", type=int, default=3)
    pr.add_argument("--instance", default=None, help="只处理含该实例名的图")
    pr.add_argument("--limit", type=int, default=0, help="本次最多处理的张数（0=不限）")
    pr.add_argument("--refresh", action="store_true", help="忽略已完成记录全量重跑")
    pr.add_argument("--log-every", type=int, default=50)

    pa = sub.add_parser("apply", help="把三字段合并进 images.jsonl")
    add_common(pa)

    ps = sub.add_parser("stream", help="常驻消费打标队列（collect stream 的下游）")
    add_common(ps)
    ps.add_argument("--dataset", type=Path, default=Path("data/dataset"),
                    help="数据湖根目录（默认 data/dataset）")
    ps.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="vLLM chat completions 地址")
    ps.add_argument("--model", default=DEFAULT_MODEL)
    ps.add_argument("--concurrency", type=int, default=16, help="初始并发（自适应起点）")
    ps.add_argument("--min-concurrency", type=int, default=8, help="自适应下限")
    ps.add_argument("--max-concurrency", type=int, default=48, help="自适应上限（worker 池大小）")
    ps.add_argument("--adapt-step", type=int, default=4, help="每次调整的并发步长")
    ps.add_argument("--max-edge", type=int, default=1280, help="送模型前最长边缩放阈值")
    ps.add_argument("--max-tokens", type=int, default=600)
    ps.add_argument("--retries", type=int, default=3)
    ps.add_argument("--flush-every", type=int, default=50,
                    help="成功标注满 N 条即合并进 images.jsonl（默认 50，或 60s 先到者）")
    ps.add_argument("--no-backfill", action="store_true",
                    help="不回填存量无标注图（只消费新投递）")
    ps.add_argument("--log-every", type=int, default=50)

    pp = sub.add_parser("report", help="分布统计与样例抽查")
    add_common(pp)

    args = p.parse_args()
    args.meta = args.meta.resolve()
    if args.cmd == "run":
        args.dataset = args.dataset.resolve()
        kb = load_instance_kb(repo_root_from_meta(args.meta))
        print(f"实例知识库加载完成：{len(kb)} 个实体")
        asyncio.run(run(args, kb))
    elif args.cmd == "apply":
        apply(args)
    elif args.cmd == "stream":
        args.dataset = args.dataset.resolve()
        kb = load_instance_kb(repo_root_from_meta(args.meta))
        print(f"实例知识库加载完成：{len(kb)} 个实体")
        asyncio.run(stream_main(args, kb))
    else:
        report(args)


if __name__ == "__main__":
    main()
