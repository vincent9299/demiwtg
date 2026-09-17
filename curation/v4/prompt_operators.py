"""Compatibility import; implementation lives in curation.v4.ops.prompt_operators."""
import sys
from curation.v4.ops import prompt_operators as _implementation
sys.modules[__name__] = _implementation
