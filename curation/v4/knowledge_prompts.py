"""Compatibility import; implementation lives in curation.v4.ops.knowledge_prompts."""
import sys
from curation.v4.ops import knowledge_prompts as _implementation
sys.modules[__name__] = _implementation
