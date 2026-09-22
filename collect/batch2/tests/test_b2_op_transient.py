"""R5 验收：瞬态下载失败回队列重试，Met 失败不缓存为空。

场景（FakeCOS + curl/urlopen 替身驱动真实 b2_batch_op + queue_runner）：
1. 503 retry_exhausted → 批不 complete、不入死信；恢复后重试产生成功记录；
2. 真实 404 → 行级死信、批正常完成（终结）；
3. Met API 网络异常 → 不缓存、批回队列；恢复后成功；空 primaryImage 的
   真实应答 → 死信 met:no_primary_image 且可缓存。
"""
import gzip
import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, "/yzp/zhaozy/yangzepeng/0905/demiflow")
import b2_op  # noqa: E402
from demiflow.collect.cosio import COSCreds, COSIO  # noqa: E402
from demiflow.collect.cosqueue import COSQueue  # noqa: E402
from demiflow.collect.queue_runner import run as qr_run  # noqa: E402

JPEG = b"\xff\xd8\xff" + b"x" * 2048


class FakeCOS:
    def __init__(self):
        self.store = {}
        self.seen = []

    def transport(self, method, url, headers, data, timeout):
        self.seen.append((method, url))
        u = urllib.parse.urlsplit(url)
        key = urllib.parse.unquote(u.path.lstrip("/"))
        qs = urllib.parse.parse_qs(u.query)
        if method == "GET" and qs.get("prefix"):
            prefix = qs["prefix"][0]
            keys = sorted(k for k in self.store if k.startswith(prefix))
            body = "".join(f"<Key>{k}</Key>" for k in keys).encode()
            body += b"<IsTruncated>false</IsTruncated>"
            return 200, {}, body
        if method == "PUT":
            inm = (headers or {}).get("If-None-Match")
            if inm == "*" and key in self.store:
                return 412, {}, b""
            self.store[key] = data or b""
            return 200, {"ETag": '"fake"'}, b""
        if method == "GET":
            return (200, {}, self.store[key]) if key in self.store else (404, {}, b"")
        if method == "HEAD":
            if key in self.store:
                return 200, {"Content-Length": str(len(self.store[key]))}, b""
            return 404, {}, b""
        if method == "DELETE":
            self.store.pop(key, None)
            return 204, {}, b""
        return 405, {}, b""


class CurlStub:
    """按 URL 派发预设结果；fail_then_ok 模拟瞬态恢复。"""

    def __init__(self):
        self.responses = {}      # url -> list[result-like]
        self.calls = 0

    def __call__(self, url, **kw):
        self.calls += 1
        queue = self.responses.get(url)
        if not queue:
            raise AssertionError(f"unexpected url {url}")
        result = queue.pop(0)
        if callable(result):
            result = result()
        return result


class R:
    def __init__(self, ok, data=b"", status=0, reason="", throttled=False):
        self.ok, self.data = ok, data
        self.status, self.reason, self.throttled = status, reason, throttled


@pytest.fixture()
def env(tmp_path, monkeypatch):
    fake = FakeCOS()
    creds = COSCreds(sid="sid", skey="skey")
    io_ = COSIO(creds, "fake-host", retries=1, backoff=[0],
                transport=fake.transport)
    state = b2_op.WorkerState(str(tmp_path / "run_w"), io_)
    monkeypatch.setattr(b2_op, "_STATE", state)
    monkeypatch.setattr(b2_op, "_LANES", 1)
    curl = CurlStub()
    monkeypatch.setattr(b2_op, "curl_fetch", curl)
    return types_env(fake, io_, state, curl, tmp_path, monkeypatch)


class types_env:
    def __init__(self, fake, io_, state, curl, tmp_path, monkeypatch):
        self.fake, self.io, self.state, self.curl = fake, io_, state, curl
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch

    def queue(self):
        return COSQueue(self.io, "queue")

    def run(self):
        return qr_run(b2_op.b2_batch_op, queue=self.queue(), worker="wt",
                      workdir=str(self.tmp_path / "wd"),
                      on_failure_sleep=0, log=lambda *a: None,
                      sleep=lambda s: None)

    def ledger_rows(self):
        path = Path(self.state.ledger_path)
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    def dead_rows(self):
        path = Path(self.state.dead_path)
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_transient_503_recovers_to_success(env):
    q = env.queue()
    q.produce([{"src": "oi", "extid": "i1", "url": "https://x/1.jpg"}],
              rows_per_batch=1)
    env.curl.responses["https://x/1.jpg"] = [
        lambda: R(False, status=503, reason="retry_exhausted"),   # 瞬态
        lambda: R(True, data=JPEG, status=200),                   # 恢复
    ]
    out = env.run()
    assert out == "drained"
    snap = q.snapshot()
    assert snap.done == {"b000000"}                # 批最终完成
    rows = env.ledger_rows()
    assert [r["extid"] for r in rows] == ["i1"]    # 成功记录存在
    assert env.dead_rows() == []                   # 瞬态绝不入死信


def test_permanent_404_terminates_in_dead_letter(env):
    q = env.queue()
    q.produce([{"src": "oi", "extid": "i2", "url": "https://x/2.jpg"}],
              rows_per_batch=1)
    env.curl.responses["https://x/2.jpg"] = [
        lambda: R(False, status=404, reason="http:404"),
    ]
    out = env.run()
    assert out == "drained"
    assert q.snapshot().done == {"b000000"}        # 永久失败：批正常终结
    dead = env.dead_rows()
    assert len(dead) == 1 and dead[0]["reason"] == "http:404"
    assert env.ledger_rows() == []


def test_met_network_error_not_cached_then_recovers(env, monkeypatch):
    q = env.queue()
    q.produce([{"src": "met", "extid": "m1", "url": ""}], rows_per_batch=1)
    attempts = {"n": 0}

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("temporarily unreachable")   # 瞬态网络异常

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"primaryImage": "https://met/img.jpg"}).encode()

        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    env.curl.responses["https://met/img.jpg"] = [
        lambda: R(True, data=JPEG, status=200),
    ]
    out = env.run()
    assert out == "drained"
    assert q.snapshot().done == {"b000000"}
    assert [r["extid"] for r in env.ledger_rows()] == ["m1"]
    assert env.dead_rows() == []
    assert "m1" in env.state.met_cache            # 只缓存成功应答


def test_met_empty_primary_image_is_permanent_and_cached(env, monkeypatch):
    q = env.queue()
    q.produce([{"src": "met", "extid": "m2", "url": ""}], rows_per_batch=1)

    def fake_urlopen(req, timeout=None):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"primaryImage": ""}).encode()

        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = env.run()
    assert out == "drained"
    dead = env.dead_rows()
    assert len(dead) == 1 and dead[0]["reason"] == "met:no_primary_image"
    assert env.state.met_cache.get("m2") == ""     # 真实无图：可缓存
