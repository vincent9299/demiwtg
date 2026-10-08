"""待审核概念的材料统计与选样；只处理传入行，不读取表或调用模型。"""
import hashlib
import json


def reference_coverage(row, *, min_candidate_images, category_depth, sources_json):
    """合并材料计数，区分已发布参考与待审图片；完整 taxonomy 原样保留。"""
    counts = {name: row.get(name) or 0 for name in (
        'candidate_image_count', 'published_image_count', 'reviewed_text_count')}
    paths = [path.split(' / ')[:category_depth] for path in row['taxonomy'] if path]
    # 多路径均留存；仅为均衡选样使用字典序首个分类前缀，不当作对象身份判定。
    category = min(' / '.join(path) for path in paths) if paths else '未提供分类'
    ready = bool(counts['published_image_count'] or counts['reviewed_text_count'])
    return {'concept': row['concept'], 'taxonomy': row['taxonomy'], 'category': category,
            **counts, 'eligible': ready or counts['candidate_image_count'] >= min_candidate_images,
            'material_status': 'published_reference' if ready else 'needs_image_review',
            'selection_rank': None, 'sources_json': sources_json}


def choose_reference_concepts(row, *, sample_size, sample_seed):
    """先已审核材料，再满足图片下限的概念；各档分类轮转，类内固定种子排序。"""
    def rank(value):
        return hashlib.sha256(json.dumps([sample_seed, value], ensure_ascii=False).encode()).hexdigest()

    eligible = [item for item in row['rows'] if item['eligible']]
    if len(eligible) < sample_size:
        raise ValueError(f'Only {len(eligible)} concepts have sufficient reference candidates; need {sample_size}')
    chosen = []
    for status in ('published_reference', 'needs_image_review'):
        groups = {}
        for item in eligible:
            if item['material_status'] == status:
                groups.setdefault(item['category'], []).append(item)
        for group in groups.values():
            group.sort(key=lambda item: (rank(item['concept']), item['concept']))
        categories = sorted(groups, key=lambda category: (rank(category), category))
        offset = 0
        while len(chosen) < sample_size:
            available = [category for category in categories if offset < len(groups[category])]
            if not available:
                break
            for category in available:
                chosen.append({**groups[category][offset], 'selection_rank': len(chosen) + 1})
                if len(chosen) == sample_size:
                    break
            offset += 1
    return {'selected': chosen, 'screened_count': len(row['rows']), 'eligible_count': len(eligible)}
