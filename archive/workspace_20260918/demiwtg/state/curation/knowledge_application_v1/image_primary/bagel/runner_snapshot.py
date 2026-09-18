"""Native multi-image BAGEL adapter for knowledge_application_v1.

Loader and VAE dtype/device shims copied from curation/probe_bagel.py, itself
adapted from frozen run_wkbench.py (dbf129730398550ab031104c22f0e16ac267e27eac3acd9dc602a347f7d932cd).
No old runner is imported or modified. No services are managed. Native images
enter both ViT and VAE contexts; references are never converted to captions.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
BAGEL_ROOT = REPO_ROOT / "bagel/Bagel"
CONDITIONS = {"baseline", "text", "image", "multimodal", "explicit_target"}
CONFIG = dict(image_shapes=(1024, 1024), num_timesteps=50,
              cfg_text_scale=4.0, cfg_img_scale=1.5, cfg_interval=[0.4, 1.0],
              timestep_shift=3.0, cfg_renorm_min=0.0, cfg_renorm_type="global",
              do_sample=False, think=False, understanding_output=False,
              max_think_token_n=1000, text_temperature=0.3, enable_taylorseer=False)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def publish(path, data):
    """Durable exclusive publication; an existing artifact must be identical."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    try:
        with open(tmp, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise RuntimeError(f"Refusing to overwrite {path}")
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def image_role_text(index, role):
    # Must be shared verbatim with other adapters for input parity.
    return f"Image {index} role: {role}."


def validate_jobs(path):
    from PIL import Image
    raw = Path(path).read_bytes()
    jobs = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    seen = set()
    for job in jobs:
        jid = job["job_id"]
        if not isinstance(jid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", jid):
            raise ValueError("job_id must be a safe nonempty filename")
        if jid in seen:
            raise ValueError(f"Duplicate job_id {jid}")
        seen.add(jid)
        if not isinstance(job["question_id"], str) or not job["question_id"]:
            raise ValueError(f"Missing question_id: {jid}")
        if job["task"] not in {"t2i", "edit"} or job["condition"] not in CONDITIONS:
            raise ValueError(f"Invalid task/condition: {jid}")
        if not isinstance(job["prompt"], str) or not job["prompt"].strip():
            raise ValueError(f"Empty prompt: {jid}")
        if type(job["seed"]) is not int or not 0 <= job["seed"] < 2**63:
            raise ValueError(f"Invalid seed: {jid}")
        if not isinstance(job["images"], list):
            raise ValueError(f"images must be an ordered list: {jid}")
        sources = 0
        for image in job["images"]:
            if image["role"] not in {"edit_source", "retrieval_reference"}:
                raise ValueError(f"Invalid image role: {jid}")
            sources += image["role"] == "edit_source"
            p = Path(image["path"])
            image_bytes = p.read_bytes()
            if not p.is_absolute() or sha(image_bytes) != image["sha256"]:
                raise ValueError(f"Missing/relative/changed image: {jid}: {p}")
            # Session invokes this before pausing Qwen. A correct hash of a bad
            # image is insufficient; both model adapters must decode the asset.
            with Image.open(io.BytesIO(image_bytes)) as decoded:
                if decoded.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError(f"Unsupported shared image format: {jid}: {p}")
                if getattr(decoded, "n_frames", 1) != 1:
                    raise ValueError(f"Freeze a single image frame before generation: {jid}: {p}")
                decoded.verify()
            with Image.open(io.BytesIO(image_bytes)) as decoded:
                decoded.load()
        if sources != (1 if job["task"] == "edit" else 0):
            raise ValueError(f"edit requires exactly one source; t2i requires none: {jid}")
    return jobs, raw


def load_model(model_path, offload_dir):
    sys.path.insert(0, str(BAGEL_ROOT))
    import torch
    from PIL import Image
    from transformers.modeling_utils import init_empty_weights
    from accelerate import infer_auto_device_map, load_checkpoint_and_dispatch

    from modeling.bagel import Bagel, BagelConfig, Qwen2Config, Qwen2ForCausalLM
    from modeling.bagel.siglip_navit import SiglipVisionConfig, SiglipVisionModel
    from modeling.qwen2 import Qwen2Tokenizer
    from modeling.autoencoder import load_ae
    from data.data_utils import add_special_tokens, pil_img2rgb
    from data.transforms import ImageTransform
    from inferencer import InterleaveInferencer

    model_path = str(model_path)
    llm_config = Qwen2Config.from_json_file(os.path.join(model_path, "llm_config.json"))
    llm_config.qk_norm = True
    llm_config.tie_word_embeddings = False
    llm_config.layer_module = "Qwen2MoTDecoderLayer"

    vit_config = SiglipVisionConfig.from_json_file(os.path.join(model_path, "vit_config.json"))
    vit_config.rope = False
    vit_config.num_hidden_layers -= 1

    vae_model, vae_config = load_ae(local_path=os.path.join(model_path, "ae.safetensors"))
    vae_model = vae_model.to("cuda").float().eval()

    config = BagelConfig(
        visual_gen=True, visual_und=True,
        llm_config=llm_config, vit_config=vit_config, vae_config=vae_config,
        vit_max_num_patch_per_side=70, connector_act='gelu_pytorch_tanh',
        latent_patch_size=2, max_latent_size=64,
    )

    with init_empty_weights():
        language_model = Qwen2ForCausalLM(llm_config)
        vit_model = SiglipVisionModel(vit_config)
        model = Bagel(language_model, vit_model, config)
        model.vit_model.vision_model.embeddings.convert_conv2d_to_linear(vit_config, meta=True)

    tokenizer = Qwen2Tokenizer.from_pretrained(model_path)
    tokenizer, new_token_ids, num_new_tokens = add_special_tokens(tokenizer)
    if num_new_tokens > 0:
        model.language_model.resize_token_embeddings(len(tokenizer))

    _free_gb = torch.cuda.mem_get_info(0)[0] / 1024**3
    if _free_gb < 42.0:
        raise RuntimeError(f"GPU 空闲显存 {_free_gb:.1f} GiB < 安全门槛 "
                           f"{42.0:.1f} GiB，拒绝装模")
    gpu_budget = f"{max(int(_free_gb) - 4, 38)}GiB"
    print(f"[wkbench] GPU free {_free_gb:.1f} GB -> dispatch budget {gpu_budget}", flush=True)
    device_map = infer_auto_device_map(
        model, max_memory={0: gpu_budget, "cpu": "60GiB"},
        no_split_module_classes=["Bagel", "Qwen2MoTDecoderLayer"],
    )
    same_device_modules = ['language_model.model.embed_tokens', 'time_embedder',
                           'latent_pos_embed', 'vae2llm', 'llm2vae', 'connector', 'vit_pos_embed']
    first_device = device_map.get(same_device_modules[0], "cuda:0")
    for k in same_device_modules:
        device_map[k] = first_device if k in device_map else "cuda:0"

    print("[wkbench] loading ema.safetensors ...", flush=True)
    model = load_checkpoint_and_dispatch(
        model, checkpoint=os.path.join(model_path, "ema.safetensors"),
        device_map=device_map, offload_buffers=True, offload_folder=str(offload_dir),
        dtype=torch.bfloat16, force_hooks=True,
    ).eval()

    # dispatch+hooks may hand back a CPU latent while the fp32 VAE sits on GPU;
    # move the latent to the VAE device before decoding (same fix as generate_t2i.py)
    _orig_decode_image = InterleaveInferencer.decode_image
    def _decode_image_on_vae_device(self, latent, image_shape):
        latent = latent.to(device=next(self.vae_model.parameters()).device)
        return _orig_decode_image(self, latent, image_shape)
    InterleaveInferencer.decode_image = _decode_image_on_vae_device

    # the fp32 VAE must run outside the bf16 autocast: autocast feeds bf16 into
    # conv_in whose fp32 bias then mismatches (edit path encodes the input image).
    # instance attr shadows the module call, so the dispatch hook no longer moves
    # the input — align device here explicitly.
    _orig_vae_encode = vae_model.encode
    _vae_device = next(vae_model.parameters()).device
    def _vae_encode_fp32(x):
        with torch.autocast("cuda", enabled=False):
            return _orig_vae_encode(x.to(device=_vae_device).float())
    vae_model.encode = _vae_encode_fp32

    # mirror fix for the edit path: fp32 VAE latent enters vae2llm through NaiveCache,
    # which accelerate dispatch hooks never wrap, so autocast can't reconcile the
    # bf16 projection weights — run the projection on vae2llm's own device/dtype
    _orig_forward_cache_update_vae = Bagel.forward_cache_update_vae
    def _forward_cache_update_vae_dtype_safe(self, vae_model, past_key_values, padded_images,
                                              patchified_vae_latent_shapes, packed_vae_position_ids,
                                              packed_timesteps, packed_vae_token_indexes,
                                              packed_text_ids, packed_text_indexes,
                                              packed_position_ids, packed_seqlens, packed_indexes,
                                              key_values_lens, packed_key_value_indexes):
        _orig_vae2llm = self.vae2llm
        _w = _orig_vae2llm.weight
        _dev, _dt = _w.device, _w.dtype
        class _Vae2LlmShim:
            def __call__(_self, x):
                y = torch.nn.functional.linear(x.to(device=_dev, dtype=_dt), _w, _orig_vae2llm.bias)
                return y.to(dtype=_dt)
        # bypass nn.Module.__setattr__ (rejects non-Module child assignment)
        object.__setattr__(self, "vae2llm", _Vae2LlmShim())
        try:
            return _orig_forward_cache_update_vae(
                self, vae_model, past_key_values, padded_images, patchified_vae_latent_shapes,
                packed_vae_position_ids, packed_timesteps, packed_vae_token_indexes,
                packed_text_ids, packed_text_indexes, packed_position_ids, packed_seqlens,
                packed_indexes, key_values_lens, packed_key_value_indexes)
        finally:
            object.__setattr__(self, "vae2llm", _orig_vae2llm)
    Bagel.forward_cache_update_vae = _forward_cache_update_vae_dtype_safe

    class SquareOutputInferencer(InterleaveInferencer):
        # Upstream interleave resets image_shapes from each input. Override only
        # final output geometry; preserve all source/reference image aspect ratios.
        def gen_image(self, image_shape, *positional, **kwargs):
            return super().gen_image((1024, 1024), *positional, **kwargs)

    inferencer = SquareOutputInferencer(
        model=model, vae_model=vae_model, tokenizer=tokenizer,
        vae_transform=ImageTransform(1024, 1024, 16),
        vit_transform=ImageTransform(980, 224, 14),
        new_token_ids=new_token_ids,
    )


    return inferencer, torch


def make_inputs(job):
    from PIL import Image
    inputs, manifest = [], []
    for i, entry in enumerate(job["images"], 1):
        raw = Path(entry["path"]).read_bytes()
        if sha(raw) != entry["sha256"]:
            raise ValueError("Input changed after jobs validation")
        with Image.open(io.BytesIO(raw)) as opened:
            # Match upstream pil_img2rgb semantics, including transparent input.
            from data.data_utils import pil_img2rgb
            image = pil_img2rgb(opened).copy()
        label = image_role_text(i, entry["role"])
        inputs.extend([label, image])
        manifest.extend([{"type": "text", "text": label},
                         {"type": "image", **entry, "decoded_size": list(image.size)}])
    inputs.append(job["prompt"])
    manifest.append({"type": "text", "text": job["prompt"]})
    return inputs, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--shard-id", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--model-path", default=str(REPO_ROOT / "bagel/models/BAGEL-7B-MoT"))
    parser.add_argument("--validate-only", action="store_true", help="No torch import or GPU access")
    args = parser.parse_args()
    if args.num_shards <= 0 or not 0 <= args.shard_id < args.num_shards:
        parser.error("Invalid shard configuration")
    jobs, raw = validate_jobs(args.jobs)
    selected = [j for i, j in enumerate(jobs) if i % args.num_shards == args.shard_id]
    if args.validate_only:
        print(json.dumps({"jobs": len(jobs), "selected": len(selected), "jobs_sha256": sha(raw)}))
        return
    if os.environ.get("LORA_PATH"):
        raise RuntimeError("This baseline runner does not implicitly load environment LoRA weights")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    if not visible or len(visible.split(",")) != 1:
        raise RuntimeError("Set CUDA_VISIBLE_DEVICES to exactly one GPU")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"shard_{args.shard_id}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        publish(out / "jobs.jsonl", raw)
        runner_bytes = Path(__file__).read_bytes()
        publish(out / "runner_snapshot.py", runner_bytes)
        common = dict(model="BAGEL-7B-MoT", model_path=str(Path(args.model_path).resolve()),
                      runner_sha256=sha(runner_bytes), jobs_sha256=sha(raw),
                      inference_config=CONFIG, input_protocol="role-text,image,...,final-job-prompt",
                      output_geometry_override="gen_image only: 1024x1024",
                      num_shards=args.num_shards)
        publish(out / "run_config.json", encoded(common))
        pending = []
        for job in selected:
            record_dir = out / "jobs" / job["job_id"]
            if (record_dir / "result.json").exists():
                continue
            if (record_dir / "request.json").exists():
                # An interrupted attempt must not silently become a second sample.
                publish(record_dir / "result.json", encoded(dict(job_id=job["job_id"],
                        question_id=job["question_id"], task=job["task"], condition=job["condition"],
                        ok=False, error="Interrupted prior attempt; no automatic retry")))
                continue
            pending.append(job)
        if not pending:
            return
        try:
            inferencer, torch = load_model(Path(args.model_path), out / f"offload_shard{args.shard_id}")
        except Exception:
            error = traceback.format_exc()
            for job in pending:
                d = out / "jobs" / job["job_id"]
                publish(d / "request.json", encoded({"job": job, **common}))
                publish(d / "result.json", encoded(dict(job_id=job["job_id"], question_id=job["question_id"],
                        task=job["task"], condition=job["condition"], ok=False, stage="model_load", error=error)))
            raise
        from PIL import Image
        for job in pending:
            d = out / "jobs" / job["job_id"]
            request = {"job": job, **common, "shard_id": args.shard_id,
                       "prompt_sha256": sha(job["prompt"].encode()), "seed": job["seed"]}
            publish(d / "request.json", encoded(request))
            started = time.time()
            result = {"job_id": job["job_id"], "question_id": job["question_id"],
                      "task": job["task"], "condition": job["condition"], "seed": job["seed"],
                      "ok": False, "started_unix": started}
            try:
                inputs, manifest = make_inputs(job)
                publish(d / "actual_inputs.json", encoded(manifest))
                torch.manual_seed(job["seed"])
                torch.cuda.manual_seed_all(job["seed"])
                outputs = inferencer.interleave_inference(inputs, **CONFIG)
                images = [x for x in outputs if isinstance(x, Image.Image)]
                result["think_text"] = [x for x in outputs if isinstance(x, str)]
                if len(images) != 1 or images[0].size != (1024, 1024):
                    raise RuntimeError("Expected exactly one 1024-square generated image")
                buf = io.BytesIO()
                images[0].save(buf, format="PNG")
                data = buf.getvalue()
                publish(d / "image.png", data)
                result.update(ok=True, image=str(d / "image.png"), output_sha256=sha(data),
                              width=1024, height=1024)
            except Exception:
                result["error"] = traceback.format_exc()
                torch.cuda.empty_cache()
            result["seconds"] = round(time.time() - started, 3)
            publish(d / "result.json", encoded(result))
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
