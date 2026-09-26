"""模型图片输入的字节读取与编码，不承担材料筛选或字段关联。"""
import base64

from preparation.operaters.inputs import pixels


def image_data_url(blob_ref):
    """读取固定 Blob、校验并解码图片，返回模型接收的 data URL；异常直接抛出。"""
    raw, mime, _ = pixels({'blob_ref': blob_ref, 'sha256': blob_ref['sha256']})
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


def image_blob_ref(image):
    """解码 preparation 的存储引用；缺失或损坏引用直接报错，不搜索备用原图。"""
    import json
    from demiflow.lance.blobs import BlobRef
    if not image.get('source_refs'):
        raise ValueError('Preparation image lacks blob_ref/source_refs')
    source = json.loads(image['source_refs'][0])
    return BlobRef(relative_uri=source['relative_uri'], version=source['lance_version'],
                   sha256=image['sha256']).to_dict()
