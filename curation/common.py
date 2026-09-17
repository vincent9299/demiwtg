"""Small shared filesystem and existing preannotation endpoint utilities."""

from pathlib import Path

import os

from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

def blob_shas(blobs_root=None):
    """List content-addressed filenames without a stat/hash per image."""
    directory = Path(blobs_root) if blobs_root is not None else ROOT / 'datasets/demiwtg/blobs'
    present = set()
    if not directory.exists():
        return present
    for sub in directory.iterdir():
        if sub.is_dir():
            with os.scandir(sub) as entries:
                for entry in entries:
                    sha = entry.name.partition('.')[0]
                    if len(sha) == 64 and all(c in '0123456789abcdef' for c in sha):
                        present.add(sha)
    return present

def safe_file(base, relative):
    base = Path(base).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError('path escapes dataset')
    return path

def require(ok, message):
    if not ok:
        raise ValueError(message)

def validate_local_endpoint(base_url, model):
    """User scope: direct local Qwen only. Paid gateways require a new explicit decision."""
    url = urlparse(base_url)
    require(url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')
            and url.port in (8000, 8001) and url.path.rstrip('/') == '/v1'
            and not url.username and not url.password and not url.query and not url.fragment,
            'only direct local Qwen on 8000/8001 is authorized; paid gateways (including 4001) are blocked')
    require(model.lower() == 'qwen3.8-27b', 'only local qwen3.8-27b is authorized')
