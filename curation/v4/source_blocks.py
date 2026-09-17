"""Compatibility import; implementation lives in curation.v4.ops.source_blocks."""
import sys
from curation.v4.ops import source_blocks as _implementation
sys.modules[__name__] = _implementation
