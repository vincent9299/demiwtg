"""Curation: active V4 plus compatibility imports for archived pilot notebooks."""
from pathlib import Path as _Path
# Historical `curation.core/pipeline/...` imports resolve here; V4 never uses these.
__path__.append(str(_Path(__file__).parent / 'archive/pre_v1'))
