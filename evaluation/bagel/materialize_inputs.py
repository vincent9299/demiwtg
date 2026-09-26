"""Explicit disposable export for third-party tools that require filenames."""
from pathlib import Path
import argparse
from project import PROJECT_ROOT, historical_evidence


def materialize(destination):
    destination = Path(destination).resolve()
    if destination.is_relative_to(PROJECT_ROOT):
        raise ValueError('Regression exchange files must stay outside the source repository')
    destination.mkdir(parents=True, exist_ok=False)
    evidence = historical_evidence()
    prefix = 'evaluation/bagel/data/'
    count = 0
    for name in evidence.paths(prefix):
        relative = name[len(prefix):]
        if not relative.startswith(('prompts/', 'dpg_bench/')) or Path(relative).suffix in {'.py', '.sh'}:
            continue
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(evidence.read_bytes(name))
        count += 1
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print({'exported_files': materialize(parser.parse_args().destination)})
