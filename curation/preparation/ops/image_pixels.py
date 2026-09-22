"""Decode real pixels while recording unusable EXIF orientation metadata."""
import struct
from PIL import ImageOps


def oriented_pixels(source):
    # Pixel decoding failures must still fail; only optional metadata can fall back.
    source.load()
    try:
        return ImageOps.exif_transpose(source), None
    except (SyntaxError, ValueError, struct.error) as error:
        return source.copy(), {'operation': 'exif_transpose',
            'status': 'invalid_metadata_kept_stored_orientation',
            'error': f'{type(error).__name__}: {error}'}


def inspect_lake_image(record):
    """Inspect only the authoritative Lance bytes of a collected source image."""
    import io
    from collect.materials import generation_origin
    from PIL import Image
    from curation.preparation.asset_io import asset_resolution
    sha = record.get('sha256')
    if not sha:
        return {'status': 'missing_expected_hash'}
    result = asset_resolution(record.get('path'), sha)
    if result.status != 'ok':
        return {'status': {'missing':'not_local', 'corrupt':'hash_mismatch',
                           'read_error':'read_error'}[result.status],
                'sha256':sha, 'error':result.error, 'asset_source':result.source}
    try:
        with Image.open(io.BytesIO(result.data)) as image:
            image.load()
            oriented, warning = oriented_pixels(image)
            width, height = oriented.size
            resolution = {'width':width, 'height':height, 'stored_width':image.width,
                'stored_height':image.height, 'megapixels':round(width*height/1e6,6),
                'aspect_ratio':round(width/height,6), 'metadata_warning':warning,
                'orientation_basis':'stored' if warning else 'exif_transposed'}
            return {'status':'verified_bytes', 'path':record.get('path'), 'sha256':sha,
                    'dimensions':list(image.size), 'format':image.format,
                    'resolution':resolution, 'byte_size':len(result.data),
                    'asset_source':result.source, 'knowledge_support':'not_reviewed',
                    'generation_origin':generation_origin(record)}
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        return {'status':'decode_error', 'sha256':sha, 'error':str(error)}
