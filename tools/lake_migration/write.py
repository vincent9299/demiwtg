"""Lance 表写入公共入口：业务 schema 查表 ＋ 平台 write_registered_table。

原子提交／回执／重放／登记由 ``demiflow.lance.registry.write_registered_table``
实现（schema 显式传入，平台不持有业务 schema 集合）；本函数只做项目侧
两件事：按 ``schema_name`` 查 ``tools.lake_migration.schemas`` 业务 schema、提供
项目 schema 版本。行工厂必须是零参可重入迭代器（重放时不执行）。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from demiflow.lance.registry import write_registered_table as _platform_write


def write_table(
    datasets_root, relative_uri: str, *, schema_name: str,
    rows_factory: Callable[[], Iterator[Mapping]],
    fingerprint: str,
    storage_options: Mapping[str, str] | None = None,
    max_rows_per_batch: int = 8192,
    register: bool = True,
) -> tuple["object", int, bool]:
    """把 ``rows_factory`` 的行写为一张 Lance 表，登记并返回引用、行数与是否重放。

    重放（目标已有同 fingerprint 提交）时行工厂不执行——调用方需要源侧
    统计（对账、坏行计数）时须自行补一遍纯统计扫描。

    ``fingerprint`` 必须由输入身份派生（源文件哈希＋选择条件＋schema 版本），
    同位置同 fingerprint 重放不重执行；输入变化必须换 fingerprint 或新位置。
    """
    from tools.lake_migration.schemas import ALL_SCHEMAS, SCHEMA_VERSION

    return _platform_write(
        datasets_root, relative_uri,
        schema=ALL_SCHEMAS[schema_name],
        schema_name=schema_name,
        schema_version=SCHEMA_VERSION,
        rows_factory=rows_factory,
        fingerprint=fingerprint,
        storage_options=storage_options,
        max_rows_per_batch=max_rows_per_batch,
        register=register,
    )


__all__ = ["write_table"]
