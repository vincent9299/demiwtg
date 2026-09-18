# Copyright 2025
# Adaptation layer for Bagel LoRA training with flash-attn + FSDP.
# This file does NOT modify any Bagel source file: all adaptations are done
# at runtime via monkey-patching and module replacement.

import math
from typing import Dict

import torch
import torch.nn as nn
import torch.nn.functional as F
from flash_attn import flash_attn_func

from modeling.bagel.bagel import Bagel
from modeling.bagel.qwen2_navit import (
    PackedAttention,
    PackedAttentionMoT,
    apply_rotary_pos_emb,
)

# ---------------------------------------------------------------------------
# Global context that carries per-sample split information (split_lens and
# attn_modes) into the patched attention modules.  It is filled by the
# patched Bagel.forward right before the original forward runs, and read by
# the patched attention forward_train implementations (including the
# recomputation pass of activation checkpointing, which happens before the
# next iteration overwrites it).
# ---------------------------------------------------------------------------
FLASH_CTX: Dict[str, object] = {"splits": None}


def build_per_sample_splits(sample_lens, split_lens, attn_modes):
    """Regroup the flat split_lens/attn_modes lists (flattened across packed
    samples by the dataloader) into per-sample absolute split ranges.

    Returns a list (one entry per sample) of lists of
    ``(start, end, mode)`` tuples with offsets relative to the sample start.
    """
    per_sample = []
    idx = 0
    for s_len in sample_lens:
        acc = 0
        splits = []
        while acc < s_len:
            sl = split_lens[idx]
            splits.append((acc, acc + sl, attn_modes[idx]))
            acc += sl
            idx += 1
        assert acc == s_len, "split_lens do not sum to sample_lens"
        per_sample.append(splits)
    return per_sample


class _FlashSentinel:
    """Placeholder passed as nested_attention_masks so that the original
    Bagel.forward skips the (expensive) flex BlockMask construction.  The
    patched attention kernels never read the mask."""


_FLASH_SENTINEL = _FlashSentinel()


def flash_packed_attention(q, k, v, sample_lens):
    """Flash-attention implementation of Bagel's packed training attention.

    Semantics reproduced from ``data.data_utils.prepare_attention_mask_per_sample``:
      - 'causal' split: attends to everything before it + causal inside split
      - 'full'   split: attends to everything before it + full inside split
      - 'noise'  split: attends only to itself (invisible to all other tokens)

    q/k/v: (total_len, num_heads, head_dim) tensors (GQA supported natively).
    """
    splits_per_sample = FLASH_CTX["splits"]
    assert splits_per_sample is not None, (
        "FLASH_CTX not populated: flash_packed_attention must be called via "
        "the patched Bagel.forward during training."
    )
    total = q.shape[0]
    out = q.new_zeros(total, q.shape[1], q.shape[2])

    noise_q, noise_k, noise_v, noise_cu, noise_spans = [], [], [], [0], []

    sample_offset = 0
    for sample_splits, s_len in zip(splits_per_sample, sample_lens):
        for (s, e, mode) in sample_splits:
            qs, qe = sample_offset + s, sample_offset + e
            if mode == "noise":
                noise_q.append(q[qs:qe])
                noise_k.append(k[qs:qe])
                noise_v.append(v[qs:qe])
                noise_cu.append(noise_cu[-1] + (e - s))
                noise_spans.append((qs, qe))
            else:
                # prefix = everything BEFORE this sample inside the packed seq is
                # invisible (samples don't attend across each other), so kv
                # starts at the sample start and ends at the split end.
                kv_start, kv_end = sample_offset, sample_offset + e
                try:
                    o = flash_attn_func(
                        q[qs:qe].unsqueeze(0),
                        k[kv_start:kv_end].unsqueeze(0),
                        v[kv_start:kv_end].unsqueeze(0),
                        causal=(mode == "causal"),
                    )
                except RuntimeError as exc:
                    raise RuntimeError(
                        f"flash_attn_func failed: mode={mode}, seqlen_q={qe - qs}, "
                        f"seqlen_k={kv_end - kv_start}, q.shape={tuple(q.shape)}, "
                        f"k.shape={tuple(k.shape)}, q.dtype={q.dtype}, sample_lens={list(sample_lens)}, "
                        f"splits={sample_splits}, offset={sample_offset}"
                    ) from exc
                out[qs:qe] = o.squeeze(0)
        sample_offset += s_len
    assert sample_offset == total
    return _scatter_noise_and_finish(out, splits_per_sample, noise_q, noise_k, noise_v, noise_cu, noise_spans)


