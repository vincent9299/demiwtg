"""来源子包：导入各适配器以触发注册。"""

from .base import SourceAdapter, get_adapter, register
from . import wikimedia  # 注册 WikimediaAdapter
from . import stubs       # 注册 M2–M4 占位适配器

__all__ = ["SourceAdapter", "get_adapter", "register", "wikimedia", "stubs"]
