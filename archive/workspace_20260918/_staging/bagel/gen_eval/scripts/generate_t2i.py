"""Batch text-to-image generation for DPG-Bench / Qwen-Image-Bench evaluation.

Loads BAGEL-7B-MoT on a single GPU (accelerate dispatch + CPU offload),
optionally injects a LoRA adapter via the LORA_PATH env var, and generates
one image per prompt. Supports sharding across GPUs via --shard/--num-shards
and resumes by skipping existing output images.

Output layout (DPG-compatible): <out_dir>/<prompt_id>.png + meta.jsonl
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, "/tank/demiwtg/bagel/Bagel")

import torch
from PIL import Image
from transformers.modeling_utils import init_empty_weights
from accelerate import infer_auto_device_map, load_checkpoint_and_dispatch

from modeling.bagel import Bagel, BagelConfig, Qwen2Config, Qwen2ForCausalLM
from modeling.bagel.siglip_navit import SiglipVisionConfig, SiglipVisionModel
from modeling.qwen2 import Qwen2Tokenizer
from modeling.autoencoder import load_ae
from data.data_utils import add_special_tokens
import random
import numpy as np
from data.transforms import ImageTransform
from inferencer import InterleaveInferencer
from train.flash_attn_adapt import apply_lora
from safetensors.torch import load_file

parser = argparse.ArgumentParser()
parser.add_argument("--model_path", type=str, default="/tank/demiwtg/bagel/models/BAGEL-7B-MoT")
parser.add_argument("--benchmark", type=str, default="dpg", choices=["dpg", "qib"])
parser.add_argument("--out_dir", type=str, required=True)
parser.add_argument("--image_size", type=int, default=512)
parser.add_argument("--num_timesteps", type=int, default=50)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--limit", type=int, default=0, help="only generate first N prompts (0=all)")
parser.add_argument("--shard", type=int, default=0)
parser.add_argument("--num_shards", type=int, default=1)
args = parser.parse_args()

# ---------------------------------------------------------------- prompts
if args.benchmark == "dpg":
    prompt_dir = "/tank/demiwtg/bagel/gen_eval/ELLA/dpg_bench/prompts"
    ids = sorted(os.path.splitext(f)[0] for f in os.listdir(prompt_dir) if f.endswith(".txt"))
    prompts = [(i, open(os.path.join(prompt_dir, f"{i}.txt")).read().strip()) for i in ids]
else:
    qib_prompts = os.environ.get(
        "QIB_PROMPT_FILE",
        "/tank/demiwtg/bagel/gen_eval/prompts/qib_prompts.jsonl",
    )
    prompts = []
    with open(qib_prompts) as f:
        for line in f:
            r = json.loads(line)
            prompts.append((r["id"], r["prompt"]))

if args.limit > 0:
    prompts = prompts[: args.limit]
prompts = [p for i, p in enumerate(prompts) if i % args.num_shards == args.shard]
print(f"[gen] benchmark={args.benchmark} shard={args.shard}/{args.num_shards} "
      f"prompts={len(prompts)}", flush=True)

# ---------------------------------------------------------------- model
model_path = args.model_path
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

# budget GPU memory dynamically: old fixed 38GiB was for a 40GB card and
# forces needless CPU offload on the 80GB A800 (shared with other tenants)
_free_gb = torch.cuda.mem_get_info(0)[0] / 1024**3
gpu_budget = f"{max(int(_free_gb) - 4, 38)}GiB"
print(f"[gen] GPU free {_free_gb:.1f} GB -> dispatch budget {gpu_budget}", flush=True)
device_map = infer_auto_device_map(
    model, max_memory={0: gpu_budget, "cpu": "60GiB"},
    no_split_module_classes=["Bagel", "Qwen2MoTDecoderLayer"],
)
same_device_modules = ['language_model.model.embed_tokens', 'time_embedder',
                       'latent_pos_embed', 'vae2llm', 'llm2vae', 'connector', 'vit_pos_embed']
first_device = device_map.get(same_device_modules[0], "cuda:0")
for k in same_device_modules:
    device_map[k] = first_device if k in device_map else "cuda:0"

print("[gen] loading ema.safetensors ...", flush=True)
model = load_checkpoint_and_dispatch(
    model, checkpoint=os.path.join(model_path, "ema.safetensors"),
    device_map=device_map, offload_buffers=True, offload_folder="offload",
    dtype=torch.bfloat16, force_hooks=True,
).eval()

lora_path = os.environ.get("LORA_PATH", "")
if lora_path:
    info = apply_lora(model, r=32, alpha=32.0, dropout=0.0)
    print(f"[gen] LoRA applied: {info}", flush=True)
    for mod in model.modules():
        if type(mod).__name__ == "LoRALinear":
            dev = mod.base.weight.device
            mod.lora_A.data = mod.lora_A.data.to(dev)
            mod.lora_B.data = mod.lora_B.data.to(dev)
    sd = load_file(lora_path)
    psd = dict(model.named_parameters())
    n = 0
    for k, v in sd.items():
        tgt = psd.get(k)
        if tgt is None:
            continue
        tgt.data.copy_(v.to(device=tgt.device, dtype=tgt.dtype))
        n += 1
    print(f"[gen] loaded {n} LoRA tensors", flush=True)

# with accelerate dispatch+hooks the sampled latent can come back on CPU while
# the fp32 VAE sits on GPU; autocast then casts the GPU conv weights to bf16
# but leaves the CPU input fp32 -> conv dtype error. Move the latent to the
# VAE's device before decoding. (upstream gen_images_mp.py loads the whole
# model with .to(device) and never hits this)
_orig_decode_image = InterleaveInferencer.decode_image
def _decode_image_on_vae_device(self, latent, image_shape):
    latent = latent.to(device=next(self.vae_model.parameters()).device)
    return _orig_decode_image(self, latent, image_shape)
InterleaveInferencer.decode_image = _decode_image_on_vae_device

inferencer = InterleaveInferencer(
    model=model, vae_model=vae_model, tokenizer=tokenizer,
    vae_transform=ImageTransform(1024, args.image_size, 16),
    vit_transform=ImageTransform(980, 224, 14),
    new_token_ids=new_token_ids,
)

# ---------------------------------------------------------------- generate
os.makedirs(args.out_dir, exist_ok=True)
meta_path = os.path.join(args.out_dir, f"meta_shard{args.shard}.jsonl")
meta_f = open(meta_path, "a")

if args.seed > 0:
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
done = set(os.path.splitext(os.path.basename(p))[0]
           for p in glob.glob(os.path.join(args.out_dir, "*.png")))
t0_all = time.time()
n_gen = 0
for pid, prompt in prompts:
    if pid in done:
        continue
    t0 = time.time()
    with torch.autocast("cuda", dtype=torch.bfloat16):
        out = inferencer(
            text=prompt, image_shapes=(args.image_size, args.image_size),
            num_timesteps=args.num_timesteps, cfg_text_scale=4.0,
            cfg_img_scale=1.0, cfg_interval=[0.4, 1.0],
            timestep_shift=3.0, cfg_renorm_min=1.0, cfg_renorm_type="global",
            do_sample=False,
        )
    img = out.get('image') if isinstance(out, dict) else None
    if img is None and isinstance(out, (list, tuple)):
        img = [x for x in out if isinstance(x, Image.Image)][-1]
    if img is None:
        print(f"[gen] WARN id={pid} no image produced, skipping", flush=True)
        continue
    img.save(os.path.join(args.out_dir, f"{pid}.png"))
    dt = time.time() - t0
    n_gen += 1
    meta_f.write(json.dumps({"id": pid, "prompt": prompt, "seconds": round(dt, 1)},
                            ensure_ascii=False) + "\n")
    meta_f.flush()
    print(f"[gen] {n_gen}/{len(prompts)} id={pid} {dt:.1f}s", flush=True)

meta_f.close()
print(f"[gen] shard done: {n_gen} images in {(time.time()-t0_all)/60:.1f} min", flush=True)
