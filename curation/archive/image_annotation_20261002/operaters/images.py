"""逐图片处理：独立 URI 验证、像素编码、中性响应与同协议证据复用。

encode_pixels 是下游显式导入的共享编码接口；参数和编码行为保持一致。
模型调用、表关联和写入均在正式入口可见，不读取概念或文章。
"""
import base64
import io
import json
import struct
from pathlib import Path
from PIL.Image import DecompressionBombError
from demiflow.execution.artifacts import digest
from demiflow.objects import ObjectRef
from preparation.images.annotation.operaters.schema import (
    call_reference, canonical, image_annotation_id, record_provenance,
    validate_description, validate_image_response,
)


def _oriented(source):
    """EXIF 方向转正；元数据损坏时保留存储朝向并返回提示，解码失败照常抛错。"""
    from PIL import ImageOps
    source.load()
    try:
        return ImageOps.exif_transpose(source), None
    except (SyntaxError, ValueError, struct.error) as error:
        return source.copy(), {'operation': 'exif_transpose',
                               'status': 'invalid_metadata_kept_stored_orientation',
                               'error': f'{type(error).__name__}: {error}'}


def encode_pixels(raw, *, max_edge, jpeg_quality):
    """原始字节 → (data URL, 编码字节 SHA)；确定性预处理，同输入同输出。"""
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as source:
        oriented, _warning = _oriented(source)
        pic = oriented.convert('RGBA')
        background = Image.new('RGBA', pic.size, 'white')
        background.alpha_composite(pic)
        pic = background.convert('RGB')
        pic.thumbnail((max_edge, max_edge))
        out = io.BytesIO()
        pic.save(out, format='JPEG', quality=jpeg_quality)
    encoded = out.getvalue()
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode(), digest(encoded)


class PrepareImagePixels:
    """线程执行算子：读取本行独立 image_uri、核 SHA、编码为请求图片。

    非 ready 行直接透传（复用/失败/跳过不读字节）；读取或编码失败转为
    状态行保留原因，不中止整链。读取不依赖采集表版本或来源引用。
    """

    def __init__(self, root, *, max_edge, jpeg_quality):
        self.root = Path(root)
        self.max_edge = max_edge
        self.jpeg_quality = jpeg_quality

    def _read_bytes(self, row):
        if not row.get('image_uri'):
            raise ValueError('image_uri missing; publish independent objects before annotation')
        return ObjectRef(row['image_uri'], row['sha256']).read()

    def __call__(self, row):
        if row.get('status') != 'ready':
            return row
        try:
            raw = self._read_bytes(row)
            data_url, input_sha256 = encode_pixels(
                raw, max_edge=self.max_edge, jpeg_quality=self.jpeg_quality)
        except (OSError, ValueError, SyntaxError, struct.error, DecompressionBombError) as error:
            # 本图片读取/解码失败保留行；程序错误和内存耗尽继续抛出。
            return {**row, 'status': 'pixel_error',
                    'error': f'{type(error).__name__}: {error}', 'prompt_images': []}
        return {**row, 'prompt_images': [data_url], 'input_sha256': input_sha256}


def reusable_image_records(row, config_id):
    """公共行 → 同协议可复用的 (描述, 图片评分) 记录；不满足条件不产出。

    复用必须同时有同 SHA、同 config_id、status=done 的描述与评分记录且 annotation_id
    一致（编码确定性使 SHA+协议即输入绑定）；多个不同对视为歧义并报错。
    """
    descriptions = [d for d in row.get('descriptions') or []
                    if (d or {}).get('config_id') == config_id and d.get('status') == 'done']
    scores = [s for s in row.get('image_scores') or []
              if (s or {}).get('config_id') == config_id and s.get('status') == 'done']
    pairs = {(d['annotation_id'], s['annotation_id'])
             for d in descriptions for s in scores if d['annotation_id'] == s['annotation_id']}
    if not pairs:
        return []
    if len(pairs) > 1 or len(descriptions) > 1 or len(scores) > 1:
        raise ValueError('ambiguous reusable image annotations for ' + row['sha256'])
    description, score = descriptions[0], scores[0]
    try:
        details = json.loads(score.get('provenance', {}).get('details_json') or '{}')
    except (ValueError, TypeError):
        details = {}
    return [{'sha256': row['sha256'], 'annotation_id': score['annotation_id'],
             'description_record': description, 'score_record': score,
             'richness': score.get('richness'),
             'input_sha256': details.get('image', {}).get('input_sha256')}]


