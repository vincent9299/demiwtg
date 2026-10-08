"""Move the two known image stores under preparation, preserving frozen URIs.

No table/model/source metadata changes. Atomic directory/link exchange is
optional; --preserve-directories uses hard links when the filesystem does not
support exchange. Old paths stay continuously readable. Only verified identical
duplicates converge. Scans stream entries with 1 MiB hash buffers, at most two
open image files per worker, 16 workers and a bounded exception sample. Resume
uses inode identity, without copying payload bytes.
"""
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import time


def exchange(left, right):
    fn = ctypes.CDLL(None, use_errno=True).renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(-100, os.fsencode(left), -100, os.fsencode(right), 2):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def move_with_alias(source, destination):
    if source.is_symlink():
        if source.resolve(strict=True) != destination.resolve(strict=True):
            raise ValueError('Existing alias points elsewhere: ' + str(source))
        return 'already_moved'
    if not source.is_dir() or os.path.lexists(destination):
        raise ValueError('Migration requires existing source and absent destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError('Atomic same-filesystem migration required')
    before = source.stat()
    destination.symlink_to(destination, target_is_directory=True)
    try:
        exchange(source, destination)
    except BaseException:
        if destination.is_symlink():
            destination.unlink()
        raise
    assert source.is_symlink() and destination.is_dir() and not destination.is_symlink()
    assert destination.stat().st_ino == before.st_ino
    for parent in {source.parent, destination.parent}:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return 'moved_with_atomic_alias'


def file_hash(path, max_bytes=512*1024*1024, timeout_s=60):
    before = path.stat()
    if before.st_size > max_bytes:
        raise ValueError('Duplicate verification file exceeds byte budget')
    digest = hashlib.sha256()
    start = time.monotonic()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024*1024):
            size += len(chunk)
            if size > max_bytes or time.monotonic() - start > timeout_s:
                raise ValueError('Duplicate verification exceeded resource budget')
            digest.update(chunk)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('File changed during verification')
    return digest.hexdigest()


