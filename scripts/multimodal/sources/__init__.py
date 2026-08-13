"""来源子包：导入各适配器以触发注册。"""

from .base import SourceAdapter, get_adapter, register
from . import wikimedia  # 注册 WikimediaAdapter / WikimediaZhAdapter
from . import inaturalist  # 注册 INaturalistAdapter（M3）
from . import stubs       # 注册 M2/M4 占位适配器（openimages / searchengine）
from . import baidu       # 注册 BaiduAdapter（未授权中文源）
from . import openverse   # 注册 OpenverseAdapter（CC 聚合，默认关）
from . import scrapers    # 注册 必应/360/搜狗/百度百科/豆瓣（未授权中文爬虫）
from . import cn_web       # 注册 央视/搜狗图库/站酷/中新/人民/花瓣/堆糖/知乎/新浪/搜狐（未授权中文网页爬虫）

__all__ = [
    "SourceAdapter", "get_adapter", "register",
    "wikimedia", "stubs", "baidu", "openverse", "scrapers", "cn_web",
]
