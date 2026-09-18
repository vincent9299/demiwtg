"""VLMEvalKit run wrapper for BAGEL-7B-MoT with optional LoRA injection.

Works exactly like VLMEvalKit's run.py, but when the LORA_PATH environment
variable is set, accelerate.load_checkpoint_and_dispatch is monkey-patched so
the LoRA adapter (same format as train/lora_pretrain_navit.py saves) is
applied right after the base weights load. No official source files modified.

Usage:
    # base model
    CUDA_VISIBLE_DEVICES=1 python scripts/run_bagel_eval.py --data MMStar --model BAGEL-7B-MoT
    # with LoRA
    CUDA_VISIBLE_DEVICES=1 LORA_PATH=/tank/demiwtg/bagel/results/lora_overfit/checkpoints/final/lora.safetensors \
        python scripts/run_bagel_eval.py --data MMStar --model BAGEL-7B-MoT
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

VLMKIT = "/tank/demiwtg/bagel/VLMEvalKit"
sys.path.insert(0, VLMKIT)
os.chdir(VLMKIT)

import accelerate

_orig_load = accelerate.load_checkpoint_and_dispatch


def _apply_lora_after_load(model, checkpoint, **kwargs):
    m = _orig_load(model, checkpoint, **kwargs)
    lora_path = os.environ.get("LORA_PATH", "")
    if lora_path:
        import torch
        sys.path.insert(0, "/tank/demiwtg/bagel/Bagel")
        from train.flash_attn_adapt import apply_lora
        from safetensors.torch import load_file
        info = apply_lora(m, r=int(os.environ.get("LORA_R", "32")),
                          alpha=float(os.environ.get("LORA_ALPHA", "32")))
        print(f"[lora-eval] LoRA applied: {info}", flush=True)
        # LoRALinear params are created on CPU; move them to the wrapped
        # base linear's device before copying values in
        for mod in m.modules():
            if type(mod).__name__ == "LoRALinear":
                dev = mod.base.weight.device
                mod.lora_A.data = mod.lora_A.data.to(dev)
                mod.lora_B.data = mod.lora_B.data.to(dev)
        sd = load_file(lora_path)
        psd = dict(m.named_parameters())
        n = 0
        for k, v in sd.items():
            tgt = psd.get(k)
            if tgt is None:
                continue
            tgt.data.copy_(v.to(device=tgt.device, dtype=tgt.dtype))
            n += 1
        print(f"[lora-eval] loaded {n} LoRA tensors from {lora_path}", flush=True)
    return m


accelerate.load_checkpoint_and_dispatch = _apply_lora_after_load

import runpy
runpy.run_path(os.path.join(VLMKIT, "run.py"), run_name="__main__")
