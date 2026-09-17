"""Compatibility import; implementation lives in curation.v4.ops.fidelity."""
import sys
from curation.v4.ops import fidelity as _implementation
sys.modules[__name__] = _implementation
