"""Compatibility import; implementation in curation.v4.ops.prompt_config."""
import sys
from curation.v4.ops import prompt_config as _implementation
sys.modules[__name__] = _implementation
