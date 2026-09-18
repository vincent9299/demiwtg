import sys as _s
from pathlib import Path as _P
_R = _P(__file__).resolve().parents[2]
_s.path.insert(0, str(_R))
from taxonomy.mount_map import *  # noqa
from taxonomy.mount_map import load_mount_map  # noqa: F401
