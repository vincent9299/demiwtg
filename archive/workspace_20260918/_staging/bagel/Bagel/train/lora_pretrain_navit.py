# Copyright 2025 Bytedance Ltd. and/or its affiliates.
# SPDX-License-Identifier: Apache-2.0
#
# LoRA fine-tuning script for BAGEL with flash-attn + FSDP.
# This is a COPY of train/pretrain_unified_navit.py with LoRA / flash-attn
# adaptations.  NO Bagel source file is modified: all adaptations live in
# train/flash_attn_adapt.py and are applied at runtime.

import functools
import gc
import os
import wandb
import yaml
from time import time
from dataclasses import dataclass, field

import torch
import torch.distributed as dist
from torch.distributed.algorithms._checkpoint.checkpoint_wrapper import (
    CheckpointImpl,
    apply_activation_checkpointing,
    checkpoint_wrapper,
)
from torch.utils.data import DataLoader
from transformers import HfArgumentParser, set_seed
from transformers.optimization import (
    get_constant_schedule_with_warmup,
    get_cosine_with_min_lr_schedule_with_warmup,
)

from data.dataset_base import DataConfig, PackedDataset, collate_wrapper
from data.data_utils import add_special_tokens
from modeling.autoencoder import load_ae
from modeling.bagel import (
    BagelConfig, Bagel, Qwen2Config, Qwen2ForCausalLM, SiglipVisionConfig, SiglipVisionModel
)
from modeling.bagel.modeling_utils import MLPconnector, TimestepEmbedder, PositionEmbedding
from modeling.bagel.qwen2_navit import (
    Qwen2DecoderLayer, Qwen2MoEDecoderLayer, Qwen2MoTDecoderLayer,
)
from modeling.bagel.siglip_navit import SiglipEncoderLayer, SiglipVisionTransformer
from modeling.qwen2 import Qwen2Tokenizer
from train.train_utils import create_logger, get_latest_ckpt
from train.fsdp_utils import (
    FSDPCheckpoint, FSDPConfig, grad_checkpoint_check_fn, fsdp_wrapper,
)
from torch.distributed.device_mesh import init_device_mesh
from torch.distributed.fsdp import (
    CPUOffload, FullyShardedDataParallel as FSDP, MixedPrecision,
    BackwardPrefetch, ShardingStrategy,
)
from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy
from train.pretrain_unified_navit import (
    ModelArguments, DataArguments, TrainingArguments,
    count_parameters, qwen2_flop_coefficients, detect_peak_tflops,
)
from train.flash_attn_adapt import (
    apply_lora, patch_bagel_flash_attention, get_lora_state_dict,
)


@dataclass
class LoRAArguments:
    lora_r: int = field(default=32, metadata={"help": "LoRA rank."})
    lora_alpha: float = field(default=32.0, metadata={"help": "LoRA scaling alpha."})
    lora_dropout: float = field(default=0.0, metadata={"help": "LoRA dropout."})
    example_data_dir: str = field(
        default="/tank/demiwtg/bagel/bagel_example",
        metadata={"help": "Root dir of the official bagel_example dataset; "
                          "used to fill DATASET_INFO at runtime (source files stay untouched)."}
    )
    lora_save_every: int = field(default=0, metadata={"help": "Save LoRA adapter every N steps (0 = only at the end)."})


