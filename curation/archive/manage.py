"""Archive historical curation code and verify relocation without touching results.

Run from repo root: python -m curation.archive.manage migrate|verify
"""
from pathlib import Path
import ast
import hashlib
import json
import os
import re
import tarfile
import sys

ROOT=Path(__file__).resolve().parents[2]
CUR=ROOT/'curation'
RUN=ROOT/'state/curation/archive_reorganization_v1'
KEEP={'__init__.py','DESIGN.md','blob_presence.py','common.py','image_preannotate.py',
      'image_supervisor.py','run_image_pipeline.sh','image_preannotation_preview.ipynb',
      'test_image_preannotate.py','test_image_supervisor.py'}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def mapping():
    out={}
    for p in CUR.iterdir():
        if p.is_file() and p.name not in KEEP:out[p]=CUR/'archive/pre_v1'/p.name
    for p in (CUR/'knowledge_application_v1').iterdir():
        if not p.is_file():continue
        if p.name.startswith('version3'):group='v3'
        elif p.name.startswith('expansion20') or p.name=='new_corpus_evidence.py':group='v2'
        elif p.name.startswith(('pilot_review','nonobject','scene_cases')):group='v1'
        else:group='shared'
        out[p]=CUR/'archive'/group/p.name
    for p in (CUR/'_archived').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            out[p]=CUR/'archive/legacy_tools'/p.relative_to(CUR/'_archived')
    return out

def migrate():
    RUN.mkdir(parents=True,exist_ok=True)
    assert not (RUN/'manifest.json').exists(), 'Migration already recorded; use verify'
    moves=mapping()
    assert len(set(moves.values()))==len(moves)
    records=[{'old':str(p.relative_to(ROOT)),'new':str(q.relative_to(ROOT)),'before_sha256':sha(p)} for p,q in moves.items()]
    with tarfile.open(RUN/'original_code.tar.gz','w:gz') as tar:
        for p in moves:tar.add(p,arcname=str(p.relative_to(ROOT)),recursive=False)
    (RUN/'manifest.json').write_text(json.dumps({'status':'prepared','files':records},ensure_ascii=False,indent=2))
    for p,q in moves.items():
        assert not q.exists(),q
        q.parent.mkdir(parents=True,exist_ok=True);p.rename(q)
    # Historical import names remain reachable without putting experiment files at root.
    compat=CUR/'archive/compat/knowledge_application_v1';compat.mkdir(parents=True,exist_ok=True)
    for r in records:
        if r['old'].startswith('curation/knowledge_application_v1/'):
            target=ROOT/r['new'];(compat/target.name).symlink_to(os.path.relpath(target,compat))
    old=CUR/'knowledge_application_v1'
    # Cached bytecode is disposable, but leave it in the archive for provenance.
    if (old/'__pycache__').exists():(old/'__pycache__').rename(compat/'__pycache__')
    old.rmdir();old.symlink_to('archive/compat/knowledge_application_v1',target_is_directory=True)
    legacy=CUR/'_archived'
    if (legacy/'__pycache__').exists():(legacy/'__pycache__').rename(CUR/'archive/legacy_tools/__pycache__')
    # Empty nested directories may remain; retain the old directory as a compatibility link.
    for p in sorted(legacy.rglob('*'),reverse=True):
        if p.is_dir():p.rmdir()
    legacy.rmdir();legacy.symlink_to('archive/legacy_tools',target_is_directory=True)
    for r in records:
        p=ROOT/r['new']
        if p.suffix!='.py' or '/legacy_tools/' in r['new']:continue
        text=p.read_text();tree=ast.parse(text)
        end=0
        if tree.body and isinstance(tree.body[0],ast.Expr) and isinstance(tree.body[0].value,ast.Constant) and isinstance(tree.body[0].value.value,str):end=tree.body[0].end_lineno
        for node in tree.body:
            if isinstance(node,ast.ImportFrom) and node.module=='__future__':end=node.end_lineno
        lines=text.splitlines(keepends=True)
        boot='\n# Archive relocation: resolve the project, not a fixed directory depth.\nfrom pathlib import Path as _ArchivePath\nimport sys as _archive_sys\n_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())\n_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))\n'
        ka=r['old'].startswith('curation/knowledge_application_v1/')
        if ka:boot+='_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))\n'
        text=''.join(lines[:end])+boot+''.join(lines[end:])
        text=re.sub(r'Path\(__file__\)\.resolve\(\)\.parents\[[12]\]', '_ARCHIVE_ROOT',text)
        # Executable/helper references must survive physical splitting by version.
        if ka:
            text=text.replace("Path(__file__).with_name('gemini_runner.py')","(_ARCHIVE_ROOT/'curation/archive/shared/gemini_runner.py')")
            text=text.replace("Path(__file__).with_name('bagel_runner.py')","(_ARCHIVE_ROOT/'curation/archive/shared/bagel_runner.py')")
        else:
            for name in ['probe_bagel.py','calibration.ipynb','trial_questions.ipynb','reassessment.ipynb']:
                text=text.replace('curation/'+name,'curation/archive/pre_v1/'+name)
        p.write_text(text)
    for r in records:r['after_sha256']=sha(ROOT/r['new'])
    (RUN/'manifest.json').write_text(json.dumps({'status':'relocated','files':records},ensure_ascii=False,indent=2))
    print('Archived',len(records),'files; originals stored in',RUN/'original_code.tar.gz')

def verify():
    doc=json.loads((RUN/'manifest.json').read_text())
    with tarfile.open(RUN/'original_code.tar.gz') as tar:
        for r in doc['files']:
            assert hashlib.sha256(tar.extractfile(r['old']).read()).hexdigest()==r['before_sha256'],r['old']
            assert sha(ROOT/r['new'])==r['after_sha256'],r['new']
    print('Verified',len(doc['files']),'archived files and original snapshots')

if __name__=='__main__':
    {'migrate':migrate,'verify':verify}[sys.argv[1]]()
