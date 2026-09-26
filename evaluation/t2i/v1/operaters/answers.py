"""Image-model answer jobs and the gateway generation actor (map_async).

The transport is ported from eval_t2i_gen.py: chat-endpoint image models with
automatic images-endpoint fallback. Scheduling, resume and call records are
the platform's; this actor only performs one job per call. Default offline
mode never touches the gateway.
"""
import asyncio
import base64
import io
import re
import time
from pathlib import Path

from demiflow.execution.artifacts import digest
from preparation.operaters.runfiles import run_records, store_blob
from evaluation.t2i.v1.operaters.runfiles import short_name

IMG_ENDPOINT_HINT = re.compile(r"images? (?:endpoint|api)|/images", re.IGNORECASE)
LIST_CONTENT_HINT = re.compile(r"valid list|content.*(list|array)", re.IGNORECASE)




class BuildAnswerJobs:
    """One generation job per question x image model (or imported responses)."""

    def __init__(self, config, imported=None):
        self.config = config
        self.imported = imported

    def __call__(self, row):
        if row['status'] != 'question_ready':
            yield row
            return
        if self.imported is not None:
            match = next((r for r in self.imported['rows'] if str(r.get('qid')) == row['qid']), None)
            if match is None:
                yield {**row, 'status': 'missing_response', 'fail_reason': 'imported responses have no row for this qid'}
                return
            if not match.get('ok') or not match.get('image'):
                yield {**row, 'status': 'model_failure', 'fail_reason': str(match.get('error') or 'response marked not ok'),
                       'response_record': match}
                return
            if match.get('_image_blob_ref'):
                from demiflow.lance.blobs import BlobRef
                from project import resolve_root
                ref=BlobRef(**match['_image_blob_ref'])
                data=ref.read(resolve_root())
                yield {**row,'status':'generated', 'image_model':'imported:'+(match.get('model') or 'history'),
                       'image':{'blob_ref':ref.to_dict(),'sha256':ref.sha256},
                       'seconds':match.get('seconds'),'mode':'imported'}
                return
            image = Path(match['image'])
            if not image.is_absolute():
                image = Path(self.imported['source']['path']).parent / image
            if not image.is_file():
                yield {**row, 'status': 'missing_image', 'fail_reason': f'imported image missing: {image}'}
                return
            data = image.read_bytes()
            sha = digest(data)
            _ingest_answer_image(data, sha, image.suffix)
            yield {**row, 'status': 'generated', 'image_model': 'imported:' + (match.get('model') or 'history'),
                   'image': {'path': str(image), 'sha256': sha},
                   'seconds': match.get('seconds'), 'mode': 'imported'}
            return
        for model in self.config['models']:
            job_id = row['qid'] + '__' + short_name(model)
            yield {**row, 'task_id': job_id, 'job_id': job_id, 'model': model,
                   'status': 'pending_generation',
                   'request': {'model': model, 'prompt': row['gen_prompt'],
                               'mode': self.config['gen_mode']}}


def _ingest_answer_image(data: bytes, sha: str, suffix: str):
    """Explicit lake import for historical answer images: SHA identity, path stays provenance."""
    from project import resolve_root
    from collect.material_writer import write_images
    write_images(resolve_root(), [{'sha256': sha, 'ext': suffix.lstrip('.').lower() or 'bin',
                                   'byte_size': len(data), 'storage_mode': 'lance_blob', 'data': data,
                                   'concepts': [], 'sources': [], 'availability': 'available',
                                   'resolution': None}])


def _extract_b64(u: str) -> bytes:
    if u.startswith("data:"):
        return base64.b64decode(u.split(",", 1)[1])
    return base64.b64decode(u)


def _fetch_url(session, u: str, timeout: int = 120) -> bytes:
    response = session.get(u, timeout=timeout)
    response.raise_for_status()
    return response.content


