import sys as _s
from pathlib import Path as _P
_R = _P(__file__).resolve().parents[2]
_s.path.insert(0, str(_R))
from taxonomy.llm_common import *  # noqa
