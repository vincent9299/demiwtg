"""来源子包：导入各适配器以触发注册。"""

from .base import SourceAdapter, get_adapter, register
from . import wikimedia  # 注册 WikimediaAdapter / WikimediaZhAdapter
from . import inaturalist  # 注册 INaturalistAdapter（M3）
from . import baidu       # 注册 BaiduAdapter（未授权中文源）
# openverse / safebooru 已改写为 spec 驱动（collect/specs/*.json，见 registry.py）
from . import scrapers    # 注册 必应/360/搜狗/百度百科/豆瓣（未授权中文爬虫）
from . import cn_web       # 注册 央视/搜狗图库/站酷/中新/人民/花瓣/堆糖/知乎/新浪/搜狐（未授权中文网页爬虫）
from . import coco        # 注册 CocoAdapter（方案A：COCO 官方标注，按类别检索）
from . import hf_dataset  # 注册 hf_coco / hf_laion（方案B：HF datasets-server 流式）
from . import booru       # 注册 DanbooruAdapter / GelbooruAdapter（safebooru 同源族）
from . import fandom      # 注册 FandomAdapter（分站 MediaWiki 检索，西方 IP wiki 农场）
from . import bilibili    # 注册 BilibiliAdapter（未授权中文源，相簿 wbi 签名检索）

__all__ = [
    "SourceAdapter", "get_adapter", "register",
    "wikimedia", "baidu", "scrapers", "cn_web",
    "coco", "hf_dataset", "booru", "fandom", "bilibili",
]
