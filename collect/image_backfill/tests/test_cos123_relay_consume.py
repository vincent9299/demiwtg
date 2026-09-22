"""R3 验收：123pan 中继按代（边车 md5/size）判定消费单元，不再按路径永久去重。

以替身 cos_call / pan123 / pan_upload 环境驱动真实 Daemon.consume：
1. 同路径新版本（边车 md5 变化）必须被完整重消费并覆盖上传；
2. 同一代重复投递保持幂等（直接补删，不再上传）；
3. 旧 done 记录不得删除新一代内容。
"""
import hashlib
import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import cos123_relay  # noqa: E402


class FakePanError(Exception):
    pass


class FakeEnv:
    def __init__(self, content: bytes, sidecar: dict):
        self.content = content
        self.sidecar = sidecar
        self.deleted = []
        self.uploads = []          # (name, md5)
        self.pan_files = {}        # name -> md5（模拟 123pan 目录最新版）

    def cos_call(self, method, key, params=None, data=None, stream=False,
                 timeout=None):
        if method == "GET" and key.startswith(cos123_relay.META_PREFIX):
            if self.sidecar is None:
                return 404, {}, b""
            return 200, {}, json.dumps(self.sidecar).encode()

        class R:
            def __init__(self, payload):
                self._payload = payload

            def iter_content(self, _n):
                yield self._payload

            def close(self):
                pass

        if method == "GET" and stream:
            return 200, {}, R(self.content)
        raise AssertionError(f"unexpected cos_call {method} {key}")

    def cos_delete(self, key):
        self.deleted.append(key)


def make_daemon(tmp_path, env):
    args = types.SimpleNamespace(pan_root="/pan", readback_pct=0, once=True,
                                 poll_gap=0)
    daemon = cos123_relay.Daemon.__new__(cos123_relay.Daemon)
    daemon.args = args
    daemon.spool = str(tmp_path)
    daemon.ledger = cos123_relay.Ledger(os.path.join(tmp_path, "ledger.db"))
    daemon.tree = types.SimpleNamespace(root_id=1, ensure=lambda d: 2)
    daemon.fails = {}
    daemon.units_done = 0
    daemon._token_ok = True
    cos123_relay.cos_call = env.cos_call
    cos123_relay.cos_delete = env.cos_delete

    def collision_upload(path, dir_id, name):
        md5 = hashlib.md5(Path(path).read_bytes()).hexdigest()
        env.pan_files[name] = md5              # 同路径覆盖：只保留最新版
        env.uploads.append((name, md5))
        return {"fileID": 100 + len(env.uploads), "reuse": False}

    cos123_relay.pan_upload_with_collision = collision_upload
    return daemon


def test_same_path_new_generation_is_resynced(tmp_path):
    v1 = b"old-version-bytes"
    v2 = b"new-version-bytes" * 10
    env = FakeEnv(v1, {"md5": hashlib.md5(v1).hexdigest(), "size": len(v1)})
    daemon = make_daemon(tmp_path, env)
    key = f"{cos123_relay.SRC_ROOT}/kb/main.json"
    daemon.consume(key, len(v1))
    assert env.pan_files["main.json"] == hashlib.md5(v1).hexdigest()
    assert key in env.deleted                      # 第一代消费并删除

    # 同路径第二代：边车 md5/size 均变化
    env2 = FakeEnv(v2, {"md5": hashlib.md5(v2).hexdigest(), "size": len(v2)})
    daemon2 = make_daemon(tmp_path, env2)
    daemon2.consume(key, len(v2))                  # 旧 done 不得直接删
    assert env2.pan_files["main.json"] == hashlib.md5(v2).hexdigest()
    assert key in env2.deleted                      # 新一代消费后删除
    assert env2.uploads, "new generation must be uploaded"


def test_same_generation_redelivery_is_idempotent(tmp_path):
    v = b"stable-bytes"
    env = FakeEnv(v, {"md5": hashlib.md5(v).hexdigest(), "size": len(v)})
    daemon = make_daemon(tmp_path, env)
    key = f"{cos123_relay.SRC_ROOT}/kb/a.json"
    daemon.consume(key, len(v))
    uploads_after_first = len(env.uploads)
    env.deleted.clear()

    env_again = FakeEnv(v, {"md5": hashlib.md5(v).hexdigest(), "size": len(v)})
    daemon_again = make_daemon(tmp_path, env_again)
    daemon_again.consume(key, len(v))               # 同代重投：补删不上传
    assert uploads_after_first == 1
    assert env_again.uploads == []                  # 幂等：没有第二次上传
    assert key in env_again.deleted
    assert env.pan_files["a.json"] == hashlib.md5(v).hexdigest()  # 首次上传仍在


def test_old_done_never_deletes_new_generation(tmp_path):
    v2 = b"brand-new-content"
    env = FakeEnv(v2, {"md5": hashlib.md5(v2).hexdigest(), "size": len(v2)})
    daemon = make_daemon(tmp_path, env)
    # 预置旧 done：不同 md5/size
    daemon.ledger.record("kb/main.json", 3, "0" * 32, 1, "done")
    key = f"{cos123_relay.SRC_ROOT}/kb/main.json"
    daemon.consume(key, len(v2))
    # 新一代内容被上传（而非被旧 done 直接删除丢弃）
    assert env.pan_files["main.json"] == hashlib.md5(v2).hexdigest()
    assert env.uploads
