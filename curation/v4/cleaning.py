"""Compatibility import; implementation lives in curation.v4.ops.cleaning."""
import sys
from curation.v4.ops import cleaning as _implementation
sys.modules[__name__] = _implementation
