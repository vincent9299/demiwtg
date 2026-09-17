"""Compatibility import; implementation lives in curation.v4.ops.passage_selection."""
import sys
from curation.v4.ops import passage_selection as _implementation
sys.modules[__name__] = _implementation
