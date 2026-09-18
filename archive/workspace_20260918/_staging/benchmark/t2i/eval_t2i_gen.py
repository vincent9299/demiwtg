#!/usr/bin/env python3
"""benchmark/t2i/eval_t2i_gen.py —— API 图像模型批量出图（经 modelhub 网关，不泄漏 checks）

读题库 questions.jsonl 的 gen_prompt 原文出题，逐模型调网关生成图片：
  - chat 端点图像模型（gemini-image / qwen-image 系）：/v1/chat/completions，
    回包 message.images[].image_url.url（data:base64）；
  - images 端点模型（gpt-image 系）：/v1/images/generations，回包 data[].b64_json / data[].url；
  - --mode auto（默认）：先 chat，上游明确要求 images 端点 / content 需 list 时自动改参重试。
模型名原样透传网关（provider 前缀完整保留）。产物与 eval_score.py 兼容：
  --out-dir/imgs/<短名>/<qid>.png + --out-dir/responses_<短名>.jsonl
  每行 {"qid": ..., "task": "t2i", "image": "imgs/<短名>/<qid>.png", "ok": true, "seconds": ...}
断点续跑：已有图且 ok 的 qid 跳过。

用法：
  python3 benchmark/t2i/eval_t2i_gen.py \
      --models openrouter/google/gemini-3.1-flash-lite-image qianwen2/qwen-image-3-pro \
      --questions benchmark/t2i/data/eval_bagel_v55/questions.jsonl \
      --out-dir benchmark/t2i/data/eval_models_v55 \
      --endpoint http://127.0.0.1:4001
"""
import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

import requests

DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "data" / "eval_bagel_v55" / "questions.jsonl"
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "eval_models_v55"
IMG_ENDPOINT_HINT = re.compile(r"images? (?:endpoint|api)|/images", re.IGNORECASE)
LIST_CONTENT_HINT = re.compile(r"valid list|content.*(list|array)", re.IGNORECASE)


def short_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model.split("/")[-1])


def load_questions(fp: Path):
    rows = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            if q.get("task", "t2i") == "t2i":
                rows.append(q)
    return rows


def save_image(data: bytes, fp: Path) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(data)


def fetch_url(u: str, timeout: int = 120) -> bytes:
    r = requests.get(u, timeout=timeout)
    r.raise_for_status()
    return r.content


def extract_b64(u: str) -> bytes:
    if u.startswith("data:"):
        return base64.b64decode(u.split(",", 1)[1])
    return base64.b64decode(u)