def patch_dataset_paths(example_data_dir: str):
    """Fill the 'your_data_path' placeholders of data/dataset_info.py at
    runtime instead of editing the source file. The parquet_info json is
    written by rank 0 only (atomic rename + barrier) to avoid multi-rank
    races truncating/reading a half-written file."""
    import data.dataset_info as dataset_info
    di = dataset_info.DATASET_INFO
    # Deterministic edit layout: the official parse_row randomly chooses how
    # many edit steps to use, which changes the sequence length every sample
    # and forces flex-attention to re-JIT-compile every step (>10 min/rank,
    # NCCL watchdog aborts). Pin the RNG per sample so shapes stay stable
    # (this is an overfit sanity run; runtime patch, no source edit).
    import random as _random
    from data.interleave_datasets.edit_dataset import (
        UnifiedEditIterableDataset as _UnifiedEditDS)
    if not getattr(_UnifiedEditDS.parse_row, '_det_patched', False):
        _orig_parse_row = _UnifiedEditDS.parse_row

        def _det_parse_row(self, row):
            _state = _random.getstate()
            _random.seed(1234)
            try:
                return _orig_parse_row(self, row)
            finally:
                _random.setstate(_state)

        _det_parse_row._det_patched = True
        _UnifiedEditDS.parse_row = _det_parse_row
    # Zero CFG condition-dropout probs: pack_sequence randomly drops the
    # vit/vae/text condition with these probs (vit: 0.4!), independently per
    # rank. That changes which modules run under FSDP and desyncs the
    # collective sequence across ranks (NCCL deadlock). Official mixed
    # training tolerates it via the mandatory t2i dataset; pure-edit overfit
    # cannot. Deterministic full-condition batches are fine for overfitting.
    from data.dataset_base import DataConfig as _DataConfig
    _orig_dc_init = _DataConfig.__init__
    def _dc_init_no_cfg_dropout(self, *a, **kw):
        kw.setdefault('text_cond_dropout_prob', 0.0)
        kw.setdefault('vit_cond_dropout_prob', 0.0)
        kw.setdefault('vae_cond_dropout_prob', 0.0)
        _orig_dc_init(self, *a, **kw)
        self.text_cond_dropout_prob = 0.0
        self.vit_cond_dropout_prob = 0.0
        self.vae_cond_dropout_prob = 0.0
    if not getattr(_DataConfig.__init__, '_cfg_patched', False):
        _dc_init_no_cfg_dropout._cfg_patched = True
        _DataConfig.__init__ = _dc_init_no_cfg_dropout
    di['t2i_pretrain']['t2i']['data_dir'] = os.path.join(example_data_dir, 't2i')
    # register a second t2i entry pointing at the same dir so multi-rank
    # runs can shard data_dirs across ranks (official get_parquet_data_paths
    # splits the *dir list*, a single dir leaves rank>0 empty and deadlocks
    # the packing loop). Only used by configs that list 't2i_2'.
    di['t2i_pretrain']['t2i_2'] = {
        'data_dir': os.path.join(example_data_dir, 't2i'),
        'num_files': di['t2i_pretrain']['t2i'].get('num_files', 1),
        'num_total_samples': di['t2i_pretrain']['t2i'].get('num_total_samples', 2),
    }
    di['unified_edit']['seedxedit_multi']['data_dir'] = os.path.join(example_data_dir, 'editing/seedxedit_multi')
    # same rank-sharding workaround as t2i_2 above: a second edit entry so
    # each rank owns at least one data dir. Only used by configs listing it.
    di['unified_edit']['seedxedit_multi_2'] = {
        'data_dir': os.path.join(example_data_dir, 'editing/seedxedit_multi'),
        'num_files': di['unified_edit']['seedxedit_multi'].get('num_files', 1),
        'num_total_samples': di['unified_edit']['seedxedit_multi'].get('num_total_samples', 2),
    }
    # The shipped parquet_info json uses 'your_data_path' placeholder keys; the
    # dataset code matches real file paths against those keys, so rewrite them
    # into a fixed copy (original data files are left untouched).
    src_info = os.path.join(example_data_dir, 'editing/parquet_info/seedxedit_multi.json')
    dst_info = os.path.join(example_data_dir, 'editing/parquet_info/seedxedit_multi_fixed.json')
    if dist.get_rank() == 0 and not os.path.exists(dst_info):
        import json
        with open(src_info, 'r') as f:
            info = json.load(f)
        fixed = {k.replace('your_data_path/bagel_example', example_data_dir): v for k, v in info.items()}
        tmp_info = dst_info + '.tmp'
        with open(tmp_info, 'w') as f:
            json.dump(fixed, f)
        os.rename(tmp_info, dst_info)
    dist.barrier()
    di['unified_edit']['seedxedit_multi']['parquet_info_path'] = dst_info
    di['unified_edit']['seedxedit_multi_2']['parquet_info_path'] = dst_info
    di['vlm_sft']['llava_ov']['data_dir'] = os.path.join(example_data_dir, 'vlm/images')
    di['vlm_sft']['llava_ov']['jsonl_path'] = os.path.join(example_data_dir, 'vlm/llava_ov_si.jsonl')


