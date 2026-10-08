import hashlib
import threading

from tools.lake_migration.unify_image_objects import move_with_alias, consolidate


def test_atomic_move_preserves_old_readers_and_resumes(tmp_path):
    source, destination = tmp_path/'old', tmp_path/'new'
    source.mkdir(); (source/'image').write_bytes(b'original')
    stop = threading.Event(); errors = []
    def reader():
        while not stop.is_set():
            try:
                assert (source/'image').read_bytes() == b'original'
            except BaseException as exc:
                errors.append(exc)
    thread = threading.Thread(target=reader); thread.start()
    try:
        assert move_with_alias(source, destination) == 'moved_with_atomic_alias'
        assert move_with_alias(source, destination) == 'already_moved'
    finally:
        stop.set(); thread.join()
    assert not errors and source.is_symlink() and not destination.is_symlink()


def test_consolidation_preserves_inode_and_verified_duplicates(tmp_path):
    source, objects = tmp_path/'downloads', tmp_path/'objects'
    source.mkdir(); objects.mkdir()
    items = []
    for i, value in enumerate([b'one', b'two']):
        sha = hashlib.sha256(value).hexdigest()
        (source/sha[:2]).mkdir(exist_ok=True)
        path = source/sha[:2]/(sha+'.png'); path.write_bytes(value)
        if i:
            (objects/sha[:2]).mkdir(); (objects/sha[:2]/sha).write_bytes(value)
        items.append((path, sha, value))
    r = consolidate(source, objects, max_files=10, report_path=tmp_path/'report.json')
    assert r['complete'] and r['hardlinked'] == r['verified_duplicates_consolidated'] == 1
    for path, sha, value in items:
        assert path.read_bytes() == value
        assert path.stat().st_ino == (objects/sha[:2]/sha).stat().st_ino
    r = consolidate(source, objects, max_files=10, report_path=tmp_path/'report2.json')
    assert r['already_shared'] == 2 and r['complete']


def test_parallel_migration_admits_global_file_budget_before_linking(tmp_path):
    import pytest
    from tools.lake_migration.unify_image_objects import consolidate_parallel
    source=tmp_path/'source';objects=tmp_path/'objects';source.mkdir();objects.mkdir()
    originals=[]
    for i in range(16):
        value=str(i).encode();sha=hashlib.sha256(value).hexdigest()
        (source/sha[:2]).mkdir(exist_ok=True);p=source/sha[:2]/sha;p.write_bytes(value);originals.append(p)
    with pytest.raises(ValueError,match='file bound'):
        consolidate_parallel(source,objects,max_files=1,report_path=tmp_path/'report.json',workers=4)
    assert len([p for p in objects.glob('*/*') if p.is_file()])==1
    assert all(p.exists() for p in originals)
    report=consolidate_parallel(source,objects,max_files=16,report_path=tmp_path/'resumed.json',workers=4)
    assert report['complete'] and report['files_seen']==16 and report['already_shared']==1
