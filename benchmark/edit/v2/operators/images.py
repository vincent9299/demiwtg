"""固定 ObjectRef 的有界图片读取与解码；不选材料、不调用模型。"""
import base64
import io

from PIL import Image
from demiflow.objects import ObjectRef


def image_bytes(object_ref):
    raw = ObjectRef(**object_ref).read(max_bytes=32 * 1024**2)
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError('Edit image exceeds 24 million decoded pixels')
        image.load()
        mime = Image.MIME.get(image.format)
        if not mime:
            raise ValueError('Unsupported image format')
    return raw, mime


def image_data_url(object_ref):
    raw, mime = image_bytes(object_ref)
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


def image_object_ref(row):
    """消费 preparation 交付的独立 URI；不使用来源表定位图片。"""
    if not row.get('image_uri'):
        raise ValueError('Published image lacks image_uri; publish independent objects first')
    return ObjectRef(row['image_uri'], row['sha256']).to_dict()