def consolidate(downloads, objects, *, max_files, report_path, progress_every=10000, shard_filter=None, admit=None):
    stats = {'files_seen': 0, 'hardlinked': 0, 'already_shared': 0,
             'verified_duplicates_consolidated': 0, 'unexpected': 0, 'errors': 0,
             'error_examples': [], 'new_payload_bytes': 0, 'logical_source_bytes': 0}
    start = time.monotonic()
    def save():
        result = {**stats, 'elapsed_s': time.monotonic()-start, 'objects': str(objects),
                  'downloads_alias_layout': str(downloads), 'complete': False}
        temp = report_path.with_suffix('.pending')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        temp.replace(report_path)
    with os.scandir(downloads) as shards:
        for shard in shards:
            if shard_filter is not None and shard.name != shard_filter:
                continue
            if not re.fullmatch('[0-9a-f]{2}', shard.name) or not shard.is_dir(follow_symlinks=False):
                stats['unexpected'] += 1
                continue
            target_dir = objects / shard.name
            target_dir.mkdir(parents=True, exist_ok=True)
            with os.scandir(shard.path) as entries:
                for entry in entries:
                    if stats['files_seen'] >= max_files:
                        save()
                        raise ValueError('Migration exceeds explicit file bound; no truncation')
                    if admit is not None:
                        try:admit()
                        except ValueError:
                            save()
                            raise
                    stats['files_seen'] += 1
                    match = re.fullmatch('([0-9a-f]{64})(?:\\.([A-Za-z0-9]{1,12}))?', entry.name)
                    if not match or match[1][:2] != shard.name or not entry.is_file(follow_symlinks=False):
                        stats['unexpected'] += 1
                        continue
                    sha = match[1]
                    source = Path(entry.path)
                    target = target_dir / sha
                    try:
                        before = source.stat()
                        stats['logical_source_bytes'] += before.st_size
                        try:
                            os.link(source, target)
                            stats['hardlinked'] += 1
                        except FileExistsError:
                            after = target.stat()
                            if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
                                stats['already_shared'] += 1
                            else:
                                if before.st_size != after.st_size or file_hash(source) != sha or file_hash(target) != sha:
                                    raise ValueError('Content conflict; originals retained')
                                # Replace only the redundant name, after full
                                # equality verification. Existing open FDs stay valid.
                                tmp = source.with_name('.image-alias-'+str(os.getpid()))
                                os.link(target, tmp)
                                try:
                                    os.replace(tmp, source)
                                finally:
                                    tmp.unlink(missing_ok=True)
                                stats['verified_duplicates_consolidated'] += 1
                        assert source.stat().st_ino == target.stat().st_ino
                    except (OSError, ValueError) as exc:
                        stats['errors'] += 1
                        if len(stats['error_examples']) < 32:
                            stats['error_examples'].append({'path': str(source), 'reason': str(exc)[:512]})
                    if stats['files_seen'] % progress_every == 0:
                        save()
                        print({k: v for k, v in stats.items() if k != 'error_examples'}, flush=True)
            fd = os.open(target_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    save()
    result = json.loads(report_path.read_text())
    result['complete'] = result['errors'] == result['unexpected'] == 0
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


def consolidate_parallel(downloads, objects, *, max_files, report_path, workers=16):
    """At most 256 known SHA shards and 16 workers; two hash FDs per worker."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    if not 1 <= workers <= 16:
        raise ValueError('Invalid migration worker bound')
    shards = []
    with os.scandir(downloads) as entries:
        for entry in entries:
            if not re.fullmatch('[0-9a-f]{2}', entry.name) or not entry.is_dir(follow_symlinks=False):
                raise ValueError('Unexpected top-level object entry: ' + entry.name)
            shards.append(entry.name)
            assert len(shards) <= 256
    counters = ['files_seen', 'hardlinked', 'already_shared', 'verified_duplicates_consolidated',
                'unexpected', 'errors', 'new_payload_bytes', 'logical_source_bytes']
    result = {k: 0 for k in counters}
    result.update(complete=False, completed_shards=0, total_shards=len(shards), workers=workers,
                  objects=str(objects), source=str(downloads), error_examples=[])
    start = time.monotonic()
    admission = Lock(); admitted = 0
    def admit():
        nonlocal admitted
        with admission:
            if admitted >= max_files:
                raise ValueError('Migration aggregate file bound exceeded; originals preserved')
            admitted += 1
    detail = report_path.parent/(report_path.stem+'_shards'); detail.mkdir(exist_ok=True)
    def work(shard):
        return consolidate(downloads, objects, max_files=min(max_files, 100000),
                           report_path=detail/(shard+'.json'), progress_every=100000,
                           shard_filter=shard, admit=admit)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # The submitted iterable is explicitly bounded to the 256 hash shards.
        for item in pool.map(work, sorted(shards)):
            for key in counters:
                result[key] += item[key]
            result['completed_shards'] += 1
            result['elapsed_s'] = time.monotonic()-start
            result['error_examples'] = (result['error_examples'] + item['error_examples'])[:32]
            temp = report_path.with_suffix('.pending')
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n'); temp.replace(report_path)
            print({k: result[k] for k in ['source','completed_shards','files_seen','hardlinked','already_shared','errors','elapsed_s']}, flush=True)
            if result['files_seen'] > max_files:
                raise ValueError('Migration aggregate file bound exceeded; originals preserved')
    result['complete'] = result['errors'] == result['unexpected'] == 0
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--max-files', type=int, default=600000)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--preserve-directories', action='store_true',
                        help='Create canonical hard links when atomic directory exchange is unsupported')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    destination = root / 'demiwtg/preparation/datasets/images'
    control = root / '_demiflow/image_library_unification_20261003'
    if not args.execute:
        print({'objects': str(root/'objects'), 'downloads': str(root/'demiwtg/collect/download/blobs'),
               'destination': str(destination), 'max_files': args.max_files})
        return
    control.mkdir(parents=True, exist_ok=True)
    from demiflow.execution.artifacts import run_lock
    with run_lock(control):
        if args.preserve_directories:
            objects = destination/'objects'; objects.mkdir(parents=True, exist_ok=True)
            sources = [('catalog', root/'objects', 3000000),
                       ('downloads', root/'demiwtg/collect/download/blobs', args.max_files)]
            reports = []
            for name, source, bound in sources:
                assert source.stat().st_dev == objects.stat().st_dev
                reports.append(consolidate_parallel(source, objects, max_files=bound,
                    report_path=control/(name+'_progress.json')))
            result = {'complete': all(r['complete'] for r in reports), 'sources': reports,
                'canonical_objects': str(objects), 'old_paths': 'Compatibility hard links; identical inodes, no duplicate payload bytes',
                'atomic_exchange': 'Unsupported by shared filesystem; source directories retained continuously readable'}
            (control/'consolidated.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        # Prove exchange semantics on this filesystem before touching stores.
        probe = control / 'exchange_probe'; probe.mkdir(exist_ok=True)
        left, right = probe/'left', probe/'right'
        left.mkdir(); (left/'proof').write_text('preserved')
        move_with_alias(left, right)
        assert (left/'proof').read_text() == (right/'proof').read_text() == 'preserved'
        left.unlink(); (right/'proof').unlink(); right.rmdir(); probe.rmdir()
        moved = {'objects': move_with_alias(root/'objects', destination/'objects'),
                 'downloads': move_with_alias(root/'demiwtg/collect/download/blobs', destination/'download_path_aliases')}
        (control/'locations.json').write_text(json.dumps({'root':str(root), 'destination':str(destination),
            'moves':moved, 'old_paths':'atomic symbolic aliases; frozen URI strings unchanged'},indent=2)+'\n')
        result = consolidate(destination/'download_path_aliases', destination/'objects',
                             max_files=args.max_files, report_path=control/'progress.json')
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
