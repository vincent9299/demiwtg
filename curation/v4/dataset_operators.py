"""Compatibility import; implementation lives in curation.v4.ops.dataset_operators."""
import sys
from curation.v4.ops import dataset_operators as _implementation
sys.modules[__name__] = _implementation