def _scatter_noise_and_finish(out, splits_per_sample, noise_q, noise_k, noise_v, noise_cu, noise_spans):
    if noise_q:
        noise_out = flash_attn_varlen_self(
            torch.cat(noise_q, dim=0),
            torch.cat(noise_k, dim=0),
            torch.cat(noise_v, dim=0),
            noise_cu,
        )
        for (qs, qe), seg in zip(noise_spans, noise_out.split([e - s for s, e in noise_spans])):
            out[qs:qe] = seg
    return out


def flash_attn_varlen_self(q, k, v, cu_list):
    from flash_attn import flash_attn_varlen_func

    cu = torch.tensor(cu_list, dtype=torch.int32, device=q.device)
    max_len = int(max(cu_list[i + 1] - cu_list[i] for i in range(len(cu_list) - 1)))
    return flash_attn_varlen_func(
        q=q, k=k, v=v,
        cu_seqlens_q=cu, cu_seqlens_k=cu,
        max_seqlen_q=max_len, max_seqlen_k=max_len,
        causal=False,
    )


# ---------------------------------------------------------------------------
# Patched forward_train implementations (copies of the official logic with
# the attention core replaced by flash-attn).
# ---------------------------------------------------------------------------

def _flash_packed_attn_forward_train(
    self,
    packed_sequence: torch.Tensor,
    sample_lens,
    attention_mask,
    packed_position_embeddings,
):
    packed_query_states = self.q_proj(packed_sequence).view(-1, self.num_heads, self.head_dim)
    packed_key_states = self.k_proj(packed_sequence).view(-1, self.num_key_value_heads, self.head_dim)
    packed_value_states = self.v_proj(packed_sequence).view(-1, self.num_key_value_heads, self.head_dim)

    packed_query_states = self.q_norm(packed_query_states)
    packed_key_states = self.k_norm(packed_key_states)

    packed_cos, packed_sin = packed_position_embeddings
    packed_query_states, packed_key_states = apply_rotary_pos_emb(
        packed_query_states, packed_key_states, packed_cos, packed_sin, unsqueeze_dim=1
    )

    packed_attn_output = flash_packed_attention(
        packed_query_states, packed_key_states, packed_value_states, sample_lens
    )

    packed_attn_output = packed_attn_output.reshape(-1, self.hidden_size)
    packed_attn_output = self.o_proj(packed_attn_output)
    return packed_attn_output