def gen_one(session, endpoint, model, prompt, mode, timeout):
    """返回 (bytes | None, mode_used, err_hint)"""
    url_chat = f"{endpoint}/chat/completions"
    url_img = f"{endpoint}/images/generations"

    def _chat(list_content=False):
        content = [{"type": "text", "text": prompt}] if list_content else prompt
        return session.post(url_chat, json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
        }, timeout=timeout)

    def _images():
        return session.post(url_img, json={"model": model, "prompt": prompt, "n": 1}, timeout=timeout)

    def _imgs_from_chat(obj):
        msg = (obj.get("choices") or [{}])[0].get("message") or {}
        urls = []
        for im in msg.get("images") or []:
            u = (im.get("image_url") or {}).get("url") if isinstance(im.get("image_url"), dict) else im.get("image_url")
            if u:
                urls.append(u)
        for part in (msg.get("content") if isinstance(msg.get("content"), list) else []) or []:
            u = ((part.get("image_url") or {}).get("url")
                 if isinstance(part.get("image_url"), dict) else part.get("image_url"))
            if u:
                urls.append(u)
        return urls

    def _imgs_from_images(obj):
        out = []
        for d in obj.get("data") or []:
            if d.get("b64_json"):
                out.append(("b64", d["b64_json"]))
            elif d.get("url"):
                out.append(("url", d["url"]))
        return out

    if mode in ("chat", "auto"):
        r = _chat()
        if r.ok:
            urls = _imgs_from_chat(r.json())
            if urls:
                data = extract_b64(urls[0]) if urls[0].startswith("data:") else fetch_url(urls[0])
                return data, "chat", ""
            return None, "chat", "chat 回包无图像字段: " + r.text[:300]
        hint = r.text[:400]
        if mode == "chat":
            return None, "chat", hint
        if not (IMG_ENDPOINT_HINT.search(hint) or LIST_CONTENT_HINT.search(hint)):
            r4 = _images()
            if r4.ok:
                got = _imgs_from_images(r4.json())
                if got:
                    kind, v = got[0]
                    data = base64.b64decode(v) if kind == "b64" else fetch_url(v)
                    return data, "images", ""
            return None, "chat", "chat 失败: " + hint + " | images 回退: " + (r4.text[:200] if not r4.ok else "回包无 data")
        if IMG_ENDPOINT_HINT.search(hint):
            r2 = _images()
            if r2.ok:
                got = _imgs_from_images(r2.json())
                if got:
                    kind, v = got[0]
                    data = base64.b64decode(v) if kind == "b64" else fetch_url(v)
                    return data, "images", ""
                return None, "images", "images 回包无 data: " + r2.text[:300]
            return None, "images", r2.text[:400]
        if LIST_CONTENT_HINT.search(hint):
            r3 = _chat(list_content=True)
            if r3.ok:
                urls = _imgs_from_chat(r3.json())
                if urls:
                    data = extract_b64(urls[0]) if urls[0].startswith("data:") else fetch_url(urls[0])
                    return data, "chat", ""
                return None, "chat", "chat(list) 回包无图像字段: " + r3.text[:300]
            return None, "chat", r3.text[:400]
        return None, "chat", hint
    r = _images()
    if r.ok:
        got = _imgs_from_images(r.json())
        if got:
            kind, v = got[0]
            data = base64.b64decode(v) if kind == "b64" else fetch_url(v)
            return data, "images", ""
        return None, "images", "images 回包无 data: " + r.text[:300]
    return None, "images", r.text[:400]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", required=True, help="网关模型名（原样透传，可多个）")
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--endpoint", default="http://127.0.0.1:4001/v1")
    ap.add_argument("--mode", choices=["auto", "chat", "images"], default="auto")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    qs = load_questions(args.questions)
    if args.limit:
        qs = qs[: args.limit]
    print(f"[t2i-gen] questions={len(qs)} models={args.models} mode={args.mode}")

    session = requests.Session()
    for model in args.models:
        sn = short_name(model)
        img_dir = args.out_dir / "imgs" / sn
        resp_fp = args.out_dir / f"responses_{sn}.jsonl"
        done = {}
        if resp_fp.exists():
            for line in resp_fp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    done[r["qid"]] = r
        ok = fail = skip = 0
        for q in qs:
            qid = q["qid"]
            img_fp = img_dir / f"{qid}.png"
            if done.get(qid, {}).get("ok") and img_fp.exists():
                skip += 1
                continue
            t0 = time.time()
            try:
                data, used, err = gen_one(session, args.endpoint, model, q["gen_prompt"], args.mode, args.timeout)
            except Exception as e:
                data, used, err = None, "-", f"{type(e).__name__}: {e}"
            if data:
                save_image(data, img_fp)
                done[qid] = {"qid": qid, "task": "t2i", "image": f"imgs/{sn}/{qid}.png",
                             "ok": True, "seconds": round(time.time() - t0, 1), "mode": used}
                ok += 1
                print(f"  [{sn}] {qid} ok ({used}, {done[qid]['seconds']}s, {len(data)//1024}KB)")
            else:
                done[qid] = {"qid": qid, "task": "t2i", "image": "", "ok": False,
                             "seconds": round(time.time() - t0, 1), "error": err}
                fail += 1
                print(f"  [{sn}] {qid} FAIL: {err[:200]}")
            resp_fp.write_text("\n".join(json.dumps(done[k], ensure_ascii=False) for k in sorted(done)) + "\n",
                               encoding="utf-8")
        print(f"[t2i-gen] {model}: ok={ok} fail={fail} skip={skip} -> {resp_fp}")


if __name__ == "__main__":
    sys.exit(main())
