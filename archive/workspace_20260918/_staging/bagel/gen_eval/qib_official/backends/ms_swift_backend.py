"""
ms-swift PtEngine inference engine.

Mirrors the swift CLI command used to produce this dataset's *_response_* fields:

    swift infer --infer_backend pt --max_batch_size 24 --seed 42 \
                --temperature 0 --top_k 1 --top_p 1 \
                --repetition_penalty 1.05 --max_new_tokens 4096 \
                --enable_thinking true

Requires ms-swift>=4.0.0.
"""

from swift import TransformersEngine, RequestConfig, InferRequest


class MsSwiftJudge:
    def __init__(self, model_path, max_batch_size=24, max_new_tokens=4096):
        # Official pt engine, weights sharded across both GPUs via explicit
        # max_memory (swift's default auto map offloads to CPU when other
        # tenants hold ~23G per card, and a single 58G-free card OOMs with the
        # 55G weights + batch-24 activations; two cards give enough headroom
        # with zero CPU offload).
        self.engine = TransformersEngine(
            model_path,
            max_batch_size=max_batch_size,
            device_map="auto",
            model_kwargs={
                "max_memory": {0: "54GiB", 1: "54GiB"},
            },
        )
        self.request_config = RequestConfig(
            max_tokens=max_new_tokens,
            temperature=0,
            top_k=1,
            top_p=1.0,
            repetition_penalty=1.05,
            seed=42,
        )

        # Enable Qwen3 thinking mode on the engine's default template.
        # ms-swift 4.x exposes this via Template.template_meta.template_kwargs.
        try:
            self.engine.default_template.template_meta.template_kwargs = {
                "enable_thinking": True
            }
        except AttributeError:
            pass  # fall back to per-request template_inputs below

    def generate_batch(self, items):
        """
        Batch inference for multiple items.
        Each item: {"system_prompt": str, "user_text": str, "image": PIL.Image}
        Returns list of generated text strings.
        """
        infer_requests = []
        for item in items:
            messages = [
                {"role": "system", "content": item["system_prompt"]},
                {"role": "user", "content": item["user_text"]},
            ]
            infer_requests.append(
                InferRequest(messages=messages, images=[item["image"]])
            )

        resp_list = self.engine.infer(
            infer_requests,
            self.request_config,
        )

        return [r.choices[0].message.content for r in resp_list]
