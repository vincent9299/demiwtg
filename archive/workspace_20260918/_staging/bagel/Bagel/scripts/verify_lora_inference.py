"""Standalone inference verification for the trained LoRA adapter.

Replicates app.py's model setup (no source modification), optionally loads a
LoRA adapter via flash_attn_adapt.apply_lora, then runs image understanding
on the two memorized overfit samples and prints base-vs-lora outputs.

Usage:
    python scripts/verify_lora_inference.py \
        --lora_path /tank/demiwtg/bagel/results/lora_overfit/checkpoints/final/lora.safetensors
    (omit --lora_path to get base-model outputs)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
from PIL import Image
from transformers.modeling_utils import init_empty_weights
from accelerate import infer_auto_device_map, load_checkpoint_and_dispatch

from modeling.bagel import Bagel, BagelConfig, Qwen2Config, Qwen2ForCausalLM
from modeling.bagel.siglip_navit import SiglipVisionConfig, SiglipVisionModel
from modeling.qwen2 import Qwen2Tokenizer
from modeling.autoencoder import load_ae
from data.data_utils import add_special_tokens
from data.transforms import ImageTransform
from inferencer import InterleaveInferencer
from train.flash_attn_adapt import apply_lora
from safetensors.torch import load_file

parser = argparse.ArgumentParser()
parser.add_argument("--model_path", type=str, default="/tank/demiwtg/bagel/models/BAGEL-7B-MoT")
parser.add_argument("--lora_path", type=str, default="")
args = parser.parse_args()

model_path = args.model_path
llm_config = Qwen2Config.from_json_file(os.path.join(model_path, "llm_config.json"))
llm_config.qk_norm = True
llm_config.tie_word_embeddings = False
llm_config.freeze_und = False
llm_config.layer_module = "Qwen2MoTDecoderLayer"

vit_config = SiglipVisionConfig.from_json_file(os.path.join(model_path, "vit_config.json"))
vit_config.rope = False
vit_config.num_hidden_layers -= 1

vae_model, vae_config = load_ae(local_path=os.path.join(model_path, "ae.safetensors"))
# ae.safetensors stores bf16 weights; force fp32 so VAE conv works both
# inside and outside autocast regions of the official inferencer
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

device_map = infer_auto_device_map(
    model, max_memory={i: "38GiB" for i in range(torch.cuda.device_count())},
    no_split_module_classes=["Bagel", "Qwen2MoTDecoderLayer"],
)
same_device_modules = ['language_model.model.embed_tokens', 'time_embedder',
                       'latent_pos_embed', 'vae2llm', 'llm2vae', 'connector', 'vit_pos_embed']
first_device = device_map.get(same_device_modules[0], "cuda:0")
for k in same_device_modules:
    device_map[k] = first_device if k in device_map else "cuda:0"

print("[verify] loading ema.safetensors ...", flush=True)
model = load_checkpoint_and_dispatch(
    model, checkpoint=os.path.join(model_path, "ema.safetensors"),
    device_map=device_map, offload_buffers=True, offload_folder="offload",
    dtype=torch.bfloat16, force_hooks=True,
).eval()

if args.lora_path:
    print(f"[verify] applying LoRA and loading {args.lora_path}", flush=True)
    info = apply_lora(model, r=32, alpha=32.0, dropout=0.0)
    print(f"[verify] LoRA applied: {info}")
    # LoRALinear params are created on CPU; move them onto the device of the
    # wrapped (accelerate-dispatched) base linear BEFORE copying values in
    module_dict = dict(model.named_modules())
    for name, mod in module_dict.items():
        if type(mod).__name__ == "LoRALinear":
            dev = mod.base.weight.device
            mod.lora_A.data = mod.lora_A.data.to(dev)
            mod.lora_B.data = mod.lora_B.data.to(dev)
    lora_sd = load_file(args.lora_path)
    model_sd = dict(model.named_parameters())
    n_loaded, n_missing = 0, []
    for k, v in lora_sd.items():
        tgt = model_sd.get(k)
        if tgt is None:
            n_missing.append(k)
            continue
        tgt.data.copy_(v.to(device=tgt.device, dtype=tgt.dtype))
        n_loaded += 1
    print(f"[verify] loaded {n_loaded} LoRA tensors, missing={len(n_missing)}")

vae_transform = ImageTransform(1024, 512, 16)
vit_transform = ImageTransform(980, 224, 14)
print("[debug] vae conv_in.weight dtype:", vae_model.encoder.conv_in.weight.dtype, "bias dtype:", vae_model.encoder.conv_in.bias.dtype, flush=True)
print("[debug] vae conv_in.weight dtype:", vae_model.encoder.conv_in.weight.dtype,
      "bias dtype:", vae_model.encoder.conv_in.bias.dtype,
      "device:", vae_model.encoder.conv_in.weight.device, flush=True)
inferencer = InterleaveInferencer(
    model=model, vae_model=vae_model, tokenizer=tokenizer,
    vae_transform=vae_transform, vit_transform=vit_transform,
    new_token_ids=new_token_ids,
)

img_dir = "/tank/demiwtg/bagel/bagel_example/vlm/images"
# exact prompts used during overfit training (LoRA memorized this pairing)
samples = [
    ("vision-flan_cinic-10+object_presence_animal+152764.jpg",
     "The given image can contain some animals; they can be animals typically found in the wild or domesticated animals. The picture could also contain something that does not fit this description. Your job is to identify if the subject of the image is an animal or not."),
    ("tabmwp_00016350.png",
     "Ms. Watson, the school librarian, wants to know which types of books the students like. She records the type of each book checked out from the library on Friday. There were 3 times as many mystery books checked out as nonfiction books. How many mystery books were checked out?"),
]

for fname, q in samples:
    img = Image.open(os.path.join(img_dir, fname)).convert("RGB")
    with torch.autocast("cuda", dtype=torch.bfloat16):
        out = inferencer(image=img, text=q, do_sample=False, understanding_output=True)
    print("=" * 70)
    print(f"IMAGE: {fname}")
    print(f"Q: {q}")
    print(f"A: {out.get('text', out) if isinstance(out, dict) else out}")
