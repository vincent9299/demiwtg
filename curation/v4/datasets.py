"""Named, column-based business datasets. JSON columns only hold named lists/structures."""
import json
import sqlite3
from contextlib import closing
from demiflow.standalone import local_data

# Source schemas describe original fields; files of the same schema are partitions.
SOURCE_SCHEMAS={
 'legacy_concepts':{'fields':'name, aliases, carriers, taxonomy','output':'concept_sources, concepts','key':'name'},
 'qid_concepts':{'fields':'qid, en.page_id/title, zh.page_id/title, aliases','output':'concept_sources, concepts, concept_pages','key':'qid'},
 'qid_concepts_base':{'fields':'qid, en.page_id/title, zh.page_id/title','output':'concept_sources, concepts, concept_pages','key':'qid'},
 'legacy_docs':{'fields':'path, title, url, concepts/instances','output':'documents, document_links','key':'source file version + row'},
 'wiki_pages':{'fields':'lang, page_id, qid (optional), title, sections','output':'documents; document_links via qid or language/page join','key':'source file version + row'},
 'legacy_images':{'fields':'path/blob_path, sha256, caption, concepts/instances','output':'images, image_links','key':'source file version + row'},
 'qid_images':{'fields':'qid, path/blob_path, sha256, caption, content_url','output':'images, image_links','key':'source file version + row'},
 'image_roles':{'fields':'qid, property, role, commons_file','output':'image_roles','key':'source file version + row'},
 'gallery_captions':{'fields':'qid, commons_file, caption','output':'gallery_captions','key':'source file version + row'},
 'qid_graph':{'fields':'from, to, remaining relation fields','output':'concept_edges','key':'source file version + row'},
}

# SQL type and user-facing meaning. No kind/record envelope in business datasets.
COMMON={'source_id':('TEXT','原始来源版本及行位置的标识'),
        'source_path':('TEXT','原始采集文件绝对路径'),'source_row':('INTEGER','从1开始的行号或数组位置')}
