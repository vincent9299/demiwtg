"""Exact-byte readers for retained baseline and design evidence."""
from functools import lru_cache
from project import historical_evidence, evidence_key, resolve_root

@lru_cache(maxsize=4)
def _store(root):return historical_evidence(root)

def store():return _store(str(resolve_root()))
def read_bytes(path):return store().read_bytes(evidence_key(path))
def read_text(path, encoding='utf-8'):return read_bytes(path).decode(encoding)
def exists(path):return evidence_key(path) in store().entries
