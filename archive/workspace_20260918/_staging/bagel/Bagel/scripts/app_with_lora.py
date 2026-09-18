"""Launch the official Gradio UI (app.py) with an optional LoRA adapter,
WITHOUT modifying any official source file.

The wrapper monkey-patches accelerate.load_checkpoint_and_dispatch: right
after the base weights are loaded, apply_lora() replaces the attention
projections with LoRALinear and copies the adapter tensors in.

Usage:
    LORA_PATH=/path/to/lora.safetensors python scripts/app_with_lora.py --server_port 7861
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

import accelerate

_orig_load = accelerate.load_checkpoint_and_dispatch


def load_with_lora(model, checkpoint, **kwargs):
    m = _orig_load(model, checkpoint, **kwargs)
    lora_path = os.environ.get("LORA_PATH", "")
    if lora_path:
        import torch
        from train.flash_attn_adapt import apply_lora
        from safetensors.torch import load_file
        info = apply_lora(m, r=int(os.environ.get("LORA_R", "32")),
                          alpha=float(os.environ.get("LORA_ALPHA", "32")))
        print(f"[lora-ui] LoRA applied: {info}", flush=True)
        # LoRALinear params are created on CPU; move them to the wrapped
        # base linear's device before copying values in
        for mod in m.modules():
            if type(mod).__name__ == "LoRALinear":
                dev = mod.base.weight.device
                mod.lora_A.data = mod.lora_A.data.to(dev)
                mod.lora_B.data = mod.lora_B.data.to(dev)
        sd = load_file(lora_path)
        psd = dict(m.named_parameters())
        # accelerate-dispatched modules ignore load_state_dict device
        # placement; copy tensors directly into parameter storage
        n = 0
        for k, v in sd.items():
            tgt = psd.get(k)
            if tgt is None:
                continue
            tgt.data.copy_(v.to(device=tgt.device, dtype=tgt.dtype))
            n += 1
        print(f"[lora-ui] loaded {n} LoRA tensors from {lora_path}", flush=True)
    return m


accelerate.load_checkpoint_and_dispatch = load_with_lora

import runpy
runpy.run_path(os.path.join(REPO, "app.py"), run_name="__main__")