def _flash_mot_attn_forward_train(
    self,
    packed_sequence: torch.Tensor,
    sample_lens,
    attention_mask,
    packed_position_embeddings,
    packed_und_token_indexes: torch.LongTensor,
    packed_gen_token_indexes: torch.LongTensor,
):
    packed_query_states = packed_sequence.new_zeros((packed_sequence.shape[0], self.num_heads * self.head_dim))
    packed_key_states = packed_sequence.new_zeros((packed_sequence.shape[0], self.num_key_value_heads * self.head_dim))
    packed_value_states = packed_sequence.new_zeros((packed_sequence.shape[0], self.num_key_value_heads * self.head_dim))

    packed_sequence_und = packed_sequence[packed_und_token_indexes]
    packed_sequence_gen = packed_sequence[packed_gen_token_indexes]

    packed_query_states[packed_und_token_indexes] = self.q_proj(packed_sequence_und)
    packed_query_states[packed_gen_token_indexes] = self.q_proj_moe_gen(packed_sequence_gen)

    packed_key_states[packed_und_token_indexes] = self.k_proj(packed_sequence_und)
    packed_key_states[packed_gen_token_indexes] = self.k_proj_moe_gen(packed_sequence_gen)

    packed_value_states[packed_und_token_indexes] = self.v_proj(packed_sequence_und)
    packed_value_states[packed_gen_token_indexes] = self.v_proj_moe_gen(packed_sequence_gen)

    packed_query_states = packed_query_states.view(-1, self.num_heads, self.head_dim)
    packed_key_states = packed_key_states.view(-1, self.num_key_value_heads, self.head_dim)
    packed_value_states = packed_value_states.view(-1, self.num_key_value_heads, self.head_dim)
    if self.config.freeze_und:
        packed_value_states[packed_und_token_indexes] = packed_value_states[packed_und_token_indexes].detach()

    packed_query_states_ = packed_query_states.new_zeros(packed_query_states.shape)
    packed_key_states_ = packed_key_states.new_zeros(packed_key_states.shape)

    packed_query_states_[packed_und_token_indexes] = self.q_norm(packed_query_states[packed_und_token_indexes])
    if self.config.freeze_und:
        packed_query_states_[packed_und_token_indexes] = packed_query_states_[packed_und_token_indexes].detach()
    packed_query_states_[packed_gen_token_indexes] = self.q_norm_moe_gen(packed_query_states[packed_gen_token_indexes])

    packed_key_states_[packed_und_token_indexes] = self.k_norm(packed_key_states[packed_und_token_indexes])
    if self.config.freeze_und:
        packed_key_states_[packed_und_token_indexes] = packed_key_states_[packed_und_token_indexes].detach()
    packed_key_states_[packed_gen_token_indexes] = self.k_norm_moe_gen(packed_key_states[packed_gen_token_indexes])

    packed_cos, packed_sin = packed_position_embeddings
    packed_query_states_, packed_key_states_ = apply_rotary_pos_emb(
        packed_query_states_, packed_key_states_, packed_cos, packed_sin, unsqueeze_dim=1
    )

    packed_attn_output = flash_packed_attention(
        packed_query_states_, packed_key_states_, packed_value_states, sample_lens
    )

    packed_attn_output = packed_attn_output.reshape(-1, self.num_heads * self.head_dim)
    packed_attn_output_ = packed_attn_output.new_zeros(packed_attn_output.shape)
    packed_attn_output_[packed_und_token_indexes] = self.o_proj(packed_attn_output[packed_und_token_indexes])
    packed_attn_output_[packed_gen_token_indexes] = self.o_proj_moe_gen(packed_attn_output[packed_gen_token_indexes])

    return packed_attn_output_


def patch_bagel_flash_attention():
    """Monkey-patch Bagel so that training uses flash-attn kernels instead of
    flex_attention / SDPA, without touching the source files on disk."""

    orig_bagel_forward = Bagel.forward

    def bagel_forward_with_flash_ctx(self, *args, **kwargs):
        if kwargs.get("split_lens") is not None and kwargs.get("attn_modes") is not None:
            sample_lens = list(kwargs["sample_lens"])
            split_lens = list(kwargs["split_lens"])
            attn_modes = list(kwargs["attn_modes"])
            # use_flex dataloader appends a pad segment so that
            # sum(sample_lens) == max_num_tokens (block alignment); real tokens
            # are only len(packed_text_ids ... indexes). Strip the pad segment:
            # flash kernels then run on the true sequence and the (zero-init)
            # tail of packed_sequence is ignored, mirroring flex's block mask.
            real_len = int(kwargs["packed_text_indexes"].max().item()) + 1
            if kwargs.get("packed_vit_token_indexes") is not None:
                real_len = max(real_len, int(kwargs["packed_vit_token_indexes"].max().item()) + 1)
            if kwargs.get("packed_vae_token_indexes") is not None:
                real_len = max(real_len, int(kwargs["packed_vae_token_indexes"].max().item()) + 1)
            if sum(sample_lens) > real_len:
                pad_len = sum(sample_lens) - real_len
                assert split_lens[-1] == pad_len, "tail segment is not the flex pad segment"
                sample_lens = sample_lens[:-1]
                split_lens = split_lens[:-1]
                attn_modes = attn_modes[:-1]
                kwargs["sequence_length"] = real_len
                # tensors built at full (padded) sequence length must be
                # trimmed to the real token range as well
                for key in ("packed_position_ids", "ce_loss_indexes", "mse_loss_indexes"):
                    if kwargs.get(key) is not None and kwargs[key].shape[0] > real_len:
                        kwargs[key] = kwargs[key][:real_len]
            FLASH_CTX["splits"] = build_per_sample_splits(sample_lens, split_lens, attn_modes)
            kwargs.pop("split_lens")
            kwargs.pop("attn_modes")
            kwargs["sample_lens"] = sample_lens
            kwargs["nested_attention_masks"] = _FLASH_SENTINEL
            # pure-VLM batches carry no generation data while the official
            # forward gates the gen branch only on config.visual_gen; flip it
            # off for this call (restored in finally) instead of patching
            # the source.
            skip_gen = kwargs.get("padded_latent") is None
            if skip_gen:
                self.config.visual_gen = False
            # pure-generation (t2i) batches carry no ViT input while the
            # official forward gates the und branch only on config.visual_und
            _vts = kwargs.get("vit_token_seqlens")
            skip_und = _vts is None or (hasattr(_vts, "numel") and _vts.numel() == 0)
            if skip_und:
                self.config.visual_und = False
            try:
                return orig_bagel_forward(self, *args, **kwargs)
            finally:
                if skip_gen:
                    self.config.visual_gen = True
                if skip_und:
                    self.config.visual_und = True
                kwargs["nested_attention_masks"] = None
        return orig_bagel_forward(self, *args, **kwargs)

    Bagel.forward = bagel_forward_with_flash_ctx
    PackedAttention.forward_train = _flash_packed_attn_forward_train
    PackedAttentionMoT.forward_train = _flash_mot_attn_forward_train


