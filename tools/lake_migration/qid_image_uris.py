"""将已下载 QID 图片挂接为独立 URI；不下载、不复制像素、不重新选样。

一次性维护命令：verify 固定子集并逐图流式核 SHA/大小，publish 只增改引用列。
源账本含重复 SHA，使用原生增列保留每条原记录；不按 SHA merge/去重源账本。
审计表是窄元数据索引，最多与已下载子集同规模；不将全量源表载入 Python。
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import threading
import time

import lance
import pyarrow as pa
from demiflow import data
from demiflow.objects import ObjectRef
from project import resolve_root
from subset.operators.runfiles import table_record
from .blob_snapshot_rebuild import atomic_json, equal_streams
from .commit_object_cutover import exclusive, register_head

VERIFIED = pa.schema([('sha256', pa.string()), ('image_uri', pa.string()),
                      ('size_bytes', pa.int64()), ('mtime_ns', pa.int64())])


def verify_unchanged(before, after, columns):
    """merge 可改变物理行序；按业务主键对齐，再逐值比较所有未负责的列。"""
    return equal_streams(
        before.scanner(columns=columns, order_by=['sha256'], batch_size=1024).to_batches(),
        after.scanner(columns=columns, order_by=['sha256'], batch_size=1024).to_batches())


def verify_image(row, *, blobs):
    """按实际 ext 定位当前文件，流式核 SHA；缺失/损坏即失败，不发布假可用状态。"""
    sha, ext = row['sha256'], row['ext']
    if not re.fullmatch('[a-f0-9]{64}', sha or '') or not re.fullmatch('[a-zA-Z0-9]{1,10}', ext or ''):
        raise ValueError('Invalid image SHA256/ext')
    path = Path(blobs) / sha[:2] / f'{sha}.{ext}'
    before = path.stat()
    if not path.is_file() or before.st_size != row['size_bytes']:
        raise ValueError(f'Image size differs: {sha}')
    uri = path.absolute().as_uri()
    size = ObjectRef(uri, sha).verify()
    after = path.stat()
    if size != row['size_bytes'] or any(getattr(before, key) != getattr(after, key)
                                       for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')):
        raise ValueError(f'Image changed during verification: {sha}')
    return {'sha256': sha, 'image_uri': uri, 'size_bytes': size, 'mtime_ns': after.st_mtime_ns}


def verify(subset, version, blobs, audit, *, concurrency=8):
    audit = Path(audit)
    audit.mkdir(parents=True, exist_ok=True)
    receipt_path, manifest = audit / 'verified.json', audit / 'verified.lance'
    if receipt_path.exists() or manifest.exists():
        raise ValueError('Verification audit already exists; use its publish receipt or a new audit directory')
    source = lance.dataset(str(subset), version=version)
    started, lock, progress = time.monotonic(), threading.Lock(), {'rows': 0, 'bytes': 0, 'last': 0.}

    def checked(row):
        result = verify_image(row, blobs=blobs)
        with lock:
            progress['rows'] += 1
            progress['bytes'] += result['size_bytes']
            elapsed = time.monotonic() - started
            if elapsed - progress['last'] >= 20:
                print(f"verified {progress['rows']:,}/{source.count_rows():,}, "
                      f"{progress['bytes']/1e9:.2f} GB, {elapsed:.0f}s", flush=True)
                progress['last'] = elapsed
        return result

    (data.read_lance(str(subset), version=version, columns=['sha256', 'ext', 'size_bytes'], batch_size=512)
     .map_async(checked, execution='thread', concurrency=concurrency, queue_depth=concurrency*2)
     .materialize()
     .write_lance(str(manifest), mode='overwrite', schema=VERIFIED))
    receipt = {'subset': table_record(source), 'manifest': table_record(lance.dataset(str(manifest))),
               'blobs': str(Path(blobs).resolve()), 'bytes': progress['bytes'],
               'verification': 'full_sha256_and_size', 'elapsed_s': time.monotonic()-started}
    atomic_json(receipt_path, receipt)
    return receipt


def publish(source, source_version, audit, *, root=None):
    """所有核对完成后增改列；两表各自原子提交，回执保存实际版本以支持重入。"""
    root, audit, source = resolve_root(root), Path(audit), Path(source).resolve()
    evidence = json.loads((audit/'verified.json').read_text())
    manifest_ref, subset_ref = evidence['manifest'], evidence['subset']
    manifest = lance.dataset(manifest_ref['uri'], version=manifest_ref['version'])
    if table_record(manifest) != manifest_ref:
        raise ValueError('Verified manifest identity changed')
    # 仅已验证的子集建立窄索引；18M 源记录始终按 batch 扫描。
    lookup = {}
    for batch in manifest.to_batches(batch_size=4096):
        for row in batch.to_pylist():
            if row['sha256'] in lookup:
                raise ValueError('Duplicate verified SHA')
            from urllib.parse import unquote, urlsplit
            stat = Path(unquote(urlsplit(row['image_uri']).path)).stat()
            if (stat.st_size, stat.st_mtime_ns) != (row['size_bytes'], row['mtime_ns']):
                raise ValueError(f'Verified object changed: {row["sha256"]}')
            lookup[row['sha256']] = row
    print(f'Checked {len(lookup):,} verified object paths; scanning collect metadata', flush=True)
    subset = Path(subset_ref['uri'])
    receipt_path = audit/'published.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if receipt and (receipt['source_uri'] != str(source) or receipt['source_version'] != source_version):
        raise ValueError('Publish target changed')
    with exclusive(source), exclusive(subset):
        original = lance.dataset(str(source), version=source_version)
        before = lance.dataset(str(subset), version=subset_ref['version'])
        if table_record(before) != subset_ref:
            raise ValueError('Subset snapshot changed')
        head, subset_head = lance.dataset(str(source)), lance.dataset(str(subset))
        if head.version != receipt.get('source_head', source_version):
            raise ValueError('Collect head changed; inspect before publishing')
        if subset_head.version != receipt.get('subset_head', subset_ref['version']):
            raise ValueError('Subset head changed; inspect before publishing')
        seen, matched = set(), 0
        columns = ['sha256', 'size_bytes'] + (['image_uri'] if 'image_uri' in original.schema.names else [])
        for batch in original.to_batches(columns=columns, batch_size=8192):
            for row in batch.to_pylist():
                found = lookup.get(row['sha256'])
                if found:
                    if row['size_bytes'] != found['size_bytes']:
                        raise ValueError(f'Source size mismatch: {row["sha256"]}')
                    if row.get('image_uri') not in (None, found['image_uri']):
                        raise ValueError(f'Source already has another URI: {row["sha256"]}')
                    seen.add(row['sha256'])
                    matched += 1
        if seen != set(lookup):
            raise ValueError(f'{len(set(lookup)-seen)} downloaded SHAs absent from collect')
        print(f'Collect preflight: {matched:,} rows / {len(seen):,} unique downloaded SHAs', flush=True)
        for batch in before.to_batches(columns=['sha256', 'image_uri'], batch_size=4096):
            for row in batch.to_pylist():
                if row['sha256'] not in lookup or row['image_uri'] not in (None, lookup[row['sha256']]['image_uri']):
                    raise ValueError('Subset coverage/URI differs')
        receipt = {**receipt, 'state': 'prepared', 'source_uri': str(source), 'source_version': source_version,
                   'subset_before': subset_ref, 'unique_images': len(lookup), 'source_matched_rows': matched,
                   'bytes': evidence['bytes'], 'verification': evidence['verification']}
        atomic_json(receipt_path, receipt)
        if 'source_head' not in receipt:
            if 'image_uri' in head.schema.names:
                raise ValueError('Source image_uri already exists; inspect before replacing a column')

            @contextmanager
            def guard(version):
                if version != source_version + 1:
                    raise ValueError('Concurrent collect write')
                yield

            @lance.batch_udf(output_schema=pa.schema([('image_uri', pa.string())]))
            def uri_column(batch):
                return pa.record_batch([pa.array([lookup.get(sha, {}).get('image_uri')
                    for sha in batch['sha256'].to_pylist()], type=pa.string())], names=['image_uri'])

            updated = lance.dataset(str(source), version=source_version, commit_lock=guard)
            updated.add_columns(uri_column, read_columns=['sha256'], batch_size=8192)
            receipt['source_head'] = updated.version
            atomic_json(receipt_path, receipt)
            print(f'Collect image_uri committed @{updated.version}', flush=True)
        if 'subset_head' not in receipt:
            patch_schema = pa.schema([('sha256', pa.string()), ('image_uri', pa.string()),
                                      ('availability', pa.string()), ('storage_mode', pa.string())])
            result = (data.read_lance(manifest_ref['uri'], version=manifest_ref['version'],
                                     columns=['sha256', 'image_uri'])
                .map(lambda r: {**r, 'availability': 'available', 'storage_mode': 'object_uri'})
                .write_lance(str(subset), mode='merge', on='sha256',
                    update_columns=['image_uri', 'availability', 'storage_mode'], when_not_matched='error',
                    expected_version=subset_ref['version'], schema=patch_schema, return_receipt=True))
            receipt['subset_head'] = result.committed_version
            atomic_json(receipt_path, receipt)
            print(f'Subset URI/status committed @{result.committed_version}; verifying untouched columns', flush=True)
        current = lance.dataset(str(source), version=receipt['source_head'])
        if current.count_rows() != original.count_rows() or current.count_rows(filter='image_uri IS NOT NULL') != matched:
            raise ValueError('Source URI coverage differs')
        for table in (current, lance.dataset(str(subset), version=receipt['subset_head'])):
            for batch in table.to_batches(columns=['sha256', 'image_uri'], filter='image_uri IS NOT NULL', batch_size=8192):
                for row in batch.to_pylist():
                    if row['image_uri'] != lookup[row['sha256']]['image_uri']:
                        raise ValueError(f'Published URI differs: {row["sha256"]}')
        # 原生增列只附加文件，原字段数据文件与 field IDs 必须保持原样。
        for old_fragment, new_fragment in zip(original.get_fragments(), current.get_fragments(), strict=True):
            old_files = old_fragment.metadata.files
            new_files = new_fragment.metadata.files
            if any(f not in new_files for f in old_files):
                raise ValueError('Original source column files changed')
        current_subset = lance.dataset(str(subset), version=receipt['subset_head'])
        untouched = [n for n in before.schema.names if n not in {'image_uri', 'availability', 'storage_mode'}]
        verify_unchanged(before, current_subset, untouched)
        if current_subset.count_rows(filter="image_uri IS NOT NULL AND availability = 'available' AND storage_mode = 'object_uri'") != len(lookup):
            raise ValueError('Subset URI coverage differs')
        receipt.update(state='complete', source_rows=current.count_rows(), subset_rows=current_subset.count_rows(),
                       source_registered=register_head(root, source), subset_registered=register_head(root, subset))
        atomic_json(receipt_path, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify', 'publish'])
    parser.add_argument('--audit', required=True)
    parser.add_argument('--subset')
    parser.add_argument('--version', type=int)
    parser.add_argument('--blobs')
    parser.add_argument('--source')
    parser.add_argument('--source-version', type=int)
    parser.add_argument('--concurrency', type=int, default=8)
    args = parser.parse_args()
    if args.action == 'verify':
        if not args.subset or not args.version or not args.blobs or args.concurrency < 1:
            parser.error('verify requires --subset, --version, --blobs and positive --concurrency')
        result = verify(args.subset, args.version, args.blobs, args.audit, concurrency=args.concurrency)
    else:
        if not args.source or not args.source_version:
            parser.error('publish requires --source and --source-version')
        result = publish(args.source, args.source_version, args.audit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