def reused_image_result(reused, *, config_id):
    """公共复用记录 → IMAGE_RESULTS 行；不重新编码、不调用模型。"""
    score = reused['score_record']
    return {'sha256': reused['sha256'], 'annotation_id': reused['annotation_id'],
            'config_id': config_id, 'status': 'reused',
            'description_record': reused['description_record'], 'score_record': score,
            'richness': score.get('richness'), 'input_sha256': reused.get('input_sha256'),
            'attempts': 0, 'response_ref': score.get('provenance', {}).get('details_json') or '',
            'error': '', 'details_json': canonical({'reuse': 'public_same_protocol'})}


def apply_image_result(row, *, run_id, image_config_id, model, source_binding,
                       max_edge, jpeg_quality):
    """图片级响应 → IMAGE_RESULTS 行；成功产出描述+评分两条公共记录。

    失败保留技术状态与原因（pixel_error/pending_response/model_error/invalid_response），
    不把失败当成 richness=0；annotation_id 在有输入摘要时即计算，便于失败定位。
    """
    sha256 = row['sha256']
    annotation_id = (image_annotation_id(sha256, image_config_id, row['input_sha256'])
                     if row.get('input_sha256') else None)
    error = row.get('prompt_error')
    call = row.get('prompt_call') or (error or {}).get('call') or {}
    base = {'sha256': sha256, 'image_uri': row.get('image_uri'), 'annotation_id': annotation_id, 'config_id': image_config_id,
            'status': row['status'], 'description_record': None, 'score_record': None,
            'richness': None, 'input_sha256': row.get('input_sha256'),
            'attempts': len(call.get('attempts') or []) or int(bool(call)),
            'response_ref': call_reference(call), 'error': '', 'details_json': ''}
    if row.get('status') == 'pixel_error':
        return {**base, 'error': row.get('error') or 'pixel preparation failed'}
    if error:
        status = ('pending_response' if error.get('type') in {'PromptResponsePending', 'UncertainPromptCall', 'PromptBudgetExceededError', 'PromptReplayMissError'}
                  else 'invalid_response' if error.get('type') in {'PromptResponseContractError', 'PromptResponseParseError'}
                  else 'model_error')
        return {**base, 'status': status,
                'error': canonical({k: error.get(k) for k in ('type', 'detail')})}
    try:
        description, richness, richness_reason = validate_image_response(row.get('prompt_result'))
    except (ValueError, TypeError, KeyError, AttributeError) as invalid:
        return {**base, 'status': 'invalid_response', 'error': f'{type(invalid).__name__}: {invalid}',
                'response_ref': call_reference(row.get('prompt_call') or (error or {}).get('call'))}
    details = {
        'image': {'source': source_binding, 'sha256': sha256,
                  'input_sha256': row['input_sha256'], 'max_edge': max_edge,
                  'jpeg_quality': jpeg_quality},
        'protocol': {'kind': 'image', 'config_id': image_config_id},
        'response_ref': json.loads(base['response_ref']) if base['response_ref'] else {},
    }
    provenance = record_provenance(run_id=run_id, config_id=image_config_id, model=model, details=details)
    description_record = {'annotation_id': annotation_id, 'config_id': image_config_id,
                          'status': 'done', 'description': description,
                          'attempts': base['attempts'], 'provenance': provenance}
    score_record = {'annotation_id': annotation_id, 'config_id': image_config_id, 'status': 'done',
                    'richness': richness, 'richness_reason': richness_reason,
                    'provenance': provenance}
    return {**base, 'status': 'reused' if call.get('reused') else 'done', 'description_record': description_record,
            'score_record': score_record, 'richness': richness,
            'details_json': canonical(details)}


