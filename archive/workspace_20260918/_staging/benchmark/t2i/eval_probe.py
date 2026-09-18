"""t2i 概念硬约束探针：五路生成 + glm-5.3 合并。

读 t2i/data/samples_<batch>.jsonl（eval_sample.py 产物），每实例走
    1) 五路独立生成（glm/glm-5.2, glm/glm-5.3-flash, qianwen2/qwen3.8-flash,
       galaxy/deepseek-v4-flash-0731, galaxy/minimax-m3）
    2) glm/glm-5.3 合并编辑（probe_merge_prompt.md，通过剔除的全部保留
       不设数量上限；出题时由出题模型从池中选锚点）
    3) 阈值放行（≥2 条且 ≥2 路非空）
产物 = 合并约束清单 jsonl，随样本流转进出题（eval_synthesize --constraints）。
（原「原图一致性预检」gemini 环节 2026-08-29 用户拍板废除。）

用法：
    # 五路生成 + 合并（断点续跑）
    python3 benchmark/t2i/eval_probe.py generate \
        --samples data/samples_20260828_v2.jsonl --limit 50 --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BENCH_ROOT = Path(__file__).resolve().parent.parent   # t2i/ -> benchmark/
REPO_ROOT = BENCH_ROOT.parent                         # benchmark/ -> repo root
sys.path.insert(0, str(BENCH_ROOT))

SUB_DIR = Path(__file__).resolve().parent             # t2i/
EVAL_DIR = SUB_DIR / "data"

PROBE_PROMPT_FILE = SUB_DIR / "probe_prompt_solid_facts.md"
MERGE_PROMPT_FILE = SUB_DIR / "probe_merge_prompt.md"

# modelhub 网关（与 eval_synthesize.py 同）
MODELHUB_URL = "http://127.0.0.1:4001/v1/chat/completions"
MODELHUB_KEY = "EMPTY"

# 五路生成阵容（2026-08-28 定案；provider 优先级：qwen/glm 自家 > galaxy > openrouter）
GENERATIVE_MODELS = [
    "glm/glm-5.2",                       # GLM 直连
    "glm/glm-5.3-flash",                  # GLM 直连
    "qianwen2/qwen3.8-flash",             # 百炼（qwen 自家）
    "galaxy/deepseek-v4-flash-0731",      # Galaxy
    "galaxy/minimax-m3",                  # Galaxy（唯一源）
]
MERGE_MODEL = "glm/glm-5.3"  # 合并编辑（用户 token 包，2026-08-29 拍板）

MAX_TOKENS = 32768            # 出题/探针类长输出须 32768 起（16k 截断）
MERGE_MAX_TOKENS = 32768
TIMEOUT = (10, 600)
TEMPERATURE = 0.3             # 探针要确定性，低温度

# instances.json KB（desc 权威源）
INSTANCES_FILE = REPO_ROOT / "datasets" / "demiwtg" / "meta" / "instances.json"


# ---------------------------------------------------------------------------
# 通用：LLM 调用与解析（与 eval_synthesize.py 同构）
# ---------------------------------------------------------------------------
def call_api(api_key: str, api_url: str, model: str,
             system_prompt: str, user_message: list, tag: str,
             max_tokens: int = MAX_TOKENS) -> dict:
    payload = {
        "model": model,
        "stream": False,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    retries = 5
    backoff = [30, 60, 120]
    for attempt in range(retries):
        try:
            resp = requests.post(api_url, json=payload, headers=headers,
                                 timeout=TIMEOUT)
            if resp.status_code == 429 or resp.status_code == 529:
                wait = backoff[min(attempt, len(backoff) - 1)]
                print(f"  [warn] {tag} HTTP {resp.status_code} 限流，"
                      f"{wait}s 后重试（{attempt+1}/{retries}）",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            if msg.get("content") is None and msg.get("reasoning_content"):
                msg["content"] = msg["reasoning_content"]
                data["choices"][0]["message"] = msg
                print(f"  [note] {tag} reasoning-only 响应，已回退 reasoning_content",
                      file=sys.stderr)
            return data
        except Exception as e:  # noqa: BLE001
            if attempt >= retries - 1:
                raise
            wait = backoff[min(attempt, len(backoff) - 1)]
            print(f"  [warn] {tag} 调用失败（{e}），{wait}s 后重试",
                  file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _lenient_object(text: str, start: int) -> dict:
    """raw_decode + 尾逗号容错。"""
    seg = text[start:]
    for _ in range(10):
        try:
            obj, _ = json.JSONDecoder().raw_decode(seg)
            return obj
        except json.JSONDecodeError as e:
            p = e.pos - 1
            while p >= 0 and seg[p] in " \t\r\n":
                p -= 1
            if p >= 0 and seg[p] == ",":
                seg = seg[:p] + seg[p + 1:]
                continue
            raise
    raise ValueError("尾逗号修复超过 10 次仍失败")


def extract_json_object(content: str) -> dict:
    """从输出中抠出第一个完整 JSON 对象（raw_decode 抗多 JSON/示例污染）。"""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"输出中无 JSON 对象: {content[:200]!r}")
    return _lenient_object(text, start)


def extract_json_array(content: str) -> list:
    """从输出中抠出 JSON 数组。"""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError(f"输出中无 JSON 数组: {content[:200]!r}")
    return json.loads(text[start : end + 1])



# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_samples(path: Path) -> list:
    if not path.exists():
        sys.exit(f"样本清单不存在：{path}\n"
                 f"请先运行 benchmark/t2i/eval_sample.py")
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


_kb_cache: dict = None

def load_kb() -> dict:
    """instances.json → {name: instance}，缓存。"""
    global _kb_cache
    if _kb_cache is None:
        _kb_cache = {}
        if INSTANCES_FILE.exists():
            with INSTANCES_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
            for inst in data.get("instances", []):
                _kb_cache[inst["name"]] = inst
    return _kb_cache


def get_desc(name: str) -> str:
    """从 instances.json KB 取 desc（权威源）。"""
    kb = load_kb()
    inst = kb.get(name)
    return inst.get("desc", "") if inst else ""


def fill_template(template: str, **kw) -> str:
    """占位符逐个字面替换。模板含字面花括号示例（variants 枚举域、
    输出 JSON 样例），str.format 会把它们当占位符解析而 KeyError。"""
    for k, v in kw.items():
        template = template.replace("{" + k + "}", str(v))
    return template


# ---------------------------------------------------------------------------
# 第一步：五路独立生成
# ---------------------------------------------------------------------------
def generate_one(model: str, name: str, paths: str, desc: str,
                 probe_prompt: str, raw_dir: Path, api_key: str, api_url: str,
                 max_tokens: int) -> dict:
    """单模型单实例探针生成。返回 {model, items, parse_ok, error}。"""
    tag = f"{name}|{model.split('/')[-1]}"
    user_text = fill_template(probe_prompt, name=name, paths=paths, desc=desc)
    user_msg = [{"type": "text", "text": user_text}]
    raw_p = raw_dir / f"{name}_{model.replace('/', '_')}.json"
    result = None
    if raw_p.exists():
        try:
            cand = json.loads(raw_p.read_text(encoding="utf-8"))
            _content = cand["choices"][0]["message"]["content"] or ""
            if _content.strip():
                result = cand
        except Exception:  # noqa: BLE001
            result = None
    if result is None:
        try:
            result = call_api(api_key, api_url, model, "", user_msg, tag,
                              max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            return {"model": model, "items": [], "parse_ok": False,
                    "error": str(e), "raw_file": None}
        raw_p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    content = result["choices"][0]["message"]["content"] or ""
    if not content.strip():
        try:
            raw_p.unlink()
        except OSError:
            pass
        return {"model": model, "items": [], "parse_ok": False,
                "error": "content_empty", "raw_file": str(raw_p)}
    try:
        obj = extract_json_object(content)
        items = obj.get("constraints") or []
        return {"model": model, "items": items, "parse_ok": True,
                "error": None, "raw_file": str(raw_p)}
    except (ValueError, json.JSONDecodeError) as e:
        try:
            raw_p.unlink()
        except OSError:
            pass
        return {"model": model, "items": [], "parse_ok": False,
                "error": f"parse_fail: {e}", "raw_file": None}


# ---------------------------------------------------------------------------
# 第二步：sol 合并
# ---------------------------------------------------------------------------
def merge_one(name: str, paths: str,
               model_results: list, merge_prompt: str,
               raw_dir: Path, api_key: str, api_url: str,
               merge_max_tokens: int,
               model: str = MERGE_MODEL, raw_tag: str = "merge") -> dict:
    """合并编辑（不喂 KB desc：富化产物可能有误，裁决靠交叉印证+模型自身知识）。
    raw_tag 区分不同合并模型的产物文件名（防覆盖）。"""
    tag = f"{name}|merge-{model.split('/')[-1]}"
    # 构造各路原始清单文本
    model_lists_text = []
    for mr in model_results:
        if not mr["items"]:
            continue
        model_lists_text.append(f"【{mr['model']}】")
        for i, c in enumerate(mr["items"], 1):
            var = " / ".join(map(str, c.get("variants") or [])) or "无"
            model_lists_text.append(
                f"  {i}. {c['constraint']}（极性: {c['polarity']}，"
                f"变体: {var}，依据: {c.get('source', 'desc')}）")
        model_lists_text.append("")
    model_lists_str = "\n".join(model_lists_text) or "（各路均无输出）"
    user_text = fill_template(merge_prompt, name=name, paths=paths,
                              model_lists=model_lists_str)
    user_msg = [{"type": "text", "text": user_text}]
    raw_p = raw_dir / f"{name}_{raw_tag}.json"
    result = None
    if raw_p.exists():
        try:
            cand = json.loads(raw_p.read_text(encoding="utf-8"))
            _content = cand["choices"][0]["message"]["content"] or ""
            if _content.strip():
                result = cand
        except Exception:  # noqa: BLE001
            result = None
    if result is None:
        try:
            result = call_api(api_key, api_url, model, "", user_msg, tag,
                              max_tokens=merge_max_tokens)
        except Exception as e:  # noqa: BLE001
            return {"instance": name, "constraints": [], "removed": [],
                    "notes": f"merge_api_error: {e}", "merge_ok": False}
        raw_p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    content = result["choices"][0]["message"]["content"] or ""
    if not content.strip():
        try:
            raw_p.unlink()
        except OSError:
            pass
        return {"instance": name, "constraints": [], "removed": [],
                "notes": "merge_content_empty", "merge_ok": False}
    try:
        obj = extract_json_object(content)
        # 校验 id 连续性
        constraints = obj.get("constraints") or []
        for i, c in enumerate(constraints, 1):
            c["id"] = i
        removed = obj.get("removed") or []
        notes = obj.get("notes", "")
        return {"instance": name, "constraints": constraints,
                "removed": removed, "notes": notes, "merge_ok": True}
    except (ValueError, json.JSONDecodeError) as e:
        try:
            raw_p.unlink()
        except OSError:
            pass
        return {"instance": name, "constraints": [], "removed": [],
                "notes": f"merge_parse_fail: {e}", "merge_ok": False}


# ---------------------------------------------------------------------------
# 主流程：generate 子命令
# ---------------------------------------------------------------------------
def generate(samples_file: Path, limit: int, workers: int,
             api_key: str, api_url: str,
             out_dir: Path, max_tokens: int, merge_max_tokens: int,
             offset: int = 0):
    samples = load_samples(samples_file)
    if offset > 0:
        samples = samples[offset:]
    if limit > 0:
        samples = samples[:limit]
    probe_prompt = PROBE_PROMPT_FILE.read_text(encoding="utf-8")
    merge_prompt = MERGE_PROMPT_FILE.read_text(encoding="utf-8")
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "constraints.jsonl"
    # 断点续跑：已合并实例跳过
    done_instances = set()
    if out_file.exists():
        for line in out_file.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                done_instances.add(r["instance"])
    fout = out_file.open("a", encoding="utf-8")  # noqa: SIM115
    write_lock = threading.Lock()
    stat_lock = threading.Lock()
    n_total = n_pass = n_fail = 0
    print(f"探针生成：{len(samples)} 实例，"
          f"已完成 {len(done_instances)}，"
          f"五路生成 + sol 合并，实例并发 {workers}（实例内五路全并行）", flush=True)

    def process_one(rec):
        nonlocal n_total, n_pass, n_fail
        name = rec["instance"]
        sid = rec["sample_id"]
        if name in done_instances:
            print(f"  [resume] {sid} {name} 已合并，跳过", flush=True)
            return
        with stat_lock:
            n_total += 1
        paths = " / ".join(rec.get("mount_paths") or [])
        desc = get_desc(name)
        t0 = time.time()
        print(f"[start] {sid} {name}", flush=True)
        # 五路生成（并行；实例间也有并发，五路全开）
        model_results = []
        with ThreadPoolExecutor(max_workers=len(GENERATIVE_MODELS)) as ex:
            futs = {ex.submit(generate_one, m, name, paths, desc,
                              probe_prompt, raw_dir, api_key, api_url,
                              max_tokens): m for m in GENERATIVE_MODELS}
            for fut in futs:
                model_results.append(fut.result())
        n_nonempty = sum(1 for mr in model_results if mr["items"])
        total_items = sum(len(mr["items"]) for mr in model_results)
        print(f"  [gen] {sid} {name}: {n_nonempty}/5 路非空，"
              f"共 {total_items} 条", flush=True)
        # sol 合并
        merged = merge_one(name, paths, model_results, merge_prompt,
                           raw_dir, api_key, api_url, merge_max_tokens)
        n_constraints = len(merged["constraints"])
        # 阈值放行：≥2 条且 ≥2 路非空
        if n_constraints < 2 or n_nonempty < 2:
            merged["_threshold_pass"] = False
            merged["_threshold_note"] = (
                f"未达阈值（{n_constraints} 条，{n_nonempty}/5 路非空）")
            with stat_lock:
                n_fail += 1
        else:
            merged["_threshold_pass"] = True
            with stat_lock:
                n_pass += 1
        merged["_sample_id"] = sid
        merged["_n_nonempty_models"] = n_nonempty
        merged["_n_total_items"] = total_items
        merged["_gen_models"] = [
            {"model": mr["model"], "n": len(mr["items"]),
             "parse_ok": mr["parse_ok"], "error": mr["error"]}
            for mr in model_results]
        with write_lock:
            fout.write(json.dumps(merged, ensure_ascii=False) + "\n")
            fout.flush()
        dt = time.time() - t0
        print(f"  [done] {sid} {name}: {n_constraints} 条，"
              f"pass={merged['_threshold_pass']}，{dt:.0f}s", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(process_one, rec) for rec in samples]
        for f in futs:
            f.result()
    fout.close()
    print(f"\n完成：{n_total} 实例，{n_pass} 通过阈值，{n_fail} 未通过 "
          f"-> {out_file}")
    print(f"  阈值：≥2 条硬约束 且 ≥2 路非空")


# ---------------------------------------------------------------------------
# remerge 子命令：候选模型离线重合并（对比实验用）
# ---------------------------------------------------------------------------
def load_raw_results(raw_dir: Path, name: str) -> list:
    """从已落盘的五路 raw 重建 model_results（不重调生成模型）。"""
    out = []
    for m in GENERATIVE_MODELS:
        p = raw_dir / f"{name}_{m.replace('/', '_')}.json"
        items, ok = [], False
        if p.exists():
            try:
                cand = json.loads(p.read_text(encoding="utf-8"))
                content = cand["choices"][0]["message"]["content"] or ""
                if content.strip():
                    obj = extract_json_object(content)
                    items = obj.get("constraints") or []
                    ok = True
            except (ValueError, json.JSONDecodeError, KeyError,
                    IndexError):  # noqa: PERF203
                items, ok = [], False
        out.append({"model": m, "items": items, "parse_ok": ok})
    return out


def remerge(batch_dir: Path, samples_file: Path, merge_model: str,
            workers: int, api_key: str, api_url: str,
            merge_max_tokens: int, merge_prompt_file: Path = None,
            tag: str = ""):
    """离线重合并：复用 batch_dir/raw 五路产物，用候选模型重跑合并。

    不重调五路生成、不覆写 constraints.jsonl；产物写
    batch_dir/remerge_<候选模型短名>.jsonl，raw 写
    batch_dir/raw/<name>_remerge_<短名>.json，供与 sol 合并结果 diff。"""
    raw_dir = batch_dir / "raw"
    src = batch_dir / "constraints.jsonl"
    if not src.exists():
        sys.exit(f"原合并清单不存在：{src}")
    names = []
    for line in src.open(encoding="utf-8"):
        if line.strip():
            names.append(json.loads(line)["instance"])
    path_map = {r["instance"]: " / ".join(r.get("mount_paths") or [])
                for r in load_samples(samples_file)}
    safe = merge_model.split("/")[-1] + (f"_{tag}" if tag else "")
    out_file = batch_dir / f"remerge_{safe}.jsonl"
    done = set()
    if out_file.exists():
        for line in out_file.open(encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                if rec.get("merge_ok"):
                    done.add(rec["instance"])
    merge_prompt = (merge_prompt_file or MERGE_PROMPT_FILE).read_text(
        encoding="utf-8")
    fout = out_file.open("a", encoding="utf-8")  # noqa: SIM115
    write_lock = threading.Lock()

    def one(name):
        if name in done:
            print(f"  [resume] {name} 已重合并，跳过", flush=True)
            return
        model_results = load_raw_results(raw_dir, name)
        n_nonempty = sum(1 for mr in model_results if mr["items"])
        paths = path_map.get(name, "")
        merged = merge_one(name, paths, model_results, merge_prompt,
                           raw_dir, api_key, api_url, merge_max_tokens,
                           model=merge_model, raw_tag=f"remerge_{safe}")
        merged["_remerge_model"] = merge_model
        merged["_n_nonempty_models"] = n_nonempty
        with write_lock:
            fout.write(json.dumps(merged, ensure_ascii=False) + "\n")
            fout.flush()
        print(f"  [remerge] {name} ({merge_model}): "
              f"{len(merged['constraints'])} 条, "
              f"ok={merged['merge_ok']}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, names))
    fout.close()
    print(f"\n重合并完成 -> {out_file}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    # generate 子命令
    g = sub.add_parser("generate", help="五路生成 + sol 合并")
    g.add_argument("--samples", type=str, required=True,
                   help="样本清单 jsonl（eval_sample.py 产物）")
    g.add_argument("--limit", type=int, default=0,
                   help="限实例数；0=全量")
    g.add_argument("--offset", type=int, default=0,
                   help="跳过前 N 个样本（从第 N+1 个起取，再应用 --limit）")
    g.add_argument("--workers", type=int, default=3,
                   help="并发实例数（实例内五路全并行，总调用并发≈workers×5；默认 3）")
    g.add_argument("--api-url", type=str, default="",
                   help="API URL，默认 modelhub 网关 4001")
    g.add_argument("--api-key", type=str, default="",
                   help="API KEY，默认 MODELHUB_KEY 环境变量")
    g.add_argument("--out-dir", type=str, required=True,
                   help="输出目录（含 raw/ 子目录）")
    g.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                   help=f"生成 token 预算（默认 {MAX_TOKENS}）")
    g.add_argument("--merge-max-tokens", type=int, default=MERGE_MAX_TOKENS,
                   help=f"合并 token 预算（默认 {MERGE_MAX_TOKENS}）")
    # remerge 子命令（候选合并模型离线对比）
    r = sub.add_parser("remerge",
                       help="复用既有五路 raw，用候选模型重合并（对比实验）")
    r.add_argument("--batch-dir", type=str, required=True,
                   help="批次目录（含 raw/ 五路产物与 constraints.jsonl）")
    r.add_argument("--samples", type=str, required=True,
                   help="样本清单 jsonl（供还原分类路径）")
    r.add_argument("--merge-model", type=str, default="glm/glm-5.3",
                   help="候选合并模型（默认 glm/glm-5.3）")
    r.add_argument("--workers", type=int, default=3,
                   help="并发实例数（默认 3）")
    r.add_argument("--api-url", type=str, default="",
                   help="API URL，默认 modelhub 网关 4001")
    r.add_argument("--api-key", type=str, default="",
                   help="API KEY，默认 MODELHUB_KEY 环境变量")
    r.add_argument("--merge-max-tokens", type=int, default=MERGE_MAX_TOKENS,
                   help=f"合并 token 预算（默认 {MERGE_MAX_TOKENS}）")
    r.add_argument("--merge-prompt", type=str, default="",
                   help="候选合并册；默认同线上册（probe_merge_prompt.md），"
                        "测放宽口径时传变体册")
    r.add_argument("--tag", type=str, default="",
                   help="产物文件名后缀（区分同模型不同册的重合并）")
    args = ap.parse_args()
    api_url = args.api_url or MODELHUB_URL
    api_key = args.api_key or os.environ.get("MODELHUB_KEY") or MODELHUB_KEY
    if args.command == "generate":
        generate(Path(args.samples), args.limit, args.workers,
                 api_key, api_url,
                 Path(args.out_dir), args.max_tokens, args.merge_max_tokens,
                 args.offset)
    elif args.command == "remerge":
        remerge(Path(args.batch_dir), Path(args.samples),
                args.merge_model, args.workers, api_key, api_url,
                args.merge_max_tokens,
                merge_prompt_file=Path(args.merge_prompt)
                if args.merge_prompt else None,
                tag=args.tag)


if __name__ == "__main__":
    main()
