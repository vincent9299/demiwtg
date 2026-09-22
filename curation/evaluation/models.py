"""Native model loading and input/output adaptation; scheduling is in the notebook."""
import base64,io,json,os
from pathlib import Path
from curation.preparation.contracts import ROOT,digest
from curation.evaluation.contracts import MODELS,verify_request

QWEN_CONFIG = {"num_inference_steps": 40, "true_cfg_scale": 4.0, "guidance_scale": 1.0,
               "negative_prompt": " ", "max_sequence_length": 1024, "width": 1024, "height": 1024}
GEMINI_CONFIG = {"modalities": ["image", "text"], "image_config": {"aspect_ratio": "1:1"}}

def unpack(job):
    from PIL import Image
    verify_request(job)
    interleaved, texts, images = [], [], []
    for part in job["request"]["messages"][0]["content"]:
        if part["type"] == "text":
            texts.append(part["text"]); interleaved.append(part["text"])
        else:
            raw = base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(raw)) as im:
                # Shared deterministic alpha-to-white decode; original bytes retained.
                rgba = im.convert("RGBA")
                white = Image.new("RGBA", rgba.size, "white")
                rgb = Image.alpha_composite(white, rgba).convert("RGB")
            images.append(rgb); interleaved.append(rgb)
    return interleaved, "\n".join(texts), images

def parse_gemini(response):
    message = response["choices"][0]["message"]
    urls = []
    for part in (message.get("images") or []) + (message.get("content") if isinstance(message.get("content"), list) else []):
        obj = part.get("image_url")
        url = obj.get("url") if isinstance(obj, dict) else obj
        if url and url not in urls:
            urls.append(url)
    if len(urls) != 1 or not urls[0].startswith("data:image/") or ";base64," not in urls[0]:
        raise ValueError("Expected exactly one inline image; no remote downloads or second attempts")
    return base64.b64decode(urls[0].split(",", 1)[1], validate=True)

def load_native(backend, run):
    import torch
    if os.environ.get("LORA_PATH"):
        raise ValueError("Unrequested LoRA is not allowed")
    if len(os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")) != 1 or not os.environ.get("CUDA_VISIBLE_DEVICES"):
        raise ValueError("Set exactly one CUDA_VISIBLE_DEVICES")
    if backend == "bagel":
        from curation.evaluation.bagel import load_model, CONFIG
        model, torch = load_model(ROOT.parent / "models/BAGEL-7B-MoT", run / "offload")
        return model, CONFIG, torch
    from diffusers import QwenImagePipeline, QwenImageEditPlusPipeline
    cls = QwenImagePipeline if backend == "qwen2512" else QwenImageEditPlusPipeline
    model = cls.from_pretrained(ROOT.parent / "models" / MODELS[backend],
                               torch_dtype=torch.bfloat16, local_files_only=True).to("cuda")
    return model, QWEN_CONFIG, torch

def environment_record(backend):
    import importlib.metadata
    versions = {}
    for name in ("torch", "transformers", "diffusers", "accelerate", "Pillow"):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    root = ROOT.parent / "models/BAGEL-7B-MoT" if backend == "bagel" else ROOT.parent / "models" / MODELS[backend]
    return {"packages": versions, "model_root": str(root.resolve()),
        "config_sha256": {str(p.relative_to(root)): digest(p.read_bytes()) for p in root.rglob("*.json")},
        "weights_stat": [{"path": str(p.resolve()), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                         for p in sorted(root.rglob("*.safetensors"))],
        "weight_identity_policy": "local path/config hashes/weight size and mtime; weights not fully rehashed"}
