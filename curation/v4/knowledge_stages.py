"""Compatibility import; implementation in curation.v4.ops.knowledge_stages."""
import sys
from curation.v4.ops import knowledge_stages as _implementation
sys.modules[__name__] = _implementation
