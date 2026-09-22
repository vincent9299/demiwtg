"""Business policy for the authorized local model endpoint."""
from urllib.parse import urlparse

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
