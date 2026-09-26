"""每题生成一张图；本地使用 Diffusers/BAGEL，远端使用明确配置的 API。"""
import asyncio
import base64
import io
import json
import os
import time

from PIL import Image
from demiflow.execution.artifacts import digest
from demiflow.lance.blobs import BlobRef
from project import resolve_root


class GenerateImage:
    """由 map_async 串行调度；复用已落表答案，不切换服务或自动换模型。"""

    def __init__(self, config, records, blobs, offload_dir):
        self.config, self.records, self.blobs = config, records, blobs
        self.offload_dir = offload_dir
        self.model = None

    async def __call__(self, row):
        """在线程内完成单题推理，返回答案及固定版本的图片 Blob 引用。"""
        return await asyncio.to_thread(self.generate, row)

    def generate(self, row):
        """保留生成失败原因；没有图像的题目不会进入 judge。"""
        # 同题的不同模型拥有独立请求与答案记录；输出仍保留原 task_id 和完整模型名。
        key = 'answer/' + digest([self.config['model'], self.config.get('use_references', False), row['task_id']])
        if previous := self.records.get(key):
            return previous
        result = {**{k: row.get(k) for k in ('task_id', 'concept', 'instruction')},
                  'answer_model': self.config['model'] + ('+参考信息' if self.config.get('use_references') else ''),
                  'status': 'generation_failed',
                  'reason': '', 'image_json': None, 'generation_seconds': None}
        # 已发送但没有结果的请求可能消耗过资源；同名续跑不再次提交。
        if self.records.get(key + '/request') is not None:
            result['reason'] = '上次生成已开始但没有保存结果；请检查调用记录。'
            self.records.put(key, result)
            return result
        if self.config['backend'] != 'modelhub' and self.model is None:
            self.load_model()
        self.records.put(key + '/request', {'instruction': row['instruction'], 'config': self.config,
                         'references_json': row.get('references_json') if self.config.get('use_references') else None})
        started = time.monotonic()
        try:
            if self.config.get('use_references'):
                # 只读出题表冻结的作答材料，不引入考点、判据或重新检索。
                prompt, images = self.reference_inputs(row)
                raw = self.local(prompt, images)
            else:
                raw = (self.remote(row['instruction'], key) if self.config['backend'] == 'modelhub'
                       else self.local(row['instruction']))
            with Image.open(io.BytesIO(raw)) as image:
                image.load()  # 确认 API 返回的是可解码图像，再交给 judge。
            ref = self.blobs.put(raw)
            result.update(status='generated', image_json=json.dumps(ref.to_dict()))
        except Exception as error:
            result['reason'] = f'{type(error).__name__}: {error}'
        result['generation_seconds'] = time.monotonic() - started
        self.records.put(key, result)
        return result

    def load_model(self):
        """只加载配置指定的本地权重；BAGEL 沿用已有适配器。"""
        import torch
        if self.config['backend'] == 'bagel':
            from evaluation.bagel.adapter import load_model
            self.model, _ = load_model(self.config['model_path'], self.offload_dir)
        else:
            from diffusers import DiffusionPipeline
            self.model = DiffusionPipeline.from_pretrained(
                self.config['model_path'], torch_dtype=torch.bfloat16,
                local_files_only=True).to(self.config['device'])

    def reference_inputs(self, row):
        """保留材料编号和文字，按顺序读取固定版本的参考图。"""
        references = json.loads(row['references_json'])
        parts, images = [], []
        for ref in references:
            if ref['kind'] == 'text':
                parts.append(f"材料 {ref['number']}：{ref.get('title', '')}\n{ref['text']}")
            elif ref['kind'] == 'image':
                with Image.open(io.BytesIO(BlobRef(**ref['blob_ref']).read(resolve_root()))) as image:
                    images.append(image.convert('RGB'))
                parts.append(f"材料 {ref['number']}（参考图 {len(images)}）：{ref.get('support', '')}\n{ref.get('limitations', '')}")
            else:
                raise ValueError('Unknown reference kind: ' + str(ref['kind']))
        return '参考信息：\n' + '\n\n'.join(parts) + '\n\n题目：\n' + row['instruction'], images

    def local(self, instruction, reference_images=None):
        """将图文参考送入模型的原生接口；采样参数与种子来自显式配置。"""
        import torch
        if self.config['backend'] == 'bagel':
            from evaluation.bagel.adapter import CONFIG
            torch.manual_seed(self.config['seed'])
            torch.cuda.manual_seed_all(self.config['seed'])
            output = self.model.interleave_inference([*(reference_images or []), instruction],
                                                      **{**CONFIG, **self.config['parameters']})
            images = [item for item in output if isinstance(item, Image.Image)]
        else:
            inputs = {'image': reference_images} if reference_images else {}
            images = self.model(prompt=instruction, **inputs, **self.config['parameters'],
                                generator=torch.Generator('cpu').manual_seed(self.config['seed'])).images
        if len(images) != 1:
            raise ValueError('答题模型须返回一张图像')
        buffer = io.BytesIO()
        images[0].save(buffer, format='PNG')
        return buffer.getvalue()

    def remote(self, instruction, key):
        """只调用配置指定的接口一次；记录原始响应，不自动回退到其他接口。"""
        import requests
        cfg = self.config
        if cfg['api'] == 'images':
            endpoint = '/images/generations'
            payload = {'model': cfg['model'], 'prompt': instruction, **cfg['parameters'], 'n': 1}
        else:
            endpoint = '/chat/completions'
            payload = {'model': cfg['model'], 'messages': [{'role': 'user', 'content': instruction}],
                       **cfg['parameters']}
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(cfg['base_url'].rstrip('/') + endpoint, json=payload,
                                    headers={'Authorization': 'Bearer ' + os.environ.get(cfg['api_key_env'], 'anything')},
                                    timeout=cfg['timeout_s'])
            self.records.put(key + '/response', {'status_code': response.status_code, 'body': response.text})
            response.raise_for_status()
            body = response.json()
            if cfg['api'] == 'images':
                images = body.get('data', [])
                if len(images) != 1:
                    raise ValueError('图像 API 须返回一张图像')
                if images[0].get('b64_json'):
                    return base64.b64decode(images[0]['b64_json'], validate=True)
                url = images[0]['url']
            else:
                message = body['choices'][0]['message']
                parts = (message.get('images') or []) + (message['content'] if isinstance(message.get('content'), list) else [])
                urls = []
                for part in parts:
                    value = part.get('image_url')
                    value = value.get('url') if isinstance(value, dict) else value
                    if value and value not in urls:
                        urls.append(value)
                if len(urls) != 1:
                    raise ValueError('Chat API 须返回一张图像')
                url = urls[0]
            if url.startswith('data:image/'):
                return base64.b64decode(url.split(',', 1)[1], validate=True)
            # 下载生成结果不携带网关认证头。
            image = session.get(url, timeout=cfg['timeout_s'])
            image.raise_for_status()
            return image.content

    async def aclose(self):
        """生成结束后释放本地模型，避免占用后续 judge 所需的显存。"""
        if self.model is not None:
            import gc
            import torch
            self.model = None
            gc.collect()
            torch.cuda.empty_cache()