# ---------------------------------------------------------------------------
# LoRA (implemented here to avoid any dependency/source change).
# ---------------------------------------------------------------------------

class LoRALinear(nn.Module):
    """Drop-in LoRA adapter around an existing nn.Linear (weights shared,
    frozen; only lora_A / lora_B are trainable)."""

    def __init__(self, base_linear: nn.Linear, r: int = 16, alpha: float = 16.0, dropout: float = 0.0):
        super().__init__()
        self.base = base_linear
        for p in self.base.parameters():
            p.requires_grad = False
        in_f = base_linear.in_features
        out_f = base_linear.out_features
        self.r = r
        self.scaling = alpha / r
        self.lora_dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()
        self.lora_A = nn.Parameter(torch.empty(r, in_f))
        self.lora_B = nn.Parameter(torch.zeros(out_f, r))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        # keep dtype aligned with the wrapped (bf16) model for FSDP flattening
        self.lora_A.data = self.lora_A.data.to(self.base.weight.dtype)
        self.lora_B.data = self.lora_B.data.to(self.base.weight.dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = F.linear(x, self.base.weight, self.base.bias)
        l = self.lora_dropout(x) @ self.lora_A.t() @ self.lora_B.t()
        return out + l * self.scaling


LORA_TARGET_SUFFIXES = (
    "q_proj", "k_proj", "v_proj", "o_proj",
    "q_proj_moe_gen", "k_proj_moe_gen", "v_proj_moe_gen", "o_proj_moe_gen",
)


def apply_lora(model: nn.Module, r: int = 16, alpha: float = 16.0, dropout: float = 0.0) -> Dict[str, int]:
    """Replace the LLM attention projections (und + MoT gen branch) with LoRA
    versions and freeze every base parameter."""
    replaced = 0
    lm = model.language_model
    for name, module in list(lm.named_modules()):
        for child_name in list(module._modules.keys()):
            child = module._modules[child_name]
            if isinstance(child, nn.Linear) and child_name in LORA_TARGET_SUFFIXES:
                module._modules[child_name] = LoRALinear(child, r=r, alpha=alpha, dropout=dropout)
                replaced += 1

    # freeze everything except LoRA params
    for n, p in model.named_parameters():
        p.requires_grad = "lora_" in n and "base" not in n

    return {"replaced_modules": replaced}


def get_lora_state_dict(fsdp_model) -> Dict[str, torch.Tensor]:
    """Gather the full LoRA adapter state dict on rank 0 (call on all ranks)."""
    with FSDP_state_dict_ctx(fsdp_model):
        sd = fsdp_model.state_dict()
    return {k: v.detach().cpu() for k, v in sd.items() if "lora_A" in k or "lora_B" in k}


def FSDP_state_dict_ctx(fsdp_model):
    from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
    from torch.distributed.fsdp import FullStateDictConfig, StateDictType
    return FSDP.state_dict_type(
        fsdp_model,
        StateDictType.FULL_STATE_DICT,
        FullStateDictConfig(offload_to_cpu=True, rank0_only=True),
    )
