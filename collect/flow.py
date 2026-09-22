"""Current collection graph: fixed master release -> source operators -> Lance.

Image metadata is collection evidence. Visual annotation and concept review are
owned by the V2 curation graph, which can run independently after collection.
"""
import argparse
from pathlib import Path
from demiflow.standalone import local_data
from collect.concepts import resolve_concept_release
from project import resolve_root


def run(dataset, *, names=None, limit=None, master_release=None, carriers=('image','text'),
        top_n=2, max_pages=20, log_every=20):
    from collect.operators.concepts import ConceptSeedStage
    from collect.operators.search import SearchStage
    from collect.operators.download import DownloadStage
    from collect.operators.text_engines import TextSearchStage
    from collect.operators.page import PageFetchStage, InlineImageStage, DocsSinkStage
    root = Path(dataset)
    release = resolve_concept_release(root, release_id=master_release)
    ref = release['dataset_ref']
    data = local_data()
    predicate = None
    if names:
        predicate = 'name IN (' + ', '.join("'"+name.replace("'", "''")+"'" for name in names) + ')'
    concepts = data.read_lance(ref.resolve(root), version=ref.lance_version,
        columns=['name','aliases','carriers'], limit=limit, filter=predicate)
    concepts = concepts.map(lambda r:{**r, 'min_images':20, 'top_n':top_n})
    results = {}
    if 'image' in carriers:
        results['images'] = (concepts.filter(lambda r:'image' in r['carriers'])
            .map_async(ConceptSeedStage()).map_async(SearchStage(top_n=top_n))
            .map_async(DownloadStage(str(root))).run_stream(log_every=log_every))
    if 'text' in carriers:
        results['documents'] = (concepts.filter(lambda r:'text' in r['carriers'])
            .map_async(ConceptSeedStage()).map_async(TextSearchStage(per_query=3))
            .map_async(PageFetchStage(str(root),max_pages_per_concept=max_pages))
            .map_async(InlineImageStage(str(root))).map_async(DocsSinkStage(str(root)))
            .run_stream(log_every=log_every))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=resolve_root())
    parser.add_argument('--master-release')
    parser.add_argument('--concept', action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--carriers', nargs='+', choices=['image','text'], default=['image','text'])
    parser.add_argument('--top-n', type=int, default=2)
    parser.add_argument('--max-pages', type=int, default=20)
    args = parser.parse_args()
    for kind, result in run(args.dataset, names=args.concept, limit=args.limit,
            master_release=args.master_release, carriers=args.carriers,
            top_n=args.top_n, max_pages=args.max_pages).items():
        print(kind, result.summary())

if __name__ == '__main__':
    main()