SCHEMAS={
 'concept_sources':({'source_id':('TEXT','来源记录标识'),'concept_ref':('TEXT','legacy:名称或qid:QID，尚未跨源合并'),
   'name':('TEXT','来源显示名称'),'aliases':('JSON','来源别名列表'),'qid':('TEXT','外部QID，可空'),
   'taxonomy':('JSON','来源分类挂载快照，不决定准入'),**COMMON},['source_id']),
 'concept_pages':({'concept_ref':('TEXT','页面所对应的来源概念引用'),'lang':('TEXT','语言'),
   'page_id':('TEXT','该语言站点页面ID'),'source_id':('TEXT','概念来源记录标识')},['concept_ref','lang','page_id','source_id']),
 'documents':({'doc_id':('TEXT','文档来源版本ID'),'title':('TEXT','采集标题'),'url':('TEXT','来源URL'),
   'path':('TEXT','datasets内保存的正文路径，可空'),'format':('TEXT','saved_text或wiki_sections'),
   'lang':('TEXT','语言，可空'),'page_id':('TEXT','Wiki页面ID，可空'),'qid':('TEXT','页面自带QID，可空'),
   'sections':('JSON','Wiki原始章节；普通文档为空列表'),**COMMON},['doc_id']),
 'images':({'image_id':('TEXT','图片来源记录版本ID，多个来源可指向相同字节'),
   'path':('TEXT','图片本地相对路径'),'sha256':('TEXT','来源声称的字节哈希，需核对'),
   'url':('TEXT','图片来源或内容URL'),'caption':('TEXT','来源caption线索，不等于看图'),**COMMON},['image_id']),
 'document_links':({'concept_ref':('TEXT','来源概念引用'),'doc_id':('TEXT','文档ID'),
   'method':('TEXT','source_fields或language_page_id'),'status':('TEXT','source_association_only或ambiguous_mapping')},['concept_ref','doc_id']),
 'image_links':({'concept_ref':('TEXT','来源概念引用'),'image_id':('TEXT','图片ID'),
   'method':('TEXT','source_fields'),'status':('TEXT','source_association_only')},['concept_ref','image_id']),
 'image_roles':({'qid':('TEXT','外部概念QID'),'property':('TEXT','来源属性'),
   'role':('TEXT','来源图片角色'),'commons_file':('TEXT','Commons文件名'),**COMMON},['source_id']),
 'gallery_captions':({'qid':('TEXT','外部概念QID'),'commons_file':('TEXT','Commons文件名'),
   'caption':('TEXT','来源图库图注'),**COMMON},['source_id']),
 'concept_edges':({'from_qid':('TEXT','起点QID'),'to_qid':('TEXT','终点QID'),
   'relation':('JSON','原始图关系字段，非推导后的可靠知识'),**COMMON},['source_id']),
 'concepts':({'concept_ref':('TEXT','业务概念引用；不同来源身份未自动合并')},['concept_ref']),
 'selected_concepts':({'concept_ref':('TEXT','业务概念引用'),'selected':('INTEGER','1入选，0未入选'),
   'reason':('TEXT','selected、id_filter、concept_sample')},['concept_ref']),
 'missing_concepts':({'concept_ref':('TEXT','未找到的指定概念引用'),'reason':('TEXT','not_in_read_concept_scope')},['concept_ref']),
 'unmatched_documents':({'doc_id':('TEXT','未匹配文档'),'concept_ref':('TEXT','来源概念引用；未知为空字符串'),
   'reason':('TEXT','no_source_link、unknown_concept或ambiguous_mapping')},['doc_id','concept_ref']),
 'unmatched_images':({'image_id':('TEXT','未匹配图片'),'concept_ref':('TEXT','来源概念引用；未知为空字符串'),
   'reason':('TEXT','no_source_link或unknown_concept')},['image_id','concept_ref']),
 'selected_document_links':({'concept_ref':('TEXT','入选概念'),'doc_id':('TEXT','关联文档'),'method':('TEXT','来源关联方法')},['concept_ref','doc_id']),
 'selected_image_links':({'concept_ref':('TEXT','入选概念'),'image_id':('TEXT','关联图片'),'method':('TEXT','来源关联方法')},['concept_ref','image_id']),
 'document_texts':({'doc_id':('TEXT','文档ID'),'text':('TEXT','读取的完整原文或Wiki章节串联文本'),
   'sha256':('TEXT','实际原文字节／序列化文本哈希'),'status':('TEXT','readable或read_error'),
   'error':('TEXT','读取错误，可空')},['doc_id']),
 'clean_documents':({'doc_id':('TEXT','文档ID'),'text':('TEXT','清洗后的全文'),
   'status':('TEXT','cleaned_candidate、needs_review、unavailable'),
   'warnings':('JSON','清洗质量提示'),'blocks':('JSON','内容块及原文／清洗位置、保留排除依据'),
   'counts':('JSON','输入输出字数与块数'),'source_sha256':('TEXT','清洗输入哈希'),
   'version':('TEXT','清洗代码版本')},['doc_id']),
 'image_checks':({'image_id':('TEXT','图片来源ID'),'status':('TEXT','字节核对状态，不是图片语义判断'),
   'details':('JSON','实际路径、哈希、解码尺寸或错误，结构见verify_image')},['image_id']),
 'concept_coverage':({'concept_ref':('TEXT','入选概念'),'document_count':('INTEGER','关联文档数'),
   'image_count':('INTEGER','关联图片数'),'readable_documents':('INTEGER','可读文档数'),
   'verified_images':('INTEGER','字节检查通过图片数'),'status':('TEXT','materials_available或no_materials_in_read_scope'),
   'knowledge_status':('TEXT','not_extracted；资料存在不等于提取完成')},['concept_ref']),
}

