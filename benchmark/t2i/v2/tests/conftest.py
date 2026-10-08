from preparation.articles.tests.conftest import isolated_lake, ingest_generated_fixture_images

from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch, request):
    """HTTP 测试须使用 MockTransport；避免平台改用 stream 后绕过 get/post mock。"""
    if Path(__file__).parent not in Path(str(request.node.path)).resolve().parents:
        return
    import httpx
    monkeypatch.setenv('MODELHUB_API_KEY', 'local-test-only')

    async def deny_async(*args, **kwargs):
        pytest.fail('T2I tests must not use a real HTTP transport')

    def deny_sync(*args, **kwargs):
        pytest.fail('T2I tests must not use a real HTTP transport')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', deny_async)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', deny_sync)
