"""Edit answer jobs and the chat image-edit generation actor (map_async).

Ported from eval_edit_gen.py: the model receives only the before image and
edit_instruction (never reasoning/evidence/level); inline data-URL responses
only; aspect ratio requested from the source image. Default offline mode
never calls the endpoint.
"""
import asyncio
import base64
import hashlib
import io
import json
import re
import time
from pathlib import Path

from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import run_records, store_blob
from evaluation.edit.v1.operaters.runfiles import resolve_source, QID_RE

ASPECTS = {"1:1": 1.0, "3:4": 3 / 4, "4:3": 4 / 3, "9:16": 9 / 16,
           "16:9": 16 / 9, "2:3": 2 / 3, "3:2": 3 / 2}
RUNNER_SCHEMA = "edit-gen-v1"




def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def data_url(path: Path) -> tuple[str, bytes]:
    """Ported from eval_edit_gen.data_url."""
    from PIL import Image
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        image_format = str(im.format or "").upper()
        im.verify()
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image_format)
    if not mime:
        raise ValueError(f"不支持的源图格式：{image_format or 'unknown'}")
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), raw


def closest_aspect(raw: bytes) -> str:
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        ratio = im.width / im.height
    return min(ASPECTS, key=lambda key: abs(ASPECTS[key] - ratio))


