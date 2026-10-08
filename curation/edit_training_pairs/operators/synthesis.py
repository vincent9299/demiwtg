"""One Edit plan -> one generated counterpart with measured local inference."""

import hashlib
import io
import time

from PIL import Image
from preparation.articles.operators.inputs import pixels


def synthesize(row, *, model, config, load_seconds=0.0):
    if row['status'] != 'designed':
        return row
    import torch

    images = [Image.open(io.BytesIO(pixels(row['existing'])[0])).convert('RGB')]
    prompt = row['synthesis_instruction']
    for material in row['synthesis_materials_selected']:
        if material['kind'] == 'text':
            prompt += '\n' + material['text']
        else:
            images.append(Image.open(io.BytesIO(pixels(material['asset'])[0])).convert('RGB'))
    started = time.perf_counter()
    try:
        image = model(
            prompt=prompt,
            image=images,
            num_inference_steps=config['steps'],
            output_resolution=config['resolution'],
            generator=torch.Generator('cpu').manual_seed(config['seed']),
        ).images[0]
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        raw = buffer.getvalue()
        return {
            **row,
            'status': 'synthesized',
            'generated_bytes': raw,
            'generated_sha256': hashlib.sha256(raw).hexdigest(),
            'synthesis_seconds': time.perf_counter() - started,
            'model_load_seconds': load_seconds,
            'synthesis_config': dict(config),
            'synthesis_prompt': prompt,
            'synthesis_image_sha256': [row['existing']['sha256']]
            + [m['asset']['sha256'] for m in row['synthesis_materials_selected'] if m['kind'] == 'image'],
        }
    except Exception as error:
        return {
            **row,
            'status': 'synthesis_failed',
            'reason': f'{type(error).__name__}: {error}',
            'synthesis_seconds': time.perf_counter() - started,
            'model_load_seconds': load_seconds,
            'synthesis_config': dict(config),
            'synthesis_prompt': prompt,
        }


class GeneratePair:
    """合成一题的对应图片并保存 Blob；共享本进程模型，不读取或遍历题表。"""

    concurrency = 1
    label = 'generate_edit_pair'

    def __init__(self, *, model, config, root, load_seconds=0.0):
        self.model, self.config = model, config
        self.root = root
        self.load_seconds = load_seconds

    def __call__(self, row):
        from demiflow.objects import LocalObjectStore
        from curation.edit_training_pairs.operators.prompting import bind_pair

        row = synthesize(row, model=self.model, config=self.config, load_seconds=self.load_seconds)
        if row['status'] != 'synthesized':
            return row
        # 图片存成可独立读取的对象；训练表只保留 URI 和内容 SHA。
        blob = LocalObjectStore(self.root / 'objects').put(
            row.pop('generated_bytes'), sha256=row['generated_sha256'])
        row['generated'] = {
            'sha256': blob.sha256,
            'object_ref': blob.to_dict(),
            'source': {'model': self.config['model_path']},
            'origin': 'generated',
        }
        return bind_pair(row)
