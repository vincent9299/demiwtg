"""Compatibility import; implementation lives in curation.v4.ops.source_markup."""
import sys
from curation.v4.ops import source_markup as _implementation
sys.modules[__name__] = _implementation
