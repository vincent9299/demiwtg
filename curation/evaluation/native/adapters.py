"""One generation call per demiflow item; no job loop or private scheduler."""
import asyncio
import io
import os
from pathlib import Path
import time
import traceback

from curation.preparation.records import ROOT, digest, immutable, read, file_record, run_records, store_blob
from curation.evaluation.contracts import verify_request
from curation.evaluation.models import (
    GEMINI_CONFIG, load_native, environment_record, parse_gemini, unpack,
)
from curation.evaluation.native.operators import verify_result


class GenerateImage:
    concurrency = 1
    queue_depth = 1

    def __init__(self, run, backend, config):
        self.run, self.backend, self.config = Path(run), backend, config
        self.native = None

    async def __call__(self, job):
        return await asyncio.to_thread(self.generate_one, job)

    def generate_one(self, job):
        from PIL import Image
        if job['backend'] != self.backend:
            raise ValueError('Wrong backend partition')
        verify_request(job)
        result = {k: v for k, v in job.items() if k != 'request'}
        result['request_sha256'] = digest(job)
        directory = self.run / 'generations' / job['job_id']
        records = run_records(self.run)
        key = 'generation/' + job['job_id']
        previous = records.get(key + '/result')
        if previous is not None:
            if previous['request_sha256'] != digest(job):
                raise ValueError('Existing result belongs to another request')
            return verify_result(previous)
        if job['status'] != 'pending':
            records.put(key + '/result', result)
            return result
        if records.get(key + '/attempt') is not None:
            # A timeout/crash may already have consumed a paid call. No silent retry.
            result.update(status='interrupted', reason='Prior attempt has unknown outcome; inspect artifacts, retry in a new run')
            records.put(key + '/result', result)
            return result
        if self.backend != 'gemini' and self.native is None:
            records.put('environment/' + self.backend, environment_record(self.backend))
            # Initialization failure is outside the attempt: repair environment and resume.
            self.native = load_native(self.backend, self.run / 'backends' / self.backend)
        cfg = GEMINI_CONFIG if self.backend == 'gemini' else self.native[1]
        started = time.time()
        records.put(key + '/attempt', {'job': job, 'config': cfg, 'started_unix': started,
                  'endpoint': self.config['endpoint'] if self.backend == 'gemini' else None})
        result['status'] = 'infra_failure'
        try:
            if self.backend == 'gemini':
                data = self.gemini(job, directory, result)
            else:
                data = self.local(job, directory, result)
            if data is not None:
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    width, height = im.size
                    # Normalize container to PNG; keep original remote response separately.
                    buffer = io.BytesIO()
                    im.save(buffer, format='PNG')
                result['repeats_input'] = digest(data) in {r['sha256'] for r in job['request']['image_roles']}
                # An unchanged edit is still a visible candidate. Let the judge
                # assess completion/preservation; do not discard its pixels.
                ref = store_blob(self.run, buffer.getvalue())
                result.update(status='generated', image={'sha256': ref.sha256, 'blob_ref': ref.to_dict(), 'path': None}, width=width, height=height)
        except Exception:
            result['error'] = traceback.format_exc()
            if self.native:
                self.native[2].cuda.empty_cache()
        result['seconds'] = round(time.time() - started, 3)
        records.put(key + '/result', result)
        return result

    def gemini(self, job, directory, result):
        import requests
        payload = {'model': job['model'], 'messages': job['request']['messages'], **GEMINI_CONFIG}
        run_records(self.run).put('generation/' + job['job_id'] + '/http_request', payload)
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(self.config['endpoint'], json=payload,
                                    timeout=(15, 600), allow_redirects=False)
        body_ref = store_blob(self.run, response.content)
        run_records(self.run).put('generation/' + job['job_id'] + '/http_body', body_ref.to_dict())
        response.raise_for_status()
        raw = response.json()
        run_records(self.run).put('generation/' + job['job_id'] + '/http_response', raw)
        result.update(usage=raw.get('usage'), response_id=raw.get('id'), response_model=raw.get('model'))
        try:
            return parse_gemini(raw)
        except (KeyError, ValueError) as error:
            result.update(status='model_failure', reason=str(error))
            return None

    def local(self, job, directory, result):
        from PIL import Image
        model, cfg, torch = self.native
        interleaved, prompt, images = unpack(job)
        torch.manual_seed(job['seed'])
        torch.cuda.manual_seed_all(job['seed'])
        if self.backend == 'bagel':
            output = model.interleave_inference(interleaved, **cfg)
            generated = [x for x in output if isinstance(x, Image.Image)]
            result['output_text'] = [x for x in output if isinstance(x, str)]
            if len(generated) != 1:
                result.update(status='model_failure', reason='BAGEL returned other than one image')
                return None
            image = generated[0]
        else:
            token_count = len(model.tokenizer(prompt)['input_ids'])
            run_records(self.run).put('generation/' + job['job_id'] + '/native_input', {'prompt': prompt, 'tokens': token_count,
                      'image_roles': job['request']['image_roles'], 'config': cfg})
            if token_count > cfg['max_sequence_length']:
                result.update(status='unsupported_input', reason='Qwen text context exceeded; no truncation')
                return None
            kwargs = dict(cfg, prompt=prompt, generator=torch.Generator('cpu').manual_seed(job['seed']))
            if self.backend == 'qwen2511':
                kwargs['image'] = images  # Original first, then knowledge figures.
            elif images:
                raise ValueError('Qwen2512 cannot receive images')
            image = model(**kwargs).images[0]
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return buffer.getvalue()

    async def aclose(self):
        if self.native:
            torch = self.native[2]
            self.native = None
            import gc
            gc.collect()
            torch.cuda.empty_cache()


class RunBackendGraph:
    """Select Python/CUDA, then launch the notebook's native Dataset graph.

    This boundary is required by BAGEL/Qwen dependency versions. It does not
    iterate jobs, allocate GPUs, retry calls, or manage other services.
    demiflow map_async schedules these backend partitions serially.
    """
    concurrency = 1
    queue_depth = 1

    def __init__(self, run, config):
        self.run, self.config = Path(run), config

    async def __call__(self, partition):
        backend = partition['backend']
        python = self.config['python'][backend]
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(ROOT) + os.pathsep + environment.get('PYTHONPATH', '')
        environment['CUDA_VISIBLE_DEVICES'] = '' if backend == 'gemini' else str(self.config['cuda'][backend])
        folder = self.run / 'backends' / backend
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / 'process.log').open('ab') as output:
            process = await asyncio.create_subprocess_exec(
                python, '-u', '-m', 'curation.evaluation.pipeline', '--run', str(self.run),
                '--backend', backend, '--through', 'generate',
                cwd=ROOT, env=environment, stdout=output, stderr=output)
            try:
                code = await process.wait()
            except BaseException:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                raise
        if code:
            raise RuntimeError(f'{backend} graph exited {code}; inspect {folder / "process.log"}')
        from curation.preparation.stages import stage_ref
        return {**partition, 'dataset_ref': stage_ref(folder, 'results').to_dict()}


def validate_execution_config(config, backends):
    for backend in backends:
        if not Path(config['python'][backend]).is_file():
            raise ValueError('Missing Python environment: ' + backend)
        if backend != 'gemini':
            device = config['cuda'].get(backend)
            if device is None or not str(device).strip() or ',' in str(device):
                raise ValueError('Explicitly assign one available CUDA device to ' + backend)
