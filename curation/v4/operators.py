"""Compatibility import; implementation lives in curation.v4.ops.operators."""
import sys
from curation.v4.ops import operators as _implementation
sys.modules[__name__] = _implementation