SCHEMAS.update({
 'knowledge':({'knowledge_id':('TEXT','任务ID＋局部fact_id，防止不同窗口重名'),
  'task_id':('TEXT','提取窗口ID'),'statement':('TEXT','知识陈述'),'conditions':('JSON','成立条件'),
  'exceptions':('JSON','例外'),'review_status':('TEXT','machine_candidate，非确定知识')},['knowledge_id']),
 'concept_knowledge':({'concept_ref':('TEXT','概念引用'),'knowledge_id':('TEXT','知识ID')},['concept_ref','knowledge_id']),
 'knowledge_sources':({'knowledge_id':('TEXT','知识ID'),'doc_id':('TEXT','支持文档ID'),
  'passage_id':('TEXT','模型输入片段ID'),'quote':('TEXT','逐字引文'),'start':('INTEGER','片段在清洗正文中的起点'),
  'end':('INTEGER','片段终点，非引文本身的精确位置')},['knowledge_id','doc_id','passage_id','quote']),
 'image_support':({'knowledge_id':('TEXT','知识ID'),'image_id':('TEXT','图片来源ID'),
  'status':('TEXT','full、partial、none、unobservable'),'region':('TEXT','图中区域'),
  'supports':('TEXT','支持内容'),'limitations':('TEXT','不能证明什么')},['knowledge_id','image_id']),
 'knowledge_tasks':({'task_id':('TEXT','窗口ID'),'concept_ref':('TEXT','概念引用'),
  'status':('TEXT','blocked或needs_joint_review'),'blocked':('JSON','阻塞原因或null'),
  'conflicts':('JSON','未解决冲突'),'coverage_note':('TEXT','模型声明的提取范围，非完整覆盖保证')},['task_id'])
})


class Tables:
    def __init__(self,path,schemas=None):
        self.schemas=SCHEMAS if schemas is None else schemas
        self.path=path; self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        for table,(fields,keys) in self.schemas.items():
            cols=','.join(f'"{k}" {"TEXT" if t=="JSON" else t}' for k,(t,_) in fields.items())
            self.db.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols}, PRIMARY KEY ({",".join(keys)}))')
        self.db.execute('CREATE TABLE IF NOT EXISTS completed(step TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS source_originals(source_id TEXT PRIMARY KEY, source_kind TEXT, raw_json TEXT, provenance TEXT)')
        self.db.commit()
    def close(self):self.db.close()
    def put(self,table,row):
        fields,keys=self.schemas[table]
        if set(row)!=set(fields):raise ValueError(f'{table}: missing {set(fields)-set(row)}, extra {set(row)-set(fields)}')
        for k,(typ,_) in fields.items():
            value=row[k]
            if value is not None and typ in {'TEXT','INTEGER'} and not isinstance(value,str if typ=='TEXT' else int):
                raise TypeError(f'{table}.{k}: expected {typ}, got {type(value).__name__}')
        vals=[json.dumps(row[k],ensure_ascii=False) if typ=='JSON' else row[k] for k,(typ,_) in fields.items()]
        old=self.db.execute(f'SELECT * FROM "{table}" WHERE '+ ' AND '.join(f'"{k}"=?' for k in keys),[row[k] for k in keys]).fetchone()
        if old is not None:
            if tuple(vals)!=old:raise ValueError(f'{table}: changed output; use a new run')
            return
        self.db.execute(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in vals)})',vals)
    def rows(self,table,sql=None,args=()):
        fields=self.schemas[table][0]
        with closing(sqlite3.connect(self.path)) as db:
            for values in db.execute(sql or f'SELECT * FROM "{table}"',args):
                yield {k:json.loads(v) if typ=='JSON' and v is not None else v for (k,(typ,_)),v in zip(fields.items(),values)}
    def dataset(self,table,sql=None,args=()):
        return local_data().from_iter(lambda:self.rows(table,sql,args))
    def describe(self,table):
        fields,keys=self.schemas[table]
        return [{'field':k,'type':typ,'key':k in keys,'meaning':meaning} for k,(typ,meaning) in fields.items()]


class SaveTable:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,tables,table):self.tables=tables;self.table=table;self.n=0
    async def __call__(self,row):
        self.tables.put(self.table,row);self.n+=1
        if self.n%100==0:self.tables.db.commit()
        return row
    async def aclose(self):self.tables.db.commit()
