import base64
import io
import pytest
from PIL import Image, UnidentifiedImageError
from curation.preparation.contracts import digest
from curation.preparation.ops.image_selection import pixels
from curation.preparation.ops.image_pixels import oriented_pixels


def item(path):
    return {'image_id': 'I1', 'bytes': {'path': str(path)},
            'record': {'sha256': digest(path.read_bytes())}}


def test_bad_exif_preserves_pixels_and_records_warning(tmp_path):
    path = tmp_path / 'bad-exif.png'
    Image.new('RGB', (31, 17), 'red').save(path, exif=b'Exif\0\0\x00\x00\x0b\x00\x0f\x01\x02\x00')
    original = path.read_bytes()
    with Image.open(path) as source:
        with pytest.raises(SyntaxError):
            source.getexif()
    urls, roles = pixels([item(path)])
    with Image.open(io.BytesIO(base64.b64decode(urls[0].split(',', 1)[1]))) as result:
        assert result.size == (31, 17)
        assert result.getpixel((15, 8))[0] > 240
    assert roles[0]['metadata_warning']['status'] == 'invalid_metadata_kept_stored_orientation'
    assert path.read_bytes() == original


def test_valid_orientation_is_applied(tmp_path):
    path = tmp_path / 'rotated.jpg'
    exif = Image.Exif(); exif[274] = 6
    Image.new('RGB', (31, 17), 'red').save(path, exif=exif)
    _, roles = pixels([item(path)])
    assert roles[0]['dimensions'] == [17, 31]
    assert 'metadata_warning' not in roles[0]


def test_pixel_decode_failure_is_not_suppressed(tmp_path):
    path = tmp_path / 'broken.png'; path.write_bytes(b'not an image')
    from unittest.mock import patch
    # Decoder errors are tested after a successful byte read, independently of storage.
    with patch('curation.preparation.asset_io.asset_bytes', return_value=(path.read_bytes(), {})):
        with pytest.raises(UnidentifiedImageError):
            pixels([item(path)])

    class BrokenPixels:
        def load(self):
            raise OSError('corrupt pixels')

    with pytest.raises(OSError, match='corrupt pixels'):
        oriented_pixels(BrokenPixels())