def build_request(model, source_raw: bytes, source_url: str, instruction: str):
    """Ported from eval_edit_gen.prepare_request（图片在前、指令在后）。"""
    aspect = closest_aspect(source_raw)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": source_url}},
            {"type": "text", "text": instruction},
        ]}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": aspect},
    }
    fingerprint_payload = {
        "runner_schema": RUNNER_SCHEMA,
        "model": model,
        "source_sha256": _sha256(source_raw),
        "instruction_sha256": _sha256(instruction.encode("utf-8")),
        "content_order": ["image_url", "text"],
        "modalities": payload["modalities"],
        "image_config": payload["image_config"],
    }
    fingerprint = _sha256(json.dumps(fingerprint_payload, ensure_ascii=False,
                                     sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return payload, fingerprint


def image_urls(message: dict) -> list:
    """Ported from eval_edit_gen.image_urls."""
    urls = []
    for image in message.get("images") or []:
        field = image.get("image_url") if isinstance(image, dict) else None
        url = field.get("url") if isinstance(field, dict) else field
        if url:
            urls.append(url)
    content = message.get("content")
    for part in content if isinstance(content, list) else []:
        field = part.get("image_url") if isinstance(part, dict) else None
        url = field.get("url") if isinstance(field, dict) else field
        if url:
            urls.append(url)
    return urls


def decode_image(url: str) -> bytes:
    """只接受内联图片（历史约定：不跟随模型返回的任意 URL）."""
    if not url.startswith("data:image/") or ";base64," not in url:
        raise ValueError("回包图片必须是 data:image/...;base64 内联数据")
    return base64.b64decode(url.split(",", 1)[1], validate=True)


def call_one(session, endpoint, payload):
    """One HTTP call; retry/backoff policy belongs to the caller."""
    resp = session.post(endpoint, json=payload, timeout=600)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    obj = resp.json()
    msg = (obj.get("choices") or [{}])[0].get("message") or {}
    urls = image_urls(msg)
    if not urls:
        raise RuntimeError(f"回包无 image：{resp.text[:500]}")
    data = decode_image(urls[0])
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        width, height, image_format = im.width, im.height, str(im.format or "").upper()
        im.verify()
    if image_format not in {"PNG", "JPEG", "WEBP"}:
        raise ValueError(f"不支持的结果图格式：{image_format or 'unknown'}")
    return data, {"response_model": obj.get("model"), "width": width,
                  "height": height, "format": image_format, "usage": obj.get("usage") or {}}


class BuildEditJobs:
    """Resolve the before image per question and prepare generation jobs."""

    def __init__(self, config, imported=None):
        self.config = config
        self.imported = imported

    def __call__(self, row):
        if row['status'] != 'question_ready':
            yield row
            return
        try:
            binding=row['question'].get('_source_blob_ref')
            if binding:
                from demiflow.lance.blobs import BlobRef
                from project import resolve_root
                source_raw=BlobRef(**binding).read(resolve_root())
                from PIL import Image
                with Image.open(io.BytesIO(source_raw)) as im:
                    mime=Image.MIME.get(im.format, 'image/png')
                source_url='data:'+mime+';base64,'+base64.b64encode(source_raw).decode()
                source={'blob_ref':binding,'sha256':_sha256(source_raw)}
            else:
                source_path=resolve_source(row['question'])
                source_url,source_raw=data_url(source_path)
                _ingest_image(source_raw, _sha256(source_raw), source_path.suffix)
                source={'path':str(source_path),'sha256':_sha256(source_raw)}
        except (FileNotFoundError, ValueError, OSError) as error:
            yield {**row,'status':'missing_source','fail_reason':str(error)}
            return
        if self.imported is not None:
            match = next((r for r in self.imported['rows'] if str(r.get('qid')) == row['qid']), None)
            if match is None:
                yield {**row, 'status': 'missing_response', 'fail_reason': 'imported responses lack this qid'}
                return
            if not match.get('ok') or not match.get('image'):
                yield {**row, 'status': 'model_failure',
                       'fail_reason': str(match.get('error') or 'response marked not ok')}
                return
            if match.get('_image_blob_ref'):
                from demiflow.lance.blobs import BlobRef
                from project import resolve_root
                ref=BlobRef(**match['_image_blob_ref'])
                data=ref.read(resolve_root())
                yield {**row,'status':'generated', 'image_model':'imported:'+(match.get('model') or 'history'),
                       'image':{'blob_ref':ref.to_dict(),'sha256':ref.sha256},
                       'source_image':source,
                       'seconds':match.get('seconds'),'mode':'imported'}
                return
            image = Path(match['image'])
            if not image.is_absolute():
                image = Path(self.imported['source']['path']).parent / image
            if not image.is_file():
                yield {**row, 'status': 'missing_image', 'fail_reason': f'imported image missing: {image}'}
                return
            data = image.read_bytes()
            _ingest_image(data, digest(data), image.suffix)
            yield {**row, 'status': 'generated', 'source_image': source,
                   'image': {'path': str(image), 'sha256': digest(data)},
                   'image_model': 'imported:' + (match.get('model') or 'history')}
            return
        payload, fingerprint = build_request(self.config['model'], source_raw, source_url,
                                             row['edit_instruction'])
        yield {**row, 'status': 'pending_generation', 'source_image': source,
               'job_id': row['qid'], 'request': {'payload': payload, 'fingerprint': fingerprint}}


def _ingest_image(data: bytes, sha: str, suffix: str):
    from project import resolve_root
    from collect.material_writer import write_images
    write_images(resolve_root(), [{'sha256': sha, 'ext': suffix.lstrip('.').lower() or 'bin',
                                   'byte_size': len(data), 'storage_mode': 'lance_blob', 'data': data,
                                   'concepts': [], 'sources': [], 'availability': 'available',
                                   'resolution': None}])


class GenerateEdit:
    """One gateway call per job; resumable, uncertain calls are never retried silently."""

    concurrency = 1
    queue_depth = 1

    def __init__(self, run, config):
        self.run, self.config = run, config

    async def __call__(self, job):
        return await asyncio.to_thread(self.generate_one, job)

    def generate_one(self, job):
        records = run_records(self.run)
        key = 'generation/' + job['job_id']
        result = {k: v for k, v in job.items() if k != 'request'}
        result['request_sha256'] = digest(job['request'])
        previous = records.get(key + '/result')
        if previous is not None:
            if previous['request_sha256'] != result['request_sha256']:
                raise ValueError('Existing result belongs to another request')
            return previous
        if self.config['mode'] != 'online':
            result.update(status='pending_generation',
                          reason='offline mode: import historical responses or rerun with --mode online')
            records.put(key + '/result', result)
            return result
        if records.get(key + '/attempt') is not None:
            result.update(status='interrupted',
                          reason='Prior attempt has unknown outcome; inspect records, retry in a new run')
            records.put(key + '/result', result)
            return result
        import requests
        started = time.time()
        records.put(key + '/attempt', {'job': {k: v for k, v in job.items() if k != 'request'},
                                       'endpoint': self.config['endpoint'], 'started_unix': started})
        result['status'] = 'model_failure'
        try:
            with requests.Session() as session:
                session.trust_env = False
                data, meta = call_one(session, self.config['endpoint'], job['request']['payload'])
            ref = store_blob(self.run, data)
            result.update(status='generated', image={'sha256': ref.sha256, 'blob_ref': ref.to_dict(),
                                                     'path': None},
                          response_meta=meta, seconds=round(time.time() - started, 1))
        except Exception as error:  # noqa: BLE001 - 单作业失败成为行的状态
            result['error'] = f'{type(error).__name__}: {str(error)[:400]}'
            result['seconds'] = round(time.time() - started, 1)
        records.put(key + '/result', result)
        return result