def save_lora_adapter(fsdp_model, save_path, logger):
    lora_sd = get_lora_state_dict(fsdp_model)
    if dist.get_rank() == 0 and lora_sd:
        from safetensors.torch import save_file
        os.makedirs(save_path, exist_ok=True)
        save_file(lora_sd, os.path.join(save_path, "lora.safetensors"))
        logger.info(f"LoRA adapter ({len(lora_sd)} tensors) saved to {save_path}")
    dist.barrier()


def fsdp_wrapper_lora(original_model, fsdp_config):
    """Same wrap policy as the official fsdp_wrapper, but with use_orig_params=True:
    LoRA freezes all base params, so requires_grad is mixed inside every wrap
    unit, which FSDP only supports when keeping original params."""
    if fsdp_config.sharding_strategy == 'HYBRID_SHARD':
        device_mesh = init_device_mesh(
            "cuda",
            mesh_shape=(fsdp_config.num_replicate, fsdp_config.num_shard),
            mesh_dim_names=("replicate", "shard")
        )
    else:
        device_mesh = None
    return FSDP(
        original_model,
        auto_wrap_policy=functools.partial(
            transformer_auto_wrap_policy,
            transformer_layer_cls={
                Qwen2DecoderLayer,
                Qwen2MoEDecoderLayer,
                Qwen2MoTDecoderLayer,
                SiglipEncoderLayer,
                SiglipVisionTransformer,
                MLPconnector,
                TimestepEmbedder,
                PositionEmbedding,
            },
        ),
        mixed_precision=MixedPrecision(
            param_dtype=torch.bfloat16,
            reduce_dtype=torch.bfloat16,
            buffer_dtype=torch.bfloat16,
        ),
        device_id=dist.get_rank() % torch.cuda.device_count(),
        sharding_strategy=ShardingStrategy[fsdp_config.sharding_strategy],
        backward_prefetch=BackwardPrefetch[fsdp_config.backward_prefetch],
        cpu_offload=CPUOffload(offload_params=fsdp_config.cpu_offload),
        device_mesh=device_mesh,
        use_orig_params=True,
    )