def finalize_image_result(row, *, run_id, image_config_id, model, source_binding,
                          max_edge, jpeg_quality):
    """图片级结果分发：复用行直接携带公共记录，其余按响应/失败状态落表。"""
    if row.get('status') == 'reused':
        return {**reused_image_result(row, config_id=image_config_id), 'image_uri': row.get('image_uri')}
    return apply_image_result(row, run_id=run_id, image_config_id=image_config_id,
                              model=model, source_binding=source_binding,
                              max_edge=max_edge, jpeg_quality=jpeg_quality)


class AnnotationProgress:
    """完成行触发的进度日志：分开成功/复用/失败与窗口吞吐，无新行不产生心跳。"""

    def __init__(self, stage, *, every=50):
        if type(every) is not int or every < 1:
            raise ValueError('progress_every must be a positive integer')
        self.stage, self.every = stage, every
        self.count = self.done = self.reused = self.errors = 0
        self.last_time = None

    def __call__(self, row):
        self.count += 1
        status = row.get('status')
        if status not in {'done', 'reused'}:
            self.errors += 1
        elif status == 'reused':
            self.reused += 1
        if self.count == 1 or self.count % self.every == 0:
            from time import monotonic
            now = monotonic()
            speed = ''
            if self.last_time is not None:
                minutes = max(now - self.last_time, 1e-9) / 60
                speed = f'，窗口吞吐={((self.count - self._last_count) / minutes):.1f} 行/分钟'
            self._last_count = self.count
            self.last_time = now
            print(f'[Image annotation][{self.stage}] 已处理={self.count}，失败={self.errors}，'
                  f'复用={self.reused}{speed}', flush=True)
        return row



def image_input_row(row, *, skip_annotated=False):
    """有效旧描述可显式排除；缺 URI 保留为待处理行，失败不缩小分母。"""
    sha = row['sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Expected a lowercase SHA256 image key')
    skipped = False
    if skip_annotated:
        for record in row.get('descriptions') or []:
            if (record or {}).get('status') != 'done':
                continue
            try:
                validate_description(record.get('description'))
            except (ValueError, TypeError):
                continue
            skipped = True
            break
    return {'sha256': sha, 'image_uri': row.get('image_uri'),
            'status': 'skipped_annotated' if skipped else 'ready',
            'reason': 'explicit skip_annotated_images scope rule' if skipped else ''}


def reuse_candidate(row, *, config_id):
    """一公共图片行产生至多一对经校验的描述/丰富度；不猜最新协议。"""
    matches = reusable_image_records(row, config_id)
    if not matches:
        return {'sha256': row['sha256'], 'reusable': None}
    saved = matches[0]
    validate_image_response({'description': saved['description_record']['description'],
                             'richness': saved['score_record']['richness'],
                             'richness_reason': saved['score_record']['richness_reason']})
    input_sha = saved.get('input_sha256')
    if not input_sha or image_annotation_id(row['sha256'], config_id, input_sha) != saved['annotation_id']:
        raise ValueError('Reusable annotation lacks a valid input/identity binding: ' + row['sha256'])
    return {'sha256': row['sha256'], 'reusable': saved}


def attach_reuse(row):
    saved = row.get('reusable')
    return ({**row, **saved, 'status': 'reused'} if saved else row)


def unique_image(a, b):
    """一个 SHA 必须只有一行；重复不能被去重悄悄掩盖。"""
    if a is not None:
        raise ValueError('Duplicate image SHA: ' + b['sha256'])
    return b
