"""文章公共交付：固定 knowledge_base 完成后写 articles，不写图片公共表。"""
from pathlib import Path
from demiflow.lance.refs import DatasetRef
from project import resolve_root
from preparation.articles.operators.article import article_entity, write_articles
from preparation.images.catalog.operators.records import identity
from preparation.articles.operators.runfiles import read_stage, run_records, stage_ref


def save_article_results(run, *, target_uri=None, write_mode='merge'):
    """本轮文章写入所配置的文章目标；完整提交的固定引用用于幂等续跑。"""
    if write_mode not in {'merge', 'append', 'overwrite'}:
        raise ValueError('write_mode must be merge, append or overwrite')
    source = stage_ref(run, 'knowledge_base')
    if not source.row_count and write_mode == 'merge':
        return None
    binding = {'stage': source.to_dict(), 'raw_source': None,
               'target_uri': str(target_uri) if target_uri else None, 'write_mode': write_mode}
    store = run_records(run)
    prior = store.publication('results', 'article')
    if prior is not None:
        if prior['binding'] != binding:
            raise ValueError('Saved result stage changed; use a new run')
        ref = DatasetRef.from_dict(prior['dataset_ref'])
        ref.open(resolve_root())
        return ref
    selection = 'article_' + identity(str(Path(run).resolve()))[:24]
    ref = write_articles(resolve_root(), [
        article_entity(row, release_id=selection, run_id=str(run))
        for row in read_stage(run, 'knowledge_base').iter_rows()],
        target_uri=target_uri, write_mode=write_mode)
    store.publish('results', {'binding': binding, 'dataset_ref': ref.to_dict(), 'release_id': selection}, 'article')
    return ref
