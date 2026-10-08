"""模型图片输入的字节读取与编码，不承担材料筛选或字段关联。"""
import base64

from preparation.articles.operators.inputs import pixels


def image_data_url(object_ref):
    """读取独立图片 URI、校验并解码图片，返回模型接收的 data URL；异常直接抛出。"""
    raw, mime, _ = pixels({'object_ref': object_ref, 'sha256': object_ref['sha256']})
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


def image_object_ref(image):
    """公共图片只交付独立 URI 和 SHA；来源追溯不参与图片寻址。"""
    from demiflow.objects import ObjectRef
    if not image.get('image_uri'):
        raise ValueError('Preparation image lacks image_uri; publish independent objects first')
    return ObjectRef(image['image_uri'], image['sha256']).to_dict()


def positive_image_data_url(object_ref, *, max_side=1536):
    """保留原有字符串接口；共享读取结果中的data URL。"""
    return positive_image_input(object_ref, max_side=max_side)['data_url']


def positive_image_input(object_ref, *, max_side=1536):
    """正例等比缩至最长边不超过max_side；主进程仅解码不超2400万像素的位图。

    JPEG 等支持 draft 的格式先降低解码尺寸；PNG等无法预降采样的大图交给
    平台限内存的独立进程，最多1.6亿原像素、1.5GiB地址空间、45秒。32MiB编码上限、
    SHA校验、EXIF方向、透明白底与JPEG90保持；小图沿用原路径以复用旧请求。
    """
    import io
    from PIL import Image, ImageOps
    from demiflow.objects import ObjectRef
    if type(max_side) is not int or not 1 <= max_side <= 4096:
        raise ValueError('max_side must be an integer in [1, 4096]')
    raw = ObjectRef(**object_ref).read(max_bytes=32 * 1024 * 1024)
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            original_size = picture.size  # 必须在draft/EXIF校正/缩放之前保存原文件宽高。
            if picture.width * picture.height > 24_000_000:
                # draft 只调整解码计划；先确认实际解码尺寸受限，再分配像素。
                # 它不裁剪画面，方向校正及最终等比缩放仍由下方统一处理。
                ratio = max_side / max(original_size)
                target = tuple(max(1, round(side * ratio)) for side in original_size)
                picture.draft(picture.mode, target)
                if picture.width * picture.height > 24_000_000:
                    from demiflow.collect.image_resize import resize_image
                    encoded = resize_image(raw, max_side=max_side, quality=90)
                    return {'data_url': 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode(),
                            'width': original_size[0], 'height': original_size[1]}
            picture.load()
            picture = ImageOps.exif_transpose(picture).convert('RGBA')
            picture.thumbnail((max_side, max_side))
            background = Image.new('RGB', picture.size, 'white')
            background.paste(picture, mask=picture.getchannel('A'))
            output = io.BytesIO()
            background.save(output, format='JPEG', quality=90)
    except (OSError, SyntaxError, EOFError, Image.DecompressionBombError) as error:
        raise ValueError('Invalid positive image: ' + str(error)) from error
    return {'data_url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode(),
            'width': original_size[0], 'height': original_size[1]}
