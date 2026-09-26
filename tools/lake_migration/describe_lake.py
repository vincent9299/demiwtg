"""Generate a readable dictionary of current registered Lance tables."""
from collections import defaultdict
from pathlib import Path
import pyarrow as pa
from demiflow.lance.registry import Catalog
from project import resolve_root

GRAINS = {
    'curated/images': '一张图片的策展属性；SHA 关联原始图片，source_refs 固定处理输入',
    'curated/articles': '一篇知识文章或独立摘要草稿，审核与发布归属在行内',
    'images': '一张图片及其来源、描述、概念匹配和视觉审核集合',
    'documents': '一个确定文档版本，正文、来源与概念关联',
    'articles': '一篇知识文章或独立摘要草稿，状态与发布归属在行内',
    'raw/images': '一行一份确定图片内容，主键 SHA；单张实际 Lance 表',
    'raw/collect_records': '历史采集证据：概念摘要草稿、丢图前清单、当时概念名单；不是现役材料池',
    'raw/taxonomy_sources': '主数据分类来源证据；不是第二份当前主数据',
    'raw/documents': '一行一个确定文档版本，主键 document_id；网页/wiki 共享 schema',
    'master/concepts': '一个概念', 'master/taxonomy': '一个分类节点或一条父子边',
    'master/memberships': '一个概念与一个分类节点的挂载关系',
    'derived/annotations': '图片描述或图片×概念审核，协议随固定表版本记录',
    'releases/visual_materials': '某次发布中的图片×概念视觉材料',
    'releases/knowledge': '某次发布中的一个概念知识文章',
    'releases/evaluation': '历史评测交付包的一条原记录；按来源文件区分其业务粒度',
    'releases/training': '历史交付包的一条原记录；包含样本及元数据，不能把总行数当样本数',
    'runs/knowledge': '一次运行中的概念处理记录',
    'runs/training': '一次运行中的训练样本记录',
    'runs/benchmark': '一次运行中的题目记录',
    'runs/evaluation': '一次评测运行中的结果记录',
}
MEANINGS = {
    'source_refs': '处理输入的固定 raw DatasetRef 列表（JSON 序列化）；不是原始表的可变 head',
    'asset_id': '图片字节 SHA；目标统一命名 sha256', 'sha256': '图片字节内容摘要',
    'data': '实际图片字节（Lance Blob）', 'ext': '文件扩展名', 'byte_size': '实际字节大小',
    'storage_mode': '存储方式；external 是待退役的旧文件索引', 'relative_path': '旧来源文件位置',
    'instances': '采集关联的概念列表；在 taxonomy nodes 中是历史挂载快照',
    'concept': '关联概念名称', 'name': '名称', 'aliases': '别名列表', 'carriers': '概念可承载的材料类型',
    'taxonomy': '概念原始分类快照；当前挂载以 memberships 为准',
    'source': '采集来源系统', 'content_url': '来源图片地址', 'landing_url': '来源页面地址',
    'source_file': '原始来源文件身份', 'source_row': '原文件行号；-1 表示文件实体',
    'raw_payload': '迁移保全原文；常用字段应提升为显式 typed 列',
    'payload': '该类型的业务正文/原始记录；当前不同类型语义不同，见粒度说明',
    'migrated_at_us': '本条记录的迁移时间', 'fetched_at': '采集时间',
    'caption': '描述文本；来源描述与模型生成描述需按所属表区分',
    'width': '来源记录中的宽度，不自动等同已核验字节的实测宽度',
    'height': '来源记录中的高度，不自动等同已核验字节的实测高度',
    'orig_width': '来源声明的原图宽度', 'orig_height': '来源声明的原图高度',
    'size_bytes': '采集记录声明的字节大小', 'mime': '来源媒体类型',
    'license': '来源版权声明', 'author': '来源作者声明', 'kb_match': '旧采集匹配评分',
    'richness': '旧采集信息量评分', 'record_key': '来源内记录身份', 'asset_name': '来源文件/材料类别',
    'concept_key': '概念主键', 'taxonomy_node_key': '分类节点主键',
    'source_ref': '固定来源表 URI@版本', 'source_record_key': '固定来源表内行序号',
    'ordinal': '关系或子项顺序', 'path': '分类节点路径', 'parent_path': '父分类路径',
    'child_path': '子分类路径', 'depth': '分类深度',
    'status': '该业务协议中的处理状态', 'match_status': '概念匹配结论', 'reason': '判定理由',
    'review_status': '所在表的审核状态；草稿为 unreviewed', 'published': '是否纳入此发布',
    'release_id': '发布身份', 'run_id': '运行身份', 'lang': '语言', 'page_id': '来源页面身份',
    'revision_id': '来源页面修订身份', 'text_sha256': '正文摘要', 'byte_len': '正文长度',
    'sections': '页面章节结构（当前 JSON 字符串）', 'images': '页面内图片记录（当前 JSON 字符串）',
}


