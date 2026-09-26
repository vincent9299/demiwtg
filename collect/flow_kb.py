"""Wikipedia dump -> parser -> current Lance document table, using demiflow."""
import argparse
import asyncio
import itertools
from pathlib import Path
from demiflow import data
from collect.operators.wiki_dump import iter_dump_pages, WikiParseStage
from collect.ingestion import wiki_document
from collect.material_writer import write_documents
from project import resolve_root


class CommitPages:
    def __init__(self, root):
        self.root = root

    async def __call__(self, batch):
        await asyncio.to_thread(write_documents, self.root, [wiki_document(r) for r in batch])
        return batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump', required=True)
    parser.add_argument('--lang', choices=['zh','en'], required=True)
    parser.add_argument('--dataset', type=Path, default=resolve_root())
    parser.add_argument('--limit', type=int)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--parse-concurrency', type=int, default=4)
    args = parser.parse_args()
    def source():
        pages = iter_dump_pages(args.dump, lang=args.lang)
        yield from itertools.islice(pages, args.offset,
            args.offset+args.limit if args.limit is not None else None)
    parse = WikiParseStage()
    parse.concurrency = args.parse_concurrency
    parse.queue_depth = 2 * args.parse_concurrency
    results = (data.from_iter(source).map_async(parse)
        .batch_map(CommitPages(args.dataset), max_batch=1024, concurrency=1)
        .run_stream())
    print(results.summary())

if __name__ == '__main__':
    main()
