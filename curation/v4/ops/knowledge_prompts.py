"""Versioned machine-candidate protocols, independent of benchmark scoring."""

from curation.v4.ops.prompt_loader import load_instruction
SYSTEM = load_instruction("system")
IDENTITY = load_instruction("identity")
EXTRACT = load_instruction("extract")
CONSOLIDATE = load_instruction("consolidate")
EVIDENCE = load_instruction("evidence")

FIDELITY = load_instruction("fidelity")

