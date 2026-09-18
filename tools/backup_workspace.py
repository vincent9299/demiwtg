"""Create a reviewed source snapshot without altering live experiment files.

Input: inventory JSON produced by the workspace audit. Existing destination files
are never overwritten; notebooks omit execution outputs and embedded attachments.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE_EXT = {'.py', '.sh', '.ipynb', '.md', '.yaml', '.yml', '.toml', '.ini', '.cfg',
              '.html', '.js', '.ts', '.css', '.sql', '.patch', '.diff', '.txt', '.json'}
TOKEN = re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----|\b(?:AKID[A-Za-z0-9]{25,50}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,})\b')

def notebook_source(raw):
    obj = json.loads(raw)
    for cell in obj.get('cells', []):
        if cell.get('cell_type') == 'code':
            cell['outputs'] = []
            cell['execution_count'] = None
        cell.pop('attachments', None)
        cell.get('metadata', {}).pop('execution', None)
    obj.get('metadata', {}).pop('widgets', None)
    return json.dumps(obj, ensure_ascii=False, indent=1) + '\n'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('workspace', type=Path)
    ap.add_argument('inventory', type=Path)
    ap.add_argument('destination', type=Path)
    ap.add_argument('--all-listed', action='store_true', help='Snapshot an explicitly reviewed input list')
    args = ap.parse_args()
    records = []
    for item in json.loads(args.inventory.read_text()):
        rel = item['path']; p = Path(rel)
        eligible = args.all_listed or rel.startswith(('_staging/', 'demiwtg/state/', 'demiwtg/data/')) or len(p.parts) == 1
        if not eligible:
            continue
        row = dict(item)
        row['decision'] = 'excluded_runtime_or_binary'
        selected = p.suffix in SOURCE_EXT or p.name in {'LICENSE', 'Makefile', 'Dockerfile', '.gitignore'}
        if p.suffix in {'.json', '.txt', '.html'}:
            selected = selected and item['bytes'] <= 2 * 1024**2
            if rel.startswith('demiwtg/state/'):
                selected = selected and any(k in p.stem.lower() for k in ('summary', 'report', 'config', 'manifest', 'score', 'readme', 'status'))
        if item['kind'] == 'symlink' or not selected:
            records.append(row); continue
        source = args.workspace / rel
        try:
            raw = source.read_bytes()
            text = raw.decode('utf-8')
            if p.suffix == '.ipynb':
                text = notebook_source(text)
            text, count = TOKEN.subn('REDACTED_LOCAL_CREDENTIAL', text)
            target = args.destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('x') as f:
                f.write(text)
            target.chmod(source.stat().st_mode & 0o777)
            row.update(decision='snapshot', source_sha256=hashlib.sha256(raw).hexdigest(),
                       snapshot_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                       credential_redactions=count, notebook_outputs_removed=p.suffix == '.ipynb')
        except (OSError, UnicodeError, ValueError) as exc:
            row.update(decision='excluded_read_error', error=type(exc).__name__)
        records.append(row)
    args.destination.mkdir(parents=True, exist_ok=True)
    (args.destination/'MANIFEST.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')
    print('Snapshot files:', sum(r['decision']=='snapshot' for r in records))
    print('Excluded:', sum(r['decision']!='snapshot' for r in records))
    print('Redactions:', sum(r.get('credential_redactions',0) for r in records))

if __name__ == '__main__':
    main()
