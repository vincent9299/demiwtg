"""Explicit import of external JSONL documents into the current Lance table.

JSONL is a transport input, never a second persisted business store.
"""
import argparse
import json
import itertools
from pathlib import Path
from demiflow.standalone import local_data
from collect.operators.page import BaseIngestStage, DocsSinkStage
from project import resolve_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=resolve_root())
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    def source():
        with args.input.open(encoding='utf-8') as stream:
            for line in stream:
                if line.strip(): yield json.loads(line)
    data = local_data().from_iter(lambda: itertools.islice(source(), args.limit))
    result = (data.map_async(BaseIngestStage(str(args.dataset)))
        .map_async(DocsSinkStage(str(args.dataset))).run_stream())
    print(result.summary())

if __name__ == '__main__':
    main()
