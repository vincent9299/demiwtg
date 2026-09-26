"""数据根与存储位置解析。

“独立”首先是解除代码与数据的位置耦合：默认根是工作区（包含 demiwtg/ 与共享 datasets/），
部署时通过 ``DEMIWTG_DATASETS_ROOT`` 或显式参数指向仓库外目录，业务代码
不感知具体位置；记录中只保存相对位置（见 refs.DatasetRef）。
"""
from __future__ import annotations

import os
from pathlib import Path

DATASETS_ROOT_ENV = "DEMIWTG_DATASETS_ROOT"

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT
# 2026-09-23：各模块 datasets/ 直接存表；共享表仍在工作区 datasets/。
DEFAULT_ROOT = PROJECT_ROOT.parent


def default_root() -> Path:
    """项目共同路径根（包含 demiwtg/ 和 datasets/），不检查存在性。"""
    return DEFAULT_ROOT


def resolve_root(explicit: str | os.PathLike | None = None) -> Path:
    """解析数据根：显式参数 > 环境变量 > 项目默认。

    返回绝对路径；路径存在性与写权限由调用方在使用时校验。
    """
    if explicit is not None:
        chosen = Path(explicit)
    else:
        from_env = os.environ.get(DATASETS_ROOT_ENV)
        chosen = Path(from_env) if from_env else DEFAULT_ROOT
    expanded = Path(os.path.expanduser(str(chosen)))
    if not expanded.is_absolute():
        raise ValueError(
            f"datasets root must be absolute: {chosen}",
        )
    return expanded



__all__ = ["DATASETS_ROOT_ENV", "DEFAULT_ROOT", "default_root", "resolve_root"]


# Fixed snapshots retained after the authorized 2026-09-24 T2I cleanup.
# Readers never follow table heads.
HISTORICAL_EVIDENCE = {'dataset_id': 'datasets/retained_artifacts__t2i_cleanup_20260924', 'store_id': 'local', 'relative_uri': 'datasets/retained_artifacts__t2i_cleanup_20260924.lance', 'lance_version': 1, 'schema_name': 'named_artifacts', 'schema_version': 'v1', 'schema_hash': 'sha256:76e97d363407f9ddf7a27f3a762825d383d2d0ca88f21e99f8c69b63582be24a', 'row_count': 14424, 'content_digest': None}

# Standalone fixed T2I V1 baseline, including every source and generated image.
T2I_V1_EVIDENCE = {'dataset_id': 'demiwtg/benchmark/t2i/v1/datasets/bench200_artifacts', 'store_id': 'local', 'relative_uri': 'demiwtg/benchmark/t2i/v1/datasets/bench200_artifacts.lance', 'lance_version': 1, 'schema_name': 'named_artifacts', 'schema_version': 'v1', 'schema_hash': 'sha256:76e97d363407f9ddf7a27f3a762825d383d2d0ca88f21e99f8c69b63582be24a', 'row_count': 1225, 'content_digest': None}


def historical_evidence(root=None):
    from demiflow.lance.artifacts import ArtifactSet
    if HISTORICAL_EVIDENCE is None:
        raise ValueError('Historical evidence preservation is not yet committed')
    return ArtifactSet(resolve_root(root), HISTORICAL_EVIDENCE)


def evidence_key(value):
    """Interpret recorded historical locators as evidence IDs, never local files."""
    value = str(value)
    sessions = '/.codex-home/sessions/'
    if sessions in value:
        return 'judge_sessions/' + value.split(sessions, 1)[1]
    for prefix in (str(PROJECT_ROOT)+'/', '/tank/demiwtg/'):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    for branch in ('benchmark/t2i', 'benchmark/edit'):
        if value.startswith(branch+'/') and not value.startswith((branch+'/v1/',branch+'/v2/')):
            value = branch+'/v1/'+value[len(branch)+1:]
    from demiflow.lance.artifacts import logical_path
    return logical_path(value)


def historical_input(value):
    """CLI explicit evidence: ID or external file import; no existence fallback."""
    if str(value).startswith('evidence:'):
        return historical_evidence().reference(str(value)[len('evidence:'):])
    return Path(value)
