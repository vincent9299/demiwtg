"""数据根与存储位置解析。

“独立”首先是解除代码与数据的位置耦合：默认根是工作区 ``datasets/``，
部署时通过 ``DEMIWTG_DATASETS_ROOT`` 或显式参数指向仓库外目录，业务代码
不感知具体位置；记录中只保存相对位置（见 refs.DatasetRef）。
"""
from __future__ import annotations

import os
from pathlib import Path

DATASETS_ROOT_ENV = "DEMIWTG_DATASETS_ROOT"

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT
# 2026-09-20 用户指令：datasets 迁至项目顶层（仓库外），所有数据入此根分层管理。
DEFAULT_ROOT = PROJECT_ROOT.parent / "datasets"


def default_root() -> Path:
    """项目默认数据根（工作区 ``datasets/``），不检查存在性。"""
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
