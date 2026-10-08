"""生成数据流的行函数：构造生成 prompt、调用配置的本地文生图端点、校验并写独立对象。

不读表、不调度 Dataset；pipeline 显式绑定模型请求与端点，
图片字节经 sha256 写入 LocalObjectStore 后只保留 ObjectRef。
"""
import base64
import hashlib
import io
import json
from time import perf_counter

import httpx
from PIL import Image
from demiflow.execution.artifacts import digest


def build_generation_prompt(row, *, template):
    """概念 + 完整 taxonomy → 统一生成 prompt；模板里不出现概念定义或答案性描述。

    taxonomy 只用于帮助模型锁定所指对象（消歧），不描述外观；
    concept 原样嵌入，英文概念不翻译。
    """
    taxonomy = '；'.join(path.strip() for path in (row.get('taxonomy') or []) if path.strip())
    if not taxonomy:
        taxonomy = '未提供'
    return template.format(concept=row['concept'], taxonomy=taxonomy)


def generation_seed(run_seed, concept, prompt):
    """由 run_seed/concept/prompt 派生确定性种子；同名重跑不换图，prompt 变更则重生成。"""
    payload = json.dumps([run_seed, concept, prompt], ensure_ascii=False)
    return int(hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16], 16) % (2**31)


def prepare_generation(row, *, endpoints, model, size, run_seed, revision, steps=None):
    """当前输入行 → 实际请求与复用身份；端点池、模型部署版本和所有请求参数一起绑定。

    endpoints 是部署等价的回环端点集合（同一模型权重与推理配置，共用 revision 标签）；
    复用身份绑定排序后的端点集合而不是单个 URL：池内轮转不影响身份，池成员变化视为
    部署变更、按新身份重生成。
    """
    request = {'model': model, 'prompt': row['prompt'], 'size': size, 'n': 1,
               'response_format': 'b64_json',
               'seed': generation_seed(run_seed, row['concept'], row['prompt'])}
    if steps is not None:
        request['num_inference_steps'] = steps
    identity = digest({'concept': row['concept'], 'endpoints': sorted(endpoints),
                       'revision': revision, 'request': request})
    return {**row, '_generation_request': request, '_generation_id': identity}


def saved_generation_identity(row):
    """旧行只提取已记录的请求身份；没有身份的历史行不冒充当前配置的缓存。"""
    call = json.loads(row['call_json'])
    return {**row, '_generation_id': call.get('generation_id')}


def decode_image(data_json, *, max_bytes):
    """OpenAI images/generations 回包 → (原始字节, mime, 宽, 高)；格式或大小不符直接抛错。"""
    items = (data_json or {}).get('data') or []
    encoded = None
    for item in items:
        if item.get('b64_json'):
            encoded = item['b64_json']
            break
    if not encoded:
        raise ValueError('response has no data[].b64_json')
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > max_bytes:
        raise ValueError(f'image exceeds max_image_bytes: {len(raw)}')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in {'PNG', 'JPEG', 'WEBP'}:
            raise ValueError('unsupported generated image format: ' + str(image.format))
        width, height = image.size
        if width * height > 24_000_000:
            raise ValueError('generated image exceeds 24 million pixels')
        mime = 'image/jpeg' if image.format == 'JPEG' else Image.MIME[image.format]
        image.load()  # 必须完整解码像素；只读头部会漏过截断图片。
    if width < 1 or height < 1:
        raise ValueError('generated image has no pixels')
    return raw, mime, width, height


def _failure(row, *, status, reason, seed, call):
    return {**row, 'status': status, 'reason': reason, 'seed': seed,
            'image_sha256': None, 'object_ref': None, 'width': 0, 'height': 0,
            'latency_s': call.get('latency_s') or 0.0, 'call_json': json.dumps(call, ensure_ascii=False)}


async def generate_image(row, *, base_url, revision, object_store,
                         max_image_bytes, timeout_s, auth=None):
    """一行概念 → 调用配置的本地模型生成 → 校验并写独立对象；技术失败保留原因不断链。

    不自动重试：网络错误、非 200、缺图或坏图都记 status='failed'，
    重试/复用策略由调用方按完整请求身份控制。call_json 不含图片字节。
    client 按请求创建：连接成本相对生成耗时可忽略，且与流式执行器的
    事件循环解耦。
    """
    request = row['_generation_request']
    seed = request['seed']
    call = {'transport': 'openai_images', 'endpoint': '/images/generations',
            'base_url': base_url, 'revision': revision, 'generation_id': row['_generation_id'],
            'model': request['model'], 'request': request}
    started = perf_counter()
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s,
                                     trust_env=False,
                                     headers={'Authorization': auth} if auth else None) as client:
            async with client.stream('POST', '/images/generations', json=request) as response:
                chunks = []
                length = 0
                # Bound the encoded JSON before decoding it or image pixels.
                response_limit = (max_image_bytes + 2) // 3 * 4 + 128 * 1024
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > response_limit:
                        call.update(latency_s=round(perf_counter() - started, 3))
                        return _failure(row, status='failed', reason='生成回包超过字节预算', seed=seed, call=call)
                    chunks.append(chunk)
                response = httpx.Response(response.status_code, content=b''.join(chunks),
                                          headers={'content-type': response.headers.get('content-type', 'application/json')})
    except httpx.HTTPError as exc:
        call.update(latency_s=round(perf_counter() - started, 3), transport_error=f'{type(exc).__name__}: {exc}')
        return _failure(row, status='failed', reason=f'请求失败：{type(exc).__name__}: {exc}', seed=seed, call=call)
    call['http_status'] = response.status_code
    if response.status_code != 200:
        detail = response.text[:300]
        call.update(latency_s=round(perf_counter() - started, 3), error_body=detail)
        return _failure(row, status='failed', reason=f'端点返回 {response.status_code}：{detail}', seed=seed, call=call)
    try:
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError('response must be a JSON object')
        if body.get('model') is not None and body['model'] != request['model']:
            raise ValueError('response model differs from requested generation_model')
        usage = body.get('usage') or {}
        if ('num_inference_steps' in request and isinstance(usage, dict)
                and usage.get('steps') is not None and usage['steps'] != request['num_inference_steps']):
            raise ValueError('response steps differ from requested num_inference_steps')
        raw, mime, width, height = decode_image(body, max_bytes=max_image_bytes)
    except (ValueError, KeyError, TypeError, OSError, Image.DecompressionBombError) as exc:
        call.update(latency_s=round(perf_counter() - started, 3), error_body=str(exc)[:300])
        return _failure(row, status='failed', reason=f'回包无效：{type(exc).__name__}: {exc}', seed=seed, call=call)
    sha = hashlib.sha256(raw).hexdigest()
    ref = object_store.put(raw)
    call.update(latency_s=round(perf_counter() - started, 3), image_sha256=sha,
                mime=mime, bytes=len(raw))
    result = {**row, 'status': 'generated', 'reason': '', 'seed': seed,
              'image_sha256': sha, 'object_ref': ref.to_dict(), 'width': width, 'height': height,
              'latency_s': call['latency_s'], 'call_json': json.dumps(call, ensure_ascii=False)}
    return result
