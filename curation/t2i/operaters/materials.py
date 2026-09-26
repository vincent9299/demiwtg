"""按既有 seed 哈希选择出题概念；仅对概念名排序，不搬运图片或全文。"""
from demiflow.execution.artifacts import digest


def design_concepts(sources, config):
    """在配置范围内选择 max_units 个概念；返回集合供主线 Dataset.filter 使用。"""
    concepts = {r['concept'] for r in sources
                if not config.get('concepts') or r['concept'] in config['concepts']}
    ranked = sorted(concepts, key=lambda c: (digest([config['seed'], c]), c))
    return set(ranked[:config['max_units']] if config['max_units'] is not None else ranked)