MEANINGS.update({
    'annotation_ref_sha': '冻结预标注记录的摘要', 'attempts': '已尝试次数',
    'case_id': '历史实验条目身份', 'categories': '页面分类列表（当前 JSON）',
    'document_count': '该概念记录包含的文档数量', 'image_count': '图片数量',
    'knowledge_count': '知识主题/条目数量', 'exclude_reason': '未发布的具体原因',
    'is_disambig': '是否消歧页面', 'is_redirect': '是否重定向页面',
    'redirect_target': '重定向目标', 'knowledge_intro': '分类节点的知识简介',
    'labels_json': '视觉标签结构（当前 JSON）', 'link_count': '页面链接数',
    'links': '页面链接明细（当前 JSON）', 'objects': '可见对象及属性（当前 JSON）',
    'observability_issues': '遮挡、模糊等可观察性问题（当前 JSON）',
    'parser_version': '来源解析器版本', 'pixel_fingerprint_role': '图片用途划分标签，非永久质量结论',
    'qid': 'Wikidata 实体 ID', 'raw_result': '原始概念匹配模型结果',
    'related_tags': '关联分类/标签（当前 JSON）', 'representation': '照片、插画、图表等表现形式',
    'representative_cases': '分类代表案例（当前 JSON）', 'status_reason': '当前状态的原因',
    'text_regions': '图中文字区域（当前 JSON）', 'title': '来源标题',
    'uncertainties': '模型明确记录的不确定点', 'view_tags': '视角/构图标签列表',
})


