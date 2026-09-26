"""Fake fairseq import hook.

modelscope 1.39's model registry eagerly imports the OFA model module
(ofa_for_all_tasks), whose mmspeech submodule requires fairseq. We only use
mPLUG VQA for DPG scoring and fairseq cannot build against torch 2.6, so we
install a meta path hook that fabricates empty fairseq.* modules on import.
None of the stubbed symbols are ever executed for mPLUG; if OFA models are
ever needed, remove this hook and install real fairseq.
"""
import sys
import types


class _StubFinder:
    def find_module(self, fullname, path=None):
        if fullname == "fairseq" or fullname.startswith("fairseq."):
            return self
        return None

    def load_module(self, fullname):
        if fullname in sys.modules:
            return sys.modules[fullname]
        mod = types.ModuleType(fullname)

        def _getattr(name):
            # capitalized -> dummy class usable as a base class;
            # otherwise -> dummy callable
            if name[0].isupper():
                return type(name, (), {"__init__": lambda self, *a, **k: None})
            return lambda *a, **k: None

        mod.__getattr__ = _getattr
        mod.__path__ = []  # mark as package so submodule imports work
        sys.modules[fullname] = mod
        return mod


sys.meta_path.insert(0, _StubFinder())