def main():
    assert torch.cuda.is_available()
    # flex-attention JIT-compiles per new sequence shape and compilation can
    # exceed the default 600s NCCL watchdog on the first steps; give it room
    import datetime as _dt
    dist.init_process_group("nccl", timeout=_dt.timedelta(hours=3))
    device = dist.get_rank() % torch.cuda.device_count()
    torch.cuda.set_device(device)
    parser = HfArgumentParser((ModelArguments, DataArguments, TrainingArguments, LoRAArguments))
    model_args, data_args, training_args, lora_args = parser.parse_args_into_dataclasses()
    if training_args.peak_device_tflops <= 0:
        auto_tflops = detect_peak_tflops(training_args.peak_device_tflops)
        if auto_tflops > 0:
            training_args.peak_device_tflops = auto_tflops

    # flash-attn adaptation (runtime monkey-patch, no source edits)
    patch_bagel_flash_attention()

    # Setup logging:
    if dist.get_rank() == 0:
        os.makedirs(training_args.results_dir, exist_ok=True)
        os.makedirs(training_args.checkpoint_dir, exist_ok=True)
        logger = create_logger(training_args.results_dir, dist.get_rank())
        wandb.init(
            project=training_args.wandb_project,
            id=f"{training_args.wandb_name}-run{training_args.wandb_runid}",
            name=training_args.wandb_name,
            resume=training_args.wandb_resume,
            mode="offline" if training_args.wandb_offline else "online",
            settings=wandb.Settings(init_timeout=120)
        )
        wandb.config.update(training_args)
        wandb.config.update(model_args)
        wandb.config.update(data_args)
        wandb.config.update(lora_args)
        logger.info(f"Using peak_device_tflops={training_args.peak_device_tflops:.2f} TFLOPs (per GPU).")
    else:
        logger = create_logger(None, dist.get_rank())
    dist.barrier()
    logger.info(f'Training arguments {training_args}')
    logger.info(f'Model arguments {model_args}')
    logger.info(f'Data arguments {data_args}')
    logger.info(f'LoRA arguments {lora_args}')

    # fill dataset paths at runtime (no source modification)
    patch_dataset_paths(lora_args.example_data_dir)

    # Set seed:
    seed = training_args.global_seed * dist.get_world_size() + dist.get_rank()
    set_seed(seed)

    # Setup model (fine-tune from the official HF-format checkpoint):
    # NOTE: build the model in bf16 directly to keep host-memory peak low
    # (fp32 instantiation of the ~14B model would need ~56GB per rank).
    assert training_args.finetune_from_hf, "LoRA script expects --finetune_from_hf True"
    _prev_default_dtype = torch.get_default_dtype()
    torch.set_default_dtype(torch.bfloat16)
    llm_config = Qwen2Config.from_json_file(os.path.join(model_args.model_path, "llm_config.json"))
    llm_config.layer_module = model_args.layer_module
    llm_config.qk_norm = model_args.llm_qk_norm
    llm_config.tie_word_embeddings = model_args.tie_word_embeddings
    llm_config.freeze_und = training_args.freeze_und
    language_model = Qwen2ForCausalLM(llm_config)
    if training_args.copy_init_moe:
        language_model.init_moe()

    if training_args.visual_und:
        vit_config = SiglipVisionConfig.from_json_file(os.path.join(model_args.model_path, "vit_config.json"))
        vit_config.num_hidden_layers = vit_config.num_hidden_layers + 1 + model_args.vit_select_layer
        vit_config.rope = model_args.vit_rope
        vit_model = SiglipVisionModel(vit_config)

    if training_args.visual_gen:
        vae_model, vae_config = load_ae(
            local_path=os.path.join(model_args.model_path, "ae.safetensors")
        )

    config = BagelConfig(
        visual_gen=training_args.visual_gen,
        visual_und=training_args.visual_und,
        llm_config=llm_config,
        vit_config=vit_config if training_args.visual_und else None,
        vae_config=vae_config if training_args.visual_gen else None,
        latent_patch_size=model_args.latent_patch_size,
        max_latent_size=model_args.max_latent_size,
        vit_max_num_patch_per_side=model_args.vit_max_num_patch_per_side,
        connector_act=model_args.connector_act,
        interpolate_pos=model_args.interpolate_pos,
        timestep_shift=training_args.timestep_shift,
    )
    model = Bagel(
        language_model,
        vit_model if training_args.visual_und else None,
        config
    )

    if training_args.visual_und:
        model.vit_model.vision_model.embeddings.convert_conv2d_to_linear(vit_config)
    torch.set_default_dtype(_prev_default_dtype)

    total_param_count = count_parameters(model)
    lm_param_count = count_parameters(model.language_model)
    logger.info(f"Model parameter count: {total_param_count / 1e9:.2f}B (LM-only: {lm_param_count / 1e9:.2f}B)")

    # Setup tokenizer for model:
    tokenizer = Qwen2Tokenizer.from_pretrained(model_args.model_path)
    tokenizer, new_token_ids, num_new_tokens = add_special_tokens(tokenizer)
    if num_new_tokens > 0:
        model.language_model.resize_token_embeddings(len(tokenizer))
        model.config.llm_config.vocab_size = len(tokenizer)
        model.language_model.config.vocab_size = len(tokenizer)

    # Load pretrained weights: official fine-tuning loads EMA weights; the
    # modelscope mirror only ships ema.safetensors, so fall back gracefully.
    # NOTE: ranks load ONE AT A TIME to keep host-memory peak under the
    # container limit (a 29GB safetensors per rank would OOM the CPU).
    resume_weights = training_args.resume_from or model_args.model_path
    if not os.path.exists(os.path.join(resume_weights, "model.safetensors")) and \
            os.path.exists(os.path.join(resume_weights, "ema.safetensors")):
        for r in range(dist.get_world_size()):
            if dist.get_rank() == r:
                logger.info(f"rank {r}: model.safetensors not found under {resume_weights}; "
                            f"loading EMA weights (ema.safetensors) instead.")
                from safetensors.torch import load_file
                ema_sd = load_file(os.path.join(resume_weights, "ema.safetensors"), device="cpu")
                ema_sd.pop('latent_pos_embed.pos_embed', None)
                ema_sd.pop('vit_pos_embed.pos_embed', None)
                msg = model.load_state_dict(ema_sd, strict=False)
                logger.info(f"EMA weights loaded: {msg}")
                del ema_sd
                gc.collect()
            dist.barrier()
    else:
        model, _ = FSDPCheckpoint.try_load_ckpt(resume_weights, logger, model, None)

    # maybe freeze something:
    if training_args.freeze_vae and training_args.visual_gen:
        for param in vae_model.parameters():
            param.requires_grad = False
    if training_args.freeze_vit and training_args.visual_und:
        model.vit_model.eval()
        for param in model.vit_model.parameters():
            param.requires_grad = False

    # unify dtype: resize_token_embeddings may have created fp32 embeddings while
    # the rest of the model is bf16; FSDP needs uniform dtype per unit
    model.to(torch.bfloat16)

    # Apply LoRA to the LLM attention projections (und + MoT gen branch):
    lora_info = apply_lora(model, r=lora_args.lora_r, alpha=lora_args.lora_alpha, dropout=lora_args.lora_dropout)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    logger.info(f"LoRA applied: {lora_info['replaced_modules']} modules replaced, "
                f"trainable params {trainable / 1e6:.2f}M")

    # Setup FSDP:
    fsdp_config = FSDPConfig(
        sharding_strategy=training_args.sharding_strategy,
        backward_prefetch=training_args.backward_prefetch,
        cpu_offload=training_args.cpu_offload,
        num_replicate=training_args.num_replicate,
        num_shard=training_args.num_shard,
    )
    fsdp_model = fsdp_wrapper_lora(model, fsdp_config)
    apply_activation_checkpointing(
        fsdp_model,
        checkpoint_wrapper_fn=functools.partial(
            checkpoint_wrapper, checkpoint_impl=CheckpointImpl.NO_REENTRANT
        ),
        check_fn=grad_checkpoint_check_fn
    )

    if dist.get_rank() == 0:
        for name, param in model.named_parameters():
            if param.requires_grad:
                print("[trainable]", name)

    # Setup optimizer and scheduler (trainable/LoRA params only):
    optimizer = torch.optim.AdamW(
        [p for p in fsdp_model.parameters() if p.requires_grad],
        lr=training_args.lr,
        betas=(training_args.beta1, training_args.beta2),
        eps=training_args.eps,
        weight_decay=0
    )
    if training_args.lr_scheduler == 'cosine':
        scheduler = get_cosine_with_min_lr_schedule_with_warmup(
            optimizer=optimizer,
            num_warmup_steps=training_args.warmup_steps,
            num_training_steps=training_args.total_steps,
            min_lr=training_args.min_lr,
        )
    elif training_args.lr_scheduler == 'constant':
        scheduler = get_constant_schedule_with_warmup(
            optimizer=optimizer, num_warmup_steps=training_args.warmup_steps
        )
    else:
        raise ValueError

    train_step = 0
    data_status = None

    # Setup packed dataloader
    with open(data_args.dataset_config_file, "r") as stream:
        dataset_meta = yaml.safe_load(stream)
    dataset_config = DataConfig(grouped_datasets=dataset_meta)
    if training_args.visual_und:
        dataset_config.vit_patch_size = model_args.vit_patch_size
        dataset_config.max_num_patch_per_side = model_args.vit_max_num_patch_per_side
    if training_args.visual_gen:
        vae_image_downsample = model_args.latent_patch_size * vae_config.downsample
        dataset_config.vae_image_downsample = vae_image_downsample
        dataset_config.max_latent_size = model_args.max_latent_size
        dataset_config.text_cond_dropout_prob = model_args.text_cond_dropout_prob
        dataset_config.vae_cond_dropout_prob = model_args.vae_cond_dropout_prob
        dataset_config.vit_cond_dropout_prob = model_args.vit_cond_dropout_prob
    # [cfg-patch] the block above re-injects ModelArguments dropout probs
    # (0.1/0.3/0.3) AFTER DataConfig construction, undoing the zeroed
    # values. Per-rank independent condition dropout makes FSDP ranks run
    # different compute graphs -> NCCL deadlock. Force zero again here.
    dataset_config.text_cond_dropout_prob = 0.0
    dataset_config.vae_cond_dropout_prob = 0.0
    dataset_config.vit_cond_dropout_prob = 0.0
    train_dataset = PackedDataset(
        dataset_config,
        tokenizer=tokenizer,
        special_tokens=new_token_ids,
        local_rank=dist.get_rank(),
        world_size=dist.get_world_size(),
        num_workers=data_args.num_workers,
        expected_num_tokens=training_args.expected_num_tokens,
        max_num_tokens_per_sample=data_args.max_num_tokens_per_sample,
        max_num_tokens=data_args.max_num_tokens,
        max_buffer_size=data_args.max_buffer_size,
        prefer_buffer_before=data_args.prefer_buffer_before,
        interpolate_pos=model_args.interpolate_pos,
        use_flex=training_args.use_flex,
        data_status=data_status,
    )
    train_dataset.set_epoch(data_args.data_seed)
    train_loader = DataLoader(
        train_dataset,
        batch_size=1,  # batch size is 1 packed dataset
        num_workers=data_args.num_workers,
        pin_memory=True,
        collate_fn=collate_wrapper(),
        drop_last=True,
        prefetch_factor=data_args.prefetch_factor,
    )

    # Prepare models for training:
    if training_args.visual_gen:
        vae_model.to(device).eval()
    fsdp_model.train()

    # train loop
    start_time = time()
    logger.info(f"Training for {training_args.total_steps} steps, starting at {train_step}...")
    optimizer.zero_grad()
    total_norm = torch.tensor(0.0, device=device)
    token_window = 0.0
    seqlen_square_window = 0.0
    step_times = []
    dense_token_factor, attn_factor = qwen2_flop_coefficients(model.language_model.config)
    for micro_step, data in enumerate(train_loader):
        curr_step = train_step + micro_step // training_args.gradient_accumulation_steps
        if curr_step >= training_args.total_steps:
            logger.info(f"Reached total_steps={training_args.total_steps}, stopping training.")
            break
        step_start = time()
        data = data.cuda(device).to_dict()
        data_indexes = data.pop('batch_data_indexes', None)
        ce_loss_weights = data.pop('ce_loss_weights', None)
        tokens_tensor = torch.tensor(float(data['sequence_length']), device=device)
        dist.all_reduce(tokens_tensor, op=dist.ReduceOp.SUM)
        token_window += tokens_tensor.item()
        # NOTE: collective ops must run on ALL ranks unconditionally (data
        # content may differ across ranks); use zero contributions instead
        sample_lens_tensor = torch.tensor(data['sample_lens'] or [0.0], dtype=torch.float32, device=device)
        sample_square = torch.dot(sample_lens_tensor, sample_lens_tensor)
        dist.all_reduce(sample_square, op=dist.ReduceOp.SUM)
        seqlen_square_window += sample_square.item()

        try:
            with torch.amp.autocast("cuda", enabled=True, dtype=torch.bfloat16):
                if training_args.visual_gen and 'padded_images' in data:
                    # pure-VLM batches carry no generation images (official mixed
                    # config always includes a mandatory t2i dataset, we don't)
                    with torch.no_grad():
                        data['padded_latent'] = vae_model.encode(data.pop('padded_images'))
                loss_dict = fsdp_model(**data)
        except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
            if "out of memory" in str(e).lower():
                logger.error(
                    f"CUDA OOM at step {curr_step}: sample_lens={data.get('sample_lens')}, "
                    f"seq={data['packed_text_ids'].shape}, "
                    f"vit_tokens={None if data.get('packed_vit_tokens') is None else data['packed_vit_tokens'].shape}, "
                    f"allocated={torch.cuda.memory_allocated()/2**30:.1f}GB")
                torch.cuda.empty_cache()
            raise

        loss = 0
        ce = loss_dict["ce"]
        total_ce_tokens = torch.tensor(
            len(data['ce_loss_indexes']) if ce is not None else 0, device=device)
        dist.all_reduce(total_ce_tokens, op=dist.ReduceOp.SUM)
        if ce is not None:
            if training_args.ce_loss_reweighting:
                ce = ce * ce_loss_weights
                total_ce_loss_weights = ce_loss_weights.sum()
                dist.all_reduce(total_ce_loss_weights, op=dist.ReduceOp.SUM)
                ce = ce.sum() * dist.get_world_size() / total_ce_loss_weights
            else:
                ce = ce.sum() * dist.get_world_size() / total_ce_tokens
            loss_dict["ce"] = ce.detach()
            loss = loss + ce * training_args.ce_weight
        else:
            # pure-generation (t2i) batches legitimately have no CE tokens
            loss_dict["ce"] = torch.tensor(0, device=device)
            total_ce_tokens = torch.tensor(0, device=device)

        # pure-VLM batches have no generation tokens -> mse is None; keep the
        # collective unconditional across ranks
        _has_mse = training_args.visual_gen and loss_dict.get("mse") is not None
        total_mse_tokens = torch.tensor(
            len(data['mse_loss_indexes']) if _has_mse else 0, device=device)
        dist.all_reduce(total_mse_tokens, op=dist.ReduceOp.SUM)
        if _has_mse:
            mse = loss_dict["mse"]
            mse = mse.mean(dim=-1).sum() * dist.get_world_size() / total_mse_tokens
            loss_dict["mse"] = mse.detach()
            loss = loss + mse * training_args.mse_weight
        else:
            loss_dict["mse"] = torch.tensor(0, device=device)
            total_mse_tokens = torch.tensor(0, device=device)

        loss = loss / training_args.gradient_accumulation_steps
        loss.backward()

        if (micro_step + 1) % training_args.gradient_accumulation_steps == 0:
            total_norm = fsdp_model.clip_grad_norm_(training_args.max_grad_norm)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()

        torch.cuda.synchronize()
        step_times.append(time() - step_start)

        # Log loss values:
        if curr_step % training_args.log_every == 0:
            total_samples = torch.tensor(len(data['sample_lens']), device=device)
            dist.all_reduce(total_samples, op=dist.ReduceOp.SUM)

            # Measure training speed:
            torch.cuda.synchronize()
            end_time = time()
            elapsed = max(end_time - start_time, 1e-6)
            steps_per_sec = training_args.log_every / elapsed
            tokens_per_sec = token_window / elapsed
            tokens_per_step = token_window / training_args.log_every
            flops_all_token = dense_token_factor * token_window + attn_factor * seqlen_square_window
            actual_tflops = flops_all_token / elapsed / 1e12
            peak_total_tflops = training_args.peak_device_tflops * dist.get_world_size()
            mfu_value = actual_tflops / peak_total_tflops if peak_total_tflops > 0 else 0.0
            recent = step_times[-training_args.log_every:]
            avg_step_time = sum(recent) / max(len(recent), 1)
            message = f"(step={curr_step:07d}) "
            wandb_log = {}
            for key, value in loss_dict.items():
                avg_loss = torch.tensor(value.item(), device=device)
                dist.all_reduce(avg_loss, op=dist.ReduceOp.SUM)
                avg_loss = avg_loss.item() / dist.get_world_size()
                message += f"Train Loss {key}: {avg_loss:.4f}, "
                wandb_log[key] = avg_loss
            message += (f"Train Steps/Sec: {steps_per_sec:.2f}, Avg Sec/Step: {avg_step_time:.3f}, "
                        f"Tokens/Sec: {tokens_per_sec/1000:.2f}k, MFU: {mfu_value*100:.1f}%, ")
            logger.info(message)
            if dist.get_rank() == 0:
                print(message, flush=True)

            wandb_log['lr'] = optimizer.param_groups[0]['lr']
            wandb_log['total_mse_tokens'] = total_mse_tokens.item()
            wandb_log['total_ce_tokens'] = total_ce_tokens.item()
            wandb_log['total_norm'] = total_norm.item()
            wandb_log['total_samples'] = total_samples.item()
            wandb_log['tokens_per_sec'] = tokens_per_sec
            wandb_log['tokens_per_step'] = tokens_per_step
            wandb_log['actual_tflops'] = actual_tflops
            wandb_log['mfu'] = mfu_value
            wandb_log['avg_sec_per_step'] = avg_step_time

            mem_allocated = torch.tensor(torch.cuda.max_memory_allocated() / 1024**2, device=device)
            dist.all_reduce(mem_allocated, op=dist.ReduceOp.MAX)
            wandb_log['mem_allocated'] = mem_allocated
            mem_cache = torch.tensor(torch.cuda.max_memory_reserved() / 1024**2, device=device)
            dist.all_reduce(mem_cache, op=dist.ReduceOp.MAX)
            wandb_log['mem_cache'] = mem_cache

            if dist.get_rank() == 0:
                wandb.log(wandb_log, step=curr_step)
            start_time = time()
            token_window = 0.0
            seqlen_square_window = 0.0

        if data_status is None:
            data_status = {}
        for item in data_indexes:
            if item['dataset_name'] not in data_status.keys():
                data_status[item['dataset_name']] = {}
            data_status[item['dataset_name']][item['worker_id']] = item['data_indexes']

        if lora_args.lora_save_every > 0 and curr_step > 0 and curr_step % lora_args.lora_save_every == 0:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            save_lora_adapter(fsdp_model, os.path.join(training_args.checkpoint_dir, f"{curr_step:07d}"), logger)

    # final summary & adapter save
    if step_times:
        warm = step_times[1:] if len(step_times) > 1 else step_times
        logger.info(
            f"[SPEED] total_steps_run={len(step_times)}, "
            f"avg sec/step (all)={sum(step_times)/len(step_times):.3f}, "
            f"avg sec/step (excl. first)={sum(warm)/len(warm):.3f}, "
            f"min={min(step_times):.3f}, max={max(step_times):.3f}"
        )
    logger.info("Saving final LoRA adapter...")
    save_lora_adapter(fsdp_model, os.path.join(training_args.checkpoint_dir, "final"), logger)

    logger.info("Done!")
    if dist.get_rank() == 0:
        wandb.finish()
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