MEANINGS.update({
    'protocol_id':'标注协议内容摘要，与 protocols 表关联',
    'configuration_json':'协议的其他模型和图像预处理参数；提示词等常用字段已展开',
    'draft_kind':'历史草稿类型，当前为 summary',
    'node_path':'历史分类节点路径，合并主键',
    'node_path_en':'同节点英文路径',
    'instances_text':'历史实例清单原单元格文本',
    'instances_en_text':'历史英文实例清单原单元格文本',
    'source_names_text':'分类来源名称原单元格文本',
    'source_details_text':'分类来源详情原单元格文本',
    'origins':'此节点在三份历史 CSV 中的来源位置',
    'selection_name':'历史概念选集名；与当前 master 概念全集区分',
    'taxonomy_paths':'选集当时携带的分类路径快照',
    'source_payload_sha256':'原始采集记录正文摘要，用于核对迁移身份',
    'describe_prompt':'图片描述提示词', 'match_prompt':'概念匹配提示词',
    'human_reviewed':'是否经过人工审核，不由模型通过状态推断',
    'author_type':'标注产生者类型', 'version':'来源协议声明版本', 'model':'来源标注模型',
    'concepts':'实体关联的概念去重列表，仅表示采集关联，不表示审核通过',
    'sources':'typed 来源明细；保留每条来源与概念、查询词、描述之间的对应关系',
    'availability':'available 表示有 Lance Blob；metadata_only 表示只有来源资料',
    'resolution':'从实际像素测得的尺寸；null 表示未测量，不能拿来源尺寸代填',
    'document_id':'来源身份、修订 ID、正文 SHA 的组合摘要；不同版本不覆盖',
    'document_type':'web 或 wiki', 'language':'文档语言',
    'source_identity':'网页 URL 或包含语言/page_id 的 wiki 身份',
    'content_sha256':'当前正文或章节拼接文本的 SHA256，读取时复核',
    'source_content_sha256':'来源系统记录的正文 SHA；与当前拼接规则分开',
    'text':'网页原始正文；wiki 正文在 sections 中，不重复复制',
    'sections':'typed 章节列表，含 title、text 和来源附加字段',
    'images':'typed 文内图片列表，引用图片 SHA/外部地址及位置，不复制 Blob',
    'content_status':'available 或 metadata_only；正文不可用时明确标记',
    'source_record_id':'该来源明细的稳定内容身份；用于幂等合并',
    'source_system':'采集来源系统', 'query':'来源查询词', 'queries':'概念与查询词的 map',
    'query_languages':'概念与查询语言的 map', 'url':'来源页面/文内图片地址',
    'declared_width':'来源声明的宽度，未经当前像素测量',
    'declared_height':'来源声明的高度，未经当前像素测量',
    'original_width':'来源声明的原图宽度', 'original_height':'来源声明的原图高度',
    'declared_bytes':'来源声明的字节数', 'declared_mime':'来源声明的 MIME',
    'identity':'该来源的概念匹配判断，不是整张图片的永久结论',
    'attributes_json':'来源特有的附加字段；常用字段已单独 typed 存储',
    'stored_width':'文件内存储像素宽度', 'stored_height':'文件内存储像素高度',
    'megapixels':'实测宽×高 / 1e6', 'aspect_ratio':'实测宽 / 高',
    'orientation_basis':'尺寸是否依据 EXIF 方向变换', 'measurement_source':'尺寸测量的来源记录',
    'position':'文内图片顺序', 'links_json':'来源内链接列表原文',
    'storage_mode':'当前图片字节均为 lance_blob',
    'width':'实测显示宽度（仅 resolution 内）；来源声明字段另存',
    'height':'实测显示高度（仅 resolution 内）；来源声明字段另存',
})


MEANINGS.update({
    'objects': '可见对象及属性结构列表',
    'text_regions': '图中文字区域结构列表',
    'observability_issues': '遮挡、模糊等可观察性问题结构列表',
    'images': '文内图片引用或文章配图位置，按所属结构区分',
    'text': '文本内容：文档正文、引用段落或图中文字，按所属结构区分',
    'descriptions': '图片描述记录集合；不同配置/运行保留来源',
    'description': '结构化可见内容描述，不等于概念审核通过',
    'concept_matches': '针对各概念的机器匹配结果集合',
    'concept_assessments': '针对各概念的视觉审核与发布归属集合',
    'annotation_id': '一份标注的稳定身份', 'assessment_id': '某次运行/发布内概念审核身份',
    'config_id': '业务配置内容摘要', 'config_path': 'Git 管理的业务配置位置',
    'published_concepts': '审核详情推导的通过概念集合；供列表索引筛选',
    'release_ids': '发布归属列表；读取时必须结合固定版本与相应审核项',
    'concepts': '来源、匹配和审核涉及概念的集合；不表示全部通过审核',
    'identity_relation': '图与该概念的关系，例如主体或相关活动',
    'visual_support': '图像对该概念的可见支持范围与限制',
    'supports': '明确支持的内容', 'region': '支持内容所在区域', 'limitations': '支持限制',
    'review_json': '原模型审核及调用证据 JSON，检索和发布判断使用展开字段',
    'observation_json': '该概念审核时的原始观察上下文 JSON',
    'provenance': '处理来源与配置记录', 'details_json': '历史源记录/补充溯源 JSON',
    'record_sha256': '来源记录内容摘要', 'location': '图内位置', 'visible_features': '可见特征',
    'readability': '文字可读性', 'type': '问题类型', 'detail': '问题描述',
    'article_id': '文章身份；独立摘要不会因概念相同而被合为同一篇',
    'article_kind': '知识文章或摘要草稿类型', 'content': '文章主题、段落、配图位置及引用',
    'citations': '引用段落与来源上下文', 'illustrations': '已绑定配图及其来源证据',
    'paragraphs': '正文段落', 'references': '段落/配图引用', 'paragraph_indices': '引用覆盖的段落序号',
    'paragraph_index': '配图关联段落位置', 'source_ids': '引用来源 ID 列表',
    'source_id': '引用来源 ID', 'source_family': '来源归属', 'figure_number': '图号',
    'image_id': '文章或审核上下文内的图像标识', 'evidence_json': '配图绑定原始证据 JSON',
    'context_json': '历史候选材料与复杂审计上下文，不充当文章正文',
    'context_before': '前文上下文', 'context_after': '后文上下文', 'reference_notes': '引用说明',
})


