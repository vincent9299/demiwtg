"""Audited, resumable production export of table-owned objects.

Export is read-only for source snapshots. A durable SQLite journal records the
SHA, size and final-file verification before advancing each batch checkpoint.
Cutover and retirement are separate actions after export reconciliation.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import lance
from demiflow.objects import LocalObjectStore


def journal(path):
    db = sqlite3.connect(path, timeout=120)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS objects(sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL,
        uri TEXT NOT NULL, mtime_ns INTEGER NOT NULL, verified_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS progress(source TEXT, version INTEGER, column_name TEXT,
        rows_done INTEGER, total_rows INTEGER, complete INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(source,version,column_name));
      CREATE TABLE IF NOT EXISTS failures(source TEXT, version INTEGER, column_name TEXT,
        row_id TEXT, sha256 TEXT, error TEXT, PRIMARY KEY(source,version,column_name,row_id));
      CREATE TABLE IF NOT EXISTS absent_objects(sha256 TEXT PRIMARY KEY, source TEXT NOT NULL,
        version INTEGER NOT NULL, reason TEXT NOT NULL);
    ''')
    return db


def export_snapshot(root, source, version, *, column='data', workers=24, batch_size=256,
                    audit=None, limit=None, offset=0):
    root = Path(root).resolve()
    audit = Path(audit or root / '_demiflow/image_object_cutover_20260929')
    audit.mkdir(parents=True, exist_ok=True)
    database = journal(audit / 'objects.sqlite')
    uri = str((root / source).resolve())
    dataset = lance.dataset(uri, version=version)
    if column not in dataset.schema.names:
        raise ValueError('Source lacks requested Blob column')
    total = min(dataset.count_rows()-offset, limit) if limit else dataset.count_rows()-offset
    if offset < 0 or total < 0:
        raise ValueError('Invalid export range')
    progress_column = column if not offset and limit is None else f'{column}:rows:{offset}:{total}'
    prior = database.execute('SELECT rows_done,total_rows,complete FROM progress WHERE source=? AND version=? AND column_name=?',
                             (uri, version, progress_column)).fetchone()
    start = prior[0] if prior else 0
    if prior and prior[1] != total:
        raise ValueError('Export scope changed; use a separate audit directory')
    if prior and prior[2]:
        return {'source': uri, 'version': version, 'rows': total, 'complete': True, 'reused': True}
    store = LocalObjectStore(root / 'objects')
    store.directory.mkdir(parents=True, exist_ok=True)
    for prefix in range(256):
        (store.directory / f'{prefix:02x}').mkdir(exist_ok=True)
    descriptor = os.open(store.directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    columns = [c for c in ('sha256','byte_size','availability') if c in dataset.schema.names]
    if 'sha256' not in columns:
        raise ValueError('Image export requires an explicit sha256 column')
    started = last = time.monotonic()
    exported = reused = byte_count = errors = 0

    def publish(pair):
        row, blob = pair
        try:
            if blob is None:
                if row.get('availability') == 'available' or (row.get('byte_size') or 0) > 0:
                    raise ValueError('Available image has a null Blob')
                return ('missing', row, None)
            size = blob.size()
            if row.get('byte_size') is not None and row['byte_size'] != size:
                raise ValueError(f'Blob size {size} differs from metadata {row["byte_size"]}')
            ref = store.reference(row['sha256'])
            path = store.directory / ref.sha256[:2] / ref.sha256
            # Batch the directory fsync below, but fsync every file before linking.
            # Temporary names stay in their SHA shard instead of contending on
            # one shared parent directory for millions of object publications.
            fd, temporary = tempfile.mkstemp(prefix='.export-', dir=path.parent)
            try:
                digest = hashlib.sha256()
                with os.fdopen(fd, 'wb') as output:
                    while chunk := blob.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if digest.hexdigest() != ref.sha256:
                    raise ValueError('Source object SHA256 mismatch')
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass  # Full final-file verification below rejects corruption.
            finally:
                os.unlink(temporary)
            if ref.verify() != size:
                raise ValueError('Published object size differs')
            return ('saved', row, (ref.sha256, size, ref.uri, path.stat().st_mtime_ns, time.time()))
        except Exception as exc:
            return ('error', row, f'{type(exc).__name__}: {exc}')
        finally:
            if blob is not None:
                blob.close()

    scanner = dataset.scanner(columns=columns, with_row_id=True, offset=offset+start, limit=total-start,
                              batch_size=batch_size, batch_readahead=1, fragment_readahead=1)
    done = start
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch in scanner.to_batches():
            rows = batch.to_pylist()
            keys = list({r['sha256'] for r in rows})
            prior_objects = {r[0]: r for r in database.execute(
                'SELECT sha256,size,uri,mtime_ns FROM objects WHERE sha256 IN (' + ','.join('?'*len(keys)) + ')', keys)}
            pending = []
            for row in rows:
                old = prior_objects.get(row['sha256'])
                path = store.directory / row['sha256'][:2] / row['sha256']
                stat = path.stat() if old and path.exists() else None
                if (old and stat and stat.st_size == old[1] and stat.st_mtime_ns == old[3]
                        and (row.get('byte_size') is None or row['byte_size'] == old[1])):
                    reused += 1
                else:
                    pending.append(row)
            if pending:
                blobs = dataset.take_blobs(column, ids=[r['_rowid'] for r in pending])
                results = list(pool.map(publish, zip(pending, blobs)))
                prefixes = {value[0][:2] for status, row, value in results if status == 'saved'}
                for prefix in prefixes:
                    descriptor = os.open(store.directory / prefix, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                for status, row, value in results:
                    if status == 'saved':
                        database.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?,?,?)', value)
                        byte_count += value[1]
                        exported += 1
                    elif status == 'error':
                        errors += 1
                        database.execute('INSERT OR REPLACE INTO failures VALUES(?,?,?,?,?,?)',
                            (uri, version, progress_column, str(row['_rowid']), row['sha256'], value))
                        print('[export error]', row['sha256'], value, flush=True)
            done += len(rows)
            database.execute('INSERT OR REPLACE INTO progress VALUES(?,?,?,?,?,0)', (uri,version,progress_column,done,total))
            database.commit()
            now = time.monotonic()
            if now-last >= 15 or done == total:
                print(json.dumps({'source': source, 'version': version, 'offset': offset, 'done': done, 'total': total,
                    'exported': exported, 'reused': reused, 'errors': errors,
                    'GiB': round(byte_count/2**30,3), 'rows_per_second': round((done-start)/max(now-started,.001),1),
                    'elapsed_s': round(now-started,1)}, ensure_ascii=False), flush=True)
                last = now
    failures = database.execute('SELECT count(*) FROM failures WHERE source=? AND version=? AND column_name=?',
                                (uri,version,progress_column)).fetchone()[0]
    if done != total or failures:
        database.close()
        raise ValueError(f'Export not complete: {done}/{total}, failures={failures}; source unchanged')
    database.execute('UPDATE progress SET complete=1 WHERE source=? AND version=? AND column_name=?', (uri,version,progress_column))
    database.commit()
    database.close()
    return {'source':uri,'version':version,'rows':done,'complete':True,'exported':exported,'reused':reused}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--source',required=True)
    p.add_argument('--version',type=int,required=True)
    p.add_argument('--workers',type=int,default=24)
    p.add_argument('--batch-size',type=int,default=256)
    p.add_argument('--audit',type=Path)
    p.add_argument('--limit',type=int)
    p.add_argument('--offset',type=int,default=0)
    a=p.parse_args()
    print(json.dumps(export_snapshot(a.root,a.source,a.version,workers=a.workers,
        batch_size=a.batch_size,audit=a.audit,limit=a.limit,offset=a.offset),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
