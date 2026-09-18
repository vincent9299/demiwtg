#!/usr/bin/env python3
"""serve_qwen_image.py —— Qwen-Image-Edit 本地 OpenAI 兼容图像编辑服务

契约对齐 benchmark/edit/eval_edit_gen.py（edit-gen-v1）：
  POST /v1/chat/completions
    请求: messages[0].content = [image_url(data:base64)..., text]，
          可选 modalities / image_config（aspect_ratio 由 pipeline 依源图自动计算）
    回包: choices[0].message.images[0].image_url.url = data:image/png;base64,...
  GET /v1/models   —— OpenAI 风格清单（供 modelhub 网关自动发现）
  GET /health

随机种子由 (源图 sha256, 指令 sha256) 派生，同请求重放结果可复现。
用法:
  CUDA_VISIBLE_DEVICES=1 python serve_qwen_image.py \
      --model-path /yzp/zhaozy/yangzepeng/0905/models/Qwen-Image-Edit-2511 \
      --served-model-name Qwen-Image-Edit-2511 --port 8002
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import threading
import time
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image

STATE: dict = {"pipe": None, "args": None, "lock": threading.Lock()}


def load_pipeline(args) -> None:
    from diffusers import QwenImageEditPlusPipeline

    t0 = time.time()
    print(f"[serve] loading {args.model_path} ...", flush=True)
    pipe = QwenImageEditPlusPipeline.from_pretrained(
        args.model_path, torch_dtype=torch.bfloat16
    )
    if args.offload:
        pipe.enable_model_cpu_offload()
    else:
        pipe.to("cuda")
    if args.vae_tiling:
        pipe.vae.enable_tiling()
    STATE["pipe"] = pipe
    print(
        f"[serve] loaded in {time.time() - t0:.1f}s "
        f"(offload={args.offload}, steps={args.steps}, true_cfg={args.true_cfg}, "
        f"guidance={args.guidance}, neg={args.negative_prompt!r})",
        flush=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_pipeline(STATE["args"])
    yield


app = FastAPI(lifespan=lifespan)


def decode_data_url(url: str) -> tuple[Image.Image, str]:
    if not url.startswith("data:image/") or ";base64," not in url:
        raise HTTPException(status_code=400, detail="image_url 必须是 data:image/...;base64")
    raw = base64.b64decode(url.split(",", 1)[1], validate=True)
    try:
        return Image.open(io.BytesIO(raw)).convert("RGB"), hashlib.sha256(raw).hexdigest()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"图片解码失败: {exc}")


def parse_messages(body: dict) -> tuple[list[Image.Image], list[str], str]:
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="messages 为空")
    content = messages[-1].get("content")
    parts = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
    images, texts, src_shas = [], [], []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url":
            field = part.get("image_url")
            url = field.get("url") if isinstance(field, dict) else field
            img, sha = decode_data_url(str(url))
            images.append(img)
            src_shas.append(sha)
        elif part.get("type") == "text":
            texts.append(str(part.get("text") or ""))
    prompt = "\n".join(t for t in texts if t.strip())
    if not images or not prompt.strip():
        raise HTTPException(status_code=400, detail="编辑请求需同时包含 image_url 与 text 指令")
    return images, [prompt], "|".join(src_shas)


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if STATE["pipe"] is not None else "loading",
            "model": STATE["args"].served_model_name}


@app.get("/v1/models")
def models() -> dict:
    name = STATE["args"].served_model_name
    return {"object": "list",
            "data": [{"id": name, "object": "model", "owned_by": "local-diffusers"}]}


@app.post("/v1/chat/completions")
def chat_completions(body: dict) -> dict:
    args = STATE["args"]
    pipe = STATE["pipe"]
    if pipe is None:
        raise HTTPException(status_code=503, detail="模型仍在加载")
    images, prompts, src_shas = parse_messages(body)
    seed = int(hashlib.sha256((src_shas + "|" + prompts[0]).encode("utf-8")).hexdigest()[:16], 16) % (2**32)
    generator = torch.Generator(device="cpu").manual_seed(seed)
    t0 = time.time()
    with STATE["lock"]:
        try:
            out = pipe(
                image=images[0] if len(images) == 1 else images,
                prompt=prompts[0],
                negative_prompt=args.negative_prompt,
                num_inference_steps=args.steps,
                true_cfg_scale=args.true_cfg,
                guidance_scale=args.guidance,
                max_sequence_length=args.max_seq_len,
                generator=generator,
            ).images[0]
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            raise HTTPException(status_code=500, detail="CUDA OOM")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    data = buf.getvalue()
    seconds = time.time() - t0
    print(f"[serve] edit ok {out.width}x{out.height} seed={seed} {seconds:.1f}s", flush=True)
    return {
        "id": f"chatcmpl-local-{int(t0 * 1000)}",
        "object": "chat.completion",
        "created": int(t0),
        "model": args.served_model_name,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "",
                "images": [{"type": "image_url",
                            "image_url": {"url": "data:image/png;base64,"
                                          + base64.b64encode(data).decode("ascii")}}],
            },
            "finish_reason": "stop",
        }],
        "usage": {"images": 1, "seconds": round(seconds, 1), "seed": seed},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-path", default="/yzp/zhaozy/yangzepeng/0905/models/Qwen-Image-Edit-2511")
    ap.add_argument("--served-model-name", default="Qwen-Image-Edit-2511")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--true-cfg", type=float, default=4.0)
    ap.add_argument("--guidance", type=float, default=1.0)
    ap.add_argument("--negative-prompt", default=" ")
    ap.add_argument("--max-seq-len", type=int, default=512)
    ap.add_argument("--offload", action="store_true", help="CPU offload（显存不足时）")
    ap.add_argument("--vae-tiling", action="store_true")
    args = ap.parse_args()
    STATE["args"] = args
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
