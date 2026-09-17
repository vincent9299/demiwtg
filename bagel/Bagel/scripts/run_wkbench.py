"""Demiwtg world-knowledge benchmark runner for BAGEL-7B-MoT (wkbench).

Runs the three task types over a synthesized question bank
(benchmark 按赛道分家：edit 正式题库 benchmark/edit/bench200/questions.jsonl、
t2i 题库 benchmark/t2i/bench200/questions.jsonl，样本图根目录随题库位置推导；
2026-09-08 迁机修复：全部路径常量由脚本自身推导，弃 /tank 旧机绝对路径)：

  vlm  : image + question            -> text answer   (think + understanding)
  edit : image + edit instruction    -> edited image  (think + generate, official cfg)
  t2i  : gen_prompt (no image)       -> image         (generate_t2i.py params)

Single-GPU accelerate-dispatch loading (pattern from gen_eval/scripts/generate_t2i.py),
optional LoRA via LORA_PATH env, shard split + resume by qid.

Outputs in --out_dir:
  responses_shard<N>.jsonl   one record per question (answer text / image path / error)
  imgs/<qid>.png             generated / edited images
  questions.jsonl            snapshot of the question bank used (first run copies it)

Usage:
    cd <repo>/bagel/Bagel && CUDA_VISIBLE_DEVICES=0 <env-bagel>/bin/python \
        scripts/run_wkbench.py --tasks edit \
        --questions ../../benchmark/edit/bench200/questions.jsonl \
        --out_dir ../../benchmark/edit/bench200/bagel
    # LoRA variant:  LORA_PATH=.../lora.safetensors python scripts/run_wkbench.py ...
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

BAGEL_ROOT = Path(__file__).resolve().parents[1]        # bagel/Bagel
REPO_ROOT = Path(__file__).resolve().parents[3]         # demiwtg
sys.path.insert(0, str(BAGEL_ROOT))

DEFAULT_QUESTIONS = str(REPO_ROOT / "benchmark/t2i/bench200/questions.jsonl")


def _resolve_sample_image(questions_path: str, rel: str) -> str:
    """样本图根目录随题库位置推导（题库目录与其上级两级候选，适配
    edit/bench200/ 与 edit/focus200/、t2i data/imgs/ 等布局）"""
    if os.path.isabs(rel):
        return rel
    base = os.path.dirname(os.path.abspath(questions_path))
    for cand_root in (base, os.path.dirname(base)):
        cand = os.path.join(cand_root, rel)
        if os.path.exists(cand):
            return cand
    return os.path.join(base, rel)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model_path", type=str,
                    default=str(REPO_ROOT / "bagel/models/BAGEL-7B-MoT"))
parser.add_argument("--questions", type=str, default=DEFAULT_QUESTIONS)
parser.add_argument("--out_dir", type=str, required=True)
parser.add_argument("--tasks", type=str, default="vlm,edit,t2i",
                    help="comma-separated subset of vlm/edit/t2i")
parser.add_argument("--limit", type=int, default=0, help="only first N questions (0=all)")
parser.add_argument("--image_size", type=int, default=512, help="t2i output side")
parser.add_argument("--num_timesteps", type=int, default=50)
parser.add_argument("--seed", type=int, default=42,
                    help="base seed；每题按 qid 派生确定性 seed")
parser.add_argument("--min_free_gpu_gb", type=float, default=42.0,
                    help="装模前 GPU 最低空闲显存；不足即安全退出")
parser.add_argument("--max_tokens", type=int, default=2048, help="vlm max output tokens")
parser.add_argument("--shard", type=int, default=0)
parser.add_argument("--num_shards", type=int, default=1)
args = parser.parse_args()

tasks = set(t.strip() for t in args.tasks.split(",") if t.strip())

# ---------------------------------------------------------------- questions
with open(args.questions, encoding="utf-8") as f:
    questions = [json.loads(l) for l in f if l.strip()]
questions = [q for q in questions if q.get("task") in tasks]
questions = [q for i, q in enumerate(questions) if i % args.num_shards == args.shard]
if args.limit > 0:
    questions = questions[: args.limit]
print(f"[wkbench] questions={len(questions)} tasks={sorted(tasks)} "
      f"shard={args.shard}/{args.num_shards}", flush=True)

os.makedirs(os.path.join(args.out_dir, "imgs"), exist_ok=True)
snap = os.path.join(args.out_dir, "questions.jsonl")
if not os.path.exists(snap):
    shutil.copy(args.questions, snap)
resp_path = os.path.join(args.out_dir, f"responses_shard{args.shard}.jsonl")
done = set()
if os.path.exists(resp_path):
    with open(resp_path, encoding="utf-8") as f:
        done = {json.loads(l)["qid"] for l in f if l.strip()}

# ---------------------------------------------------------------- model
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

_free_gb = torch.cuda.mem_get_info(0)[0] / 1024**3
if _free_gb < args.min_free_gpu_gb:
    raise RuntimeError(f"GPU 空闲显存 {_free_gb:.1f} GiB < 安全门槛 "
                       f"{args.min_free_gpu_gb:.1f} GiB，拒绝装模")
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
    device_map=device_map, offload_buffers=True, offload_folder="offload",
    dtype=torch.bfloat16, force_hooks=True,
).eval()

lora_path = os.environ.get("LORA_PATH", "")
if lora_path:
    from train.flash_attn_adapt import apply_lora
    from safetensors.torch import load_file
    info = apply_lora(model, r=int(os.environ.get("LORA_R", "32")),
                      alpha=float(os.environ.get("LORA_ALPHA", "32")))
    print(f"[wkbench] LoRA applied: {info}", flush=True)
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
    print(f"[wkbench] loaded {n} LoRA tensors from {lora_path}", flush=True)

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

inferencer = InterleaveInferencer(
    model=model, vae_model=vae_model, tokenizer=tokenizer,
    vae_transform=ImageTransform(1024, args.image_size, 16),
    vit_transform=ImageTransform(980, 224, 14),
    new_token_ids=new_token_ids,
)

# ---------------------------------------------------------------- run
resp_f = open(resp_path, "a", encoding="utf-8")
t0_all = time.time()
n_ok = 0
for qi, q in enumerate(questions, 1):
    qid = q.get("qid", f"q{qi}")
    if qid in done:
        continue
    task = q.get("task")
    rec = {"qid": qid, "task": task, "difficulty": q.get("difficulty"),
           "knowledge_dim": q.get("knowledge_dim"), "probe_dims": q.get("probe_dims"),
           "sample_image": q.get("_sample_image"), "model": "BAGEL-7B-MoT",
           "ok": False}
    qid_seed = args.seed + int.from_bytes(hashlib.sha256(qid.encode("utf-8")).digest()[:4], "big")
    torch.manual_seed(qid_seed)
    torch.cuda.manual_seed_all(qid_seed)
    rec["seed"] = qid_seed
    t0 = time.time()
    try:
        if task == "vlm":
            img = Image.open(_resolve_sample_image(args.questions, q["_sample_image"]))
            stem = q.get("stem", "")
            if q.get("answer_format") == "choice" and q.get("choices"):
                stem += "\n（四选一，请先给出最终选项再说明理由）"
            with torch.autocast("cuda", dtype=torch.bfloat16):
                out = inferencer(
                    image=pil_img2rgb(img), text=stem,
                    understanding_output=True, think=True, do_sample=False,
                    max_think_token_n=args.max_tokens,
                )
            rec["response"] = out.get("text") or ""
            rec["ok"] = bool(rec["response"].strip())
        elif task == "edit":
            src_path = _resolve_sample_image(args.questions, q["_sample_image"])
            img = Image.open(src_path)
            with open(src_path, "rb") as src_f:
                rec["source_sha256"] = hashlib.sha256(src_f.read()).hexdigest()
            rec["instruction_sha256"] = hashlib.sha256(
                q.get("edit_instruction", "").encode("utf-8")).hexdigest()
            with torch.autocast("cuda", dtype=torch.bfloat16):
                out = inferencer(
                    image=pil_img2rgb(img), text=q.get("edit_instruction", ""),
                    understanding_output=False, think=True, do_sample=False,
                    cfg_text_scale=4.0, cfg_img_scale=2.0, cfg_interval=[0.0, 1.0],
                    cfg_renorm_min=0.0, cfg_renorm_type="text_channel",
                    timestep_shift=3.0, num_timesteps=args.num_timesteps,
                )
            eimg = out.get("image")
            if eimg is not None:
                out_path = os.path.join(args.out_dir, "imgs", f"{qid}.png")
                eimg.save(out_path)
                rec["image"] = f"imgs/{qid}.png"
                with open(out_path, "rb") as out_f:
                    rec["output_sha256"] = hashlib.sha256(out_f.read()).hexdigest()
                rec["width"], rec["height"] = eimg.size
                rec["think"] = out.get("text") or ""
                rec["ok"] = True
        elif task == "t2i":
            with torch.autocast("cuda", dtype=torch.bfloat16):
                out = inferencer(
                    text=q.get("gen_prompt", ""),
                    image_shapes=(args.image_size, args.image_size),
                    num_timesteps=args.num_timesteps, cfg_text_scale=4.0,
                    cfg_img_scale=1.0, cfg_interval=[0.4, 1.0],
                    timestep_shift=3.0, cfg_renorm_min=1.0, cfg_renorm_type="global",
                    do_sample=False,
                )
            gimg = out.get("image")
            if gimg is not None:
                gimg.save(os.path.join(args.out_dir, "imgs", f"{qid}.png"))
                rec["image"] = f"imgs/{qid}.png"
                rec["ok"] = True
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    rec["seconds"] = round(time.time() - t0, 1)
    resp_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    resp_f.flush()
    if rec["ok"]:
        n_ok += 1
    print(f"[wkbench] {qi}/{len(questions)} {qid} ({task}) "
          f"{'ok' if rec['ok'] else 'FAIL'} {rec['seconds']}s "
          f"{rec.get('error', '')}", flush=True)

resp_f.close()
print(f"[wkbench] done: {n_ok} ok / {len(questions)} total in "
      f"{(time.time() - t0_all) / 60:.1f} min -> {args.out_dir}", flush=True)
