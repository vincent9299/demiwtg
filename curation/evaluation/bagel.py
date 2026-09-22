"""Native BAGEL loader migrated from the historical runner; no archive imports.

Preserves the tested fp32 VAE/bf16 projection fixes and output-only geometry.
"""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
BAGEL_ROOT = ROOT / "bagel/Bagel"
CONFIG = dict(image_shapes=(1024, 1024), num_timesteps=50,
              cfg_text_scale=4.0, cfg_img_scale=1.5, cfg_interval=[0.4, 1.0],
              timestep_shift=3.0, cfg_renorm_min=0.0, cfg_renorm_type="global",
              do_sample=False, think=False, understanding_output=False,
              max_think_token_n=1000, text_temperature=0.3, enable_taylorseer=False)


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
