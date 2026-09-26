"""judge 图片读取与编码；不承担字段筛选、评分或结果落表。"""
import base64
import io

from PIL import Image
from demiflow.lance.blobs import BlobRef


def image_data_url(blob_ref, root):
    """读取固定 Blob，识别 MIME 并按原尺寸编码；读取或解码异常直接抛出。"""
    raw = BlobRef(**blob_ref).read(root)
    with Image.open(io.BytesIO(raw)) as image:
        mime = Image.MIME[image.format]
    return 'data:' + mime + ';base64,' + base64.b64encode(raw).decode()
