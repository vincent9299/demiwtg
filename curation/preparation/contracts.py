"""Run-local material contracts, identity registry and resumable immutable outputs."""
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[2]
SCHEMA='curation-v4-materials/1'

from demiflow.execution.artifacts import encoded, digest, immutable, run_lock, snapshot


def read(path):
    return json.loads(Path(path).read_text())


def source_code():
    root = Path(__file__).parent.parent
    files = {str(p.relative_to(root)):p.read_text() for p in sorted(p for branch in ('preparation','benchmark','training','evaluation') for p in (root / branch).rglob('*'))
            if p.suffix in {'.py', '.yaml', '.txt'} and not p.name.startswith('test_') and not {'__pycache__','tests','reviews'} & set(p.parts)}
    files["preparation/annotation_contracts.py"] = (root / "preparation/annotation_contracts.py").read_text()
    for relative in ("project.py", "collect/assets.py", "collect/schemas.py",
                     "collect/material_schema.py", "collect/materials.py", "collect/material_writer.py",
                     "collect/concepts.py", "curation/preparation/schemas.py", "curation/preparation/images.py", "curation/preparation/image_schema.py", "curation/preparation/articles.py", "curation/preparation/publication.py", "curation/preparation/configs/image_annotation_v2.json"):
        files["../../" + relative] = (ROOT / relative).read_text()
    return files

def code_fingerprint():
    return digest(source_code())

def runtime_version():
    import importlib.metadata
    import demiflow
    directory=Path(demiflow.__file__).parent
    return {'trafilatura':importlib.metadata.version('trafilatura'), 'mwparserfromhell':importlib.metadata.version('mwparserfromhell'),
            'demiflow':importlib.metadata.version('demiflow'),
            'demiflow_python_sha256':digest({str(p.relative_to(directory)):digest(p.read_bytes()) for p in sorted(directory.rglob('*.py'))})}