def gen_one(session, endpoint, model, prompt, mode, timeout):
    """Ported from eval_t2i_gen.gen_one: returns (bytes | None, mode_used, err)."""
    url_chat = f"{endpoint}/chat/completions"
    url_img = f"{endpoint}/images/generations"

    def _chat(list_content=False):
        content = [{"type": "text", "text": prompt}] if list_content else prompt
        return session.post(url_chat, json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
        }, timeout=timeout)

    def _images():
        return session.post(url_img, json={"model": model, "prompt": prompt, "n": 1}, timeout=timeout)

    def _imgs_from_chat(obj):
        msg = (obj.get("choices") or [{}])[0].get("message") or {}
        urls = []
        for im in msg.get("images") or []:
            u = (im.get("image_url") or {}).get("url") if isinstance(im.get("image_url"), dict) else im.get("image_url")
            if u:
                urls.append(u)
        for part in (msg.get("content") if isinstance(msg.get("content"), list) else []) or []:
            u = ((part.get("image_url") or {}).get("url")
                 if isinstance(part.get("image_url"), dict) else part.get("image_url"))
            if u:
                urls.append(u)
        return urls

    def _imgs_from_images(obj):
        out = []
        for d in obj.get("data") or []:
            if d.get("b64_json"):
                out.append(("b64", d["b64_json"]))
            elif d.get("url"):
                out.append(("url", d["url"]))
        return out

    if mode in ("chat", "auto"):
        r = _chat()
        if r.ok:
            urls = _imgs_from_chat(r.json())
            if urls:
                data = _extract_b64(urls[0]) if urls[0].startswith("data:") else _fetch_url(session, urls[0])
                return data, "chat", ""
            return None, "chat", "chat 回包无图像字段: " + r.text[:300]
        hint = r.text[:400]
        if mode == "chat":
            return None, "chat", hint
        if not (IMG_ENDPOINT_HINT.search(hint) or LIST_CONTENT_HINT.search(hint)):
            r4 = _images()
            if r4.ok:
                got = _imgs_from_images(r4.json())
                if got:
                    kind, v = got[0]
                    data = base64.b64decode(v) if kind == "b64" else _fetch_url(session, v)
                    return data, "images", ""
            return None, "chat", "chat 失败: " + hint + " | images 回退: " + (r4.text[:200] if not r4.ok else "回包无 data")
        if IMG_ENDPOINT_HINT.search(hint):
            r2 = _images()
            if r2.ok:
                got = _imgs_from_images(r2.json())
                if got:
                    kind, v = got[0]
                    data = base64.b64decode(v) if kind == "b64" else _fetch_url(session, v)
                    return data, "images", ""
                return None, "images", "images 回包无 data: " + r2.text[:300]
            return None, "images", r2.text[:400]
        if LIST_CONTENT_HINT.search(hint):
            r3 = _chat(list_content=True)
            if r3.ok:
                urls = _imgs_from_chat(r3.json())
                if urls:
                    data = _extract_b64(urls[0]) if urls[0].startswith("data:") else _fetch_url(session, urls[0])
                    return data, "chat", ""
                return None, "chat", "chat(list) 回包无图像字段: " + r3.text[:300]
            return None, "chat", r3.text[:400]
        return None, "chat", hint
    r = _images()
    if r.ok:
        got = _imgs_from_images(r.json())
        if got:
            kind, v = got[0]
            data = base64.b64decode(v) if kind == "b64" else _fetch_url(session, v)
            return data, "images", ""
        return None, "images", "images 回包无 data: " + r.text[:300]
    return None, "images", r.text[:400]


class GenerateT2I:
    """One gateway call per job; resumable, no silent retries of uncertain calls."""

    concurrency = 1
    queue_depth = 1

    def __init__(self, run, config):
        self.run, self.config = run, config

    async def __call__(self, job):
        return await asyncio.to_thread(self.generate_one, job)

    def generate_one(self, job):
        from PIL import Image
        records = run_records(self.run)
        key = 'generation/' + job['job_id']
        result = {k: v for k, v in job.items() if k != 'request'}
        result['request_sha256'] = digest(job)
        previous = records.get(key + '/result')
        if previous is not None:
            if previous['request_sha256'] != result['request_sha256']:
                raise ValueError('Existing result belongs to another request')
            return previous
        if self.config['mode'] != 'online':
            result.update(status='pending_generation',
                          reason='offline mode: bind imported responses or rerun with --mode online')
            records.put(key + '/result', result)
            return result
        if records.get(key + '/attempt') is not None:
            # 一次付费调用的结果未知时不静默重试（与评测 adapters 同约定）。
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
                data, used, err = gen_one(session, self.config['endpoint'], job['model'],
                                          job['request']['prompt'], job['request']['mode'],
                                          self.config['timeout'])
            if data is not None:
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    buffer = io.BytesIO()
                    image.save(buffer, format='PNG')
                ref = store_blob(self.run, buffer.getvalue())
                result.update(status='generated', mode=used,
                              image={'sha256': ref.sha256, 'blob_ref': ref.to_dict(), 'path': None},
                              seconds=round(time.time() - started, 1))
            else:
                result.update(error=err[:500], seconds=round(time.time() - started, 1))
        except Exception as error:  # noqa: BLE001 - 单作业失败成为行的状态，不断链
            result['error'] = f'{type(error).__name__}: {str(error)[:400]}'
            result['seconds'] = round(time.time() - started, 1)
        records.put(key + '/result', result)
        return result