def field_rows(field,prefix=''):
    name=prefix+field.name;typ=field.type;children=None
    if pa.types.is_struct(typ):label='struct';children=typ
    elif pa.types.is_list(typ) and pa.types.is_struct(typ.value_type):
        label='list<struct>';children=typ.value_type
    else:label=str(typ).replace('|','\\|')
    yield f'| `{name}` | `{label}` | {"是" if field.nullable else "否"} | {MEANINGS.get(field.name,"所属业务协议的显式字段；见 schema 定义及源记录")} |'
    if children:
        for child in children:yield from field_rows(child,name+('[]' if pa.types.is_list(typ) else '')+'.')


def grain(key):
    specific = {'historical_delivery_records': '历史交付包中的一条原记录；样本、审核与元数据按 asset_name 区分', 'image_descriptions': '图片 SHA × 描述协议/版本',
                'concept_matches': '图片 SHA × 概念 × 匹配协议/版本',
                'taxonomy_nodes': '一个分类节点', 'taxonomy_edges': '一条分类父子边',
                'knowledge_drafts':'一个概念的历史摘要草稿，未审核',
                'image_observations':'丢图前的一条采集观察记录，不代表当前字节可用',
                'concept_selections':'历史选集中的一个概念条目',
                'taxonomy_history':'一个历史分类节点，三份来源按 node_path 合并',
                'annotation_protocols':'一份确定的标注协议，protocol_id 为内容摘要'}
    prefix, schema = key.split(' · ')
    if prefix == 'derived/annotations' and schema == 'verbatim_records':
        return '一份预标注协议原文'
    return specific.get(schema, GRAINS.get(prefix, '一条来源记录；按来源文件及原始行号确定含义'))


def describe(root, destination):
    latest = {}
    for ref in Catalog(root).registered():
        prior = latest.get(ref.relative_uri)
        if prior is None or ref.lance_version > prior.lance_version: latest[ref.relative_uri] = ref
    groups = defaultdict(list)
    for uri, ref in latest.items():
        key = str(Path(ref.resolve(root)).parent.relative_to(root)) + ' · ' + ref.schema_name
        groups[key].append(ref)
    lines = ['# 当前 Lance 数据字典', '',
             '按当前登记的各表最新版本生成。固定发布仍按自身引用读取；表版本不等于 pipeline 版本。', '',
             '本页描述实际现状；实体、嵌套标注和发布选择见 [实体设计](entity_tables_20260921.md)，迁移验证见 [分离报告](raw_curated_separation_20260921.md)。', '',
             '| 数据集 / schema | 粒度 | 物理表数 | 总行数 |', '| --- | --- | ---: | ---: |']
    for key, refs in sorted(groups.items()):
        prefix = key.split(' · ')[0]
        lines.append(f'| {key} | {grain(key)} | {len(refs)} | {sum(x.row_count for x in refs):,} |')
    for key, refs in sorted(groups.items()):
        lines += ['', '## ' + key, '', '粒度：' + grain(key), '',
                  '表位置：' + '、'.join('`'+str(Path(x.resolve(root)).relative_to(root))+'`' for x in refs[:3]) + (' 等分片' if len(refs)>3 else ''), '',
                  '| 字段 | Lance/Arrow 类型 | 可空 | 含义 |', '| --- | --- | --- | --- |']
        for field in refs[0].open(root).schema:
            lines.extend(field_rows(field))
    Path(destination).write_text('\n'.join(lines)+'\n')
    return {'logical_groups': len(groups), 'physical_tables': len(latest)}


if __name__ == '__main__':
    print(describe(resolve_root(), Path(__file__).parent/'reports/lake_data_dictionary.md'))
