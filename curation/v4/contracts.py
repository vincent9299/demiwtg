"""Run-local material contracts, identity registry and resumable immutable outputs."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

ROOT=Path(__file__).resolve().parents[2]
SCHEMA='curation-v4-materials/1'

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2).encode()+b'\n'
def digest(value):return hashlib.sha256(value if isinstance(value,bytes) else encoded(value)).hexdigest()
def read(path):return json.loads(Path(path).read_text())

def immutable(path,value):
    path=Path(path);data=encoded(value);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=data:raise ValueError(f'Existing result differs; use a new run: {path}')
        return False
    # Callers hold the run lock. Atomic rename prevents partial JSON on interruption.
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(path);return True

@contextmanager
def run_lock(run):
    run=Path(run);run.mkdir(parents=True,exist_ok=True)
    with (run/'.lock').open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another writer holds this run') from None
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def snapshot(path):
    path=Path(path).resolve()
    if not path.exists():return {'path':str(path),'exists':False}
    s=path.stat()
    return {'path':str(path),'exists':True,'size':s.st_size,'mtime_ns':s.st_mtime_ns,'inode':s.st_ino}

def source_code():
    root = Path(__file__).parent
    return {str(p.relative_to(root)):p.read_text() for p in sorted(root.rglob('*'))
            if p.suffix in {'.py', '.yaml', '.txt'} and not p.name.startswith('test_') and '__pycache__' not in p.parts}

def code_fingerprint():
    return digest(source_code())

def runtime_version():
    import importlib.metadata
    import demiflow
    directory=Path(demiflow.__file__).parent
    return {'trafilatura':importlib.metadata.version('trafilatura'), 'mwparserfromhell':importlib.metadata.version('mwparserfromhell'),
            'demiflow':importlib.metadata.version('demiflow'),
            'demiflow_python_sha256':digest({str(p.relative_to(directory)):digest(p.read_bytes()) for p in sorted(directory.rglob('*.py'))})}


class IdentityRegistry:
    """Internal UUIDs persist independently of labels/QIDs; no name-based auto-merge.

    Each source identity initially remains separate. Verified cross-source identity
    merge/split requires a later explicit migration operation, not a lookup guess.
    """
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=30)
        self.db.execute('CREATE TABLE IF NOT EXISTS identities (source_key TEXT PRIMARY KEY, concept_id TEXT UNIQUE NOT NULL)')
    def get(self,request):
        if request.get('kind') not in ('qid','legacy'):raise ValueError('kind must be qid or legacy')
        value=request.get('value')
        if not isinstance(value,str) or not value.strip():raise ValueError('nonempty identity value required')
        if request['kind']=='qid' and not (value.startswith('Q') and value[1:].isdigit()):raise ValueError('invalid QID')
        key=request['kind']+':'+value
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO identities VALUES (?,?)',(key,'concept:'+str(uuid.uuid4())))
        return self.db.execute('SELECT concept_id FROM identities WHERE source_key=?',(key,)).fetchone()[0]
    def close(self):self.db.close()
