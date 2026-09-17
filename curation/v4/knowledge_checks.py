"""Compatibility import; implementation lives in curation.v4.ops.knowledge_checks."""
import sys
from curation.v4.ops import knowledge_checks as _implementation
sys.modules[__name__] = _implementation
