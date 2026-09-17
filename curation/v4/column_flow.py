"""Three business datasets; operators append columns and gather only for model input."""
import asyncio
import json
from .datasets import Tables,SCHEMAS
from .dataset_flow import DatasetFlow,ReadDocument,CleanDocument,CheckImage,JOINS
from .contracts import immutable,run_lock

DOCUMENT_COLUMNS={
 'raw_text':('TEXT','读取的完整原文'), 'raw_sha256':('TEXT','实际原文哈希'),
 'read_status':('TEXT','readable/read_error，null表示未读取'),'read_error':('TEXT','读取失败原因'),
 'clean_text':('TEXT','清洗正文'),'clean_status':('TEXT','清洗状态，非可靠性审核'),
 'clean_warnings':('JSON','清洗质量提示'),'clean_blocks':('JSON','内容块、定位、保留排除依据'),
 'clean_counts':('JSON','清洗前后字符及块数'),'clean_version':('TEXT','清洗版本；null表示未清洗')}
IMAGE_COLUMNS={'byte_status':('TEXT','字节核对状态；null表示未处理'),
               'byte_details':('JSON','实际路径、哈希、尺寸或错误；不等于看图')}
CONCEPT_COLUMNS={'source_details':('JSON','名称、别名、QID等原始身份信息及来源引用'),
 'selected':('INTEGER','1入选、0未入选、null尚未选择'),'selection_reason':('TEXT','选择原因'),
 'document_count':('INTEGER','已关联文档数'),'image_count':('INTEGER','已关联图片数'),
 'readable_documents':('INTEGER','可读文档数'),'verified_images':('INTEGER','字节通过图片数'),
 'material_status':('TEXT','有资料或当前范围缺资料'),'knowledge_status':('TEXT','知识处理状态，非审核结论')}
# Compatibility views feed existing model adapters; they do not store step outputs.
VIEWS={
 'document_texts':'SELECT doc_id,raw_text AS text,raw_sha256 AS sha256,read_status AS status,read_error AS error FROM documents WHERE read_status IS NOT NULL',
 'clean_documents':'SELECT doc_id,clean_text AS text,clean_status AS status,clean_warnings AS warnings,clean_blocks AS blocks,clean_counts AS counts,raw_sha256 AS source_sha256,clean_version AS version FROM documents WHERE clean_version IS NOT NULL',
 'image_checks':'SELECT image_id,byte_status AS status,byte_details AS details FROM images WHERE byte_status IS NOT NULL',
 'selected_concepts':'SELECT concept_ref,selected,selection_reason AS reason FROM concepts WHERE selected IS NOT NULL',
 'concept_coverage':'SELECT concept_ref,document_count,image_count,readable_documents,verified_images,material_status AS status,knowledge_status FROM concepts WHERE material_status IS NOT NULL',
}
BUSINESS_SCHEMAS={name:({**SCHEMAS[name][0],**extra,**({'concept_refs':('JSON','来源已有概念引用；非身份核验'),'association_issues':('JSON','无关联、未知概念或歧义提示')} if name!='concepts' else {})},SCHEMAS[name][1]) for name,extra in [
 ('concepts',CONCEPT_COLUMNS),('documents',DOCUMENT_COLUMNS),('images',IMAGE_COLUMNS)]}


class ColumnTables(Tables):
    def __init__(self,path):
        schemas={k:v for k,v in SCHEMAS.items() if k not in VIEWS}
        schemas.update(BUSINESS_SCHEMAS)
        super().__init__(path,schemas)
        for table,sql in VIEWS.items():self.db.execute(f'CREATE VIEW IF NOT EXISTS {table} AS {sql}')
        # Decode compatibility view columns with their historical schema.
        self.schemas.update({k:SCHEMAS[k] for k in VIEWS});self.db.commit()

    def put(self,table,row):
        if table=='selected_concepts':
            return self.extend('concepts',row['concept_ref'],{'selected':row['selected'],'selection_reason':row['reason']})
        if table=='concept_coverage':
            return self.extend('concepts',row['concept_ref'],{
                ('material_status' if k=='status' else k):v for k,v in row.items() if k!='concept_ref'})
        if table in BUSINESS_SCHEMAS:
            base=SCHEMAS[table][0]
            if set(row)==set(base):
                key=self.schemas[table][1][0]
                old=self.db.execute(f'SELECT {",".join(base)} FROM {table} WHERE {key}=?',(row[key],)).fetchone()
                if old is not None:
                    expected=tuple(json.dumps(row[k],ensure_ascii=False) if typ=='JSON' else row[k] for k,(typ,_) in base.items())
                    if old!=expected:raise ValueError('source fields changed; new run required')
                    return
                row={**{k:None for k in self.schemas[table][0]},**row}
        return super().put(table,row)

    def extend(self,table,key,changes):
        fields,keys=self.schemas[table];pk=keys[0]
        if not set(changes)<=fields.keys() or pk in changes:raise ValueError('invalid extension columns')
        old=self.db.execute(f'SELECT {",".join(changes)} FROM {table} WHERE {pk}=?',(key,)).fetchone()
        if old is None:raise ValueError('cannot extend a missing object')
        values=[json.dumps(v,ensure_ascii=False) if fields[k][0]=='JSON' else v for k,v in changes.items()]
        # Expansion is immutable once a non-null value was saved; changing code/config requires a new run.
        if any(a is not None and not (fields[k][0]=='JSON' and a=='null') and a!=b for k,a,b in zip(changes,old,values)):raise ValueError('extension changed; use a new run')
        self.db.execute(f'UPDATE {table} SET '+','.join(f'{k}=?' for k in changes)+f' WHERE {pk}=?',values+[key])


class ReadDocumentColumns(ReadDocument):
    async def __call__(self,doc):
        if doc.get('read_status') is not None:return doc
        result=await super().__call__(doc)
        return {**doc,'raw_text':result['text'],'raw_sha256':result['sha256'],
            'read_status':result['status'],'read_error':result['error']}


class CleanDocumentColumns(CleanDocument):
    async def __call__(self,doc):
        if doc.get('clean_version') is not None:return doc
        result=await super().__call__({'doc_id':doc['doc_id'],'text':doc['raw_text'],
            'sha256':doc['raw_sha256'],'status':doc['read_status'],'error':doc['read_error']})
        return {**doc,**{'clean_'+k:result[k] for k in ('text','status','warnings','blocks','counts','version')}}


class CheckImageColumns(CheckImage):
    async def __call__(self,img):
        if img.get('byte_status') is not None:return img
        result=await super().__call__(img)
        return {**img,'byte_status':result['status'],'byte_details':result['details']}


class SaveColumns:
    concurrency=1;queue_depth=8;catch=()
    def __init__(self,tables,table,columns):self.tables=tables;self.table=table;self.columns=columns;self.n=0
    async def __call__(self,row):
        key=self.tables.schemas[self.table][1][0]
        self.tables.extend(self.table,row[key],{k:row[k] for k in self.columns})
        self.n+=1
        if self.n%100==0:self.tables.db.commit()
        return row
    async def aclose(self):self.tables.db.commit()


class ColumnFlow(DatasetFlow):
    table_type=ColumnTables
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        with run_lock(self.run):immutable(self.run/'column_manifest.json',{'schema':'column-expansion/1','business_schemas':BUSINESS_SCHEMAS})

    def reuse_source_adaptation(self,previous_run):
        """Reuse only unchanged source projections/links, never processing columns."""
        import ast
        from pathlib import Path
        from .contracts import read,digest,source_code
        previous=Path(previous_run).resolve()
        if previous==self.run:raise ValueError('source cache must be another run')
        old=read(previous/'record_manifest.json');manifest=read(previous/'dataset_manifest.json')
        if old['sources']!=self.raw.sources or old['config']['max_records_per_source']!=self.config['max_records_per_source']:
            raise ValueError('source adaptation scope differs')
        saved=read(previous/'record_code_snapshot.json')
        def adapter(s):return ast.dump(next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='AdaptSource'))
        def source_step(s):
            cls=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='DatasetFlow')
            return ast.dump(next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='step'))
        current=source_code()
        if digest(saved)!=old['code_hash'] or adapter(saved['dataset_flow.py'])!=adapter(current['dataset_flow.py']) or source_step(saved['dataset_flow.py'])!=source_step(current['dataset_flow.py']):
            raise ValueError('source adaptation changed; rebuild from original records')
        names=['concept_sources','concept_pages','concepts','documents','images','document_links','image_links',
               'image_roles','gallery_captions','concept_edges','unmatched_documents','unmatched_images']
        for name in names:
            if digest(manifest['schemas'][name])!=digest(SCHEMAS[name]):raise ValueError('source schema changed')
        self.raw._check()
        with run_lock(previous),run_lock(self.run):
            db=self.tables.db
            if db.execute('SELECT 1 FROM concepts LIMIT 1').fetchone():raise ValueError('source cache requires empty destination')
            db.execute('ATTACH DATABASE ? AS source_cache',((previous/'datasets.sqlite').as_uri()+'?mode=ro',))
            if not db.execute("SELECT 1 FROM source_cache.completed WHERE step='read_sources'").fetchone():raise ValueError('source adaptation incomplete')
            immutable(self.run/'source_adaptation_reuse.json',{'run':str(previous),'manifest_hash':digest(manifest),
                'tables':names,'scope':'Source projections and original associations only. Processing columns are not copied.'})
            try:
                for name in names:
                    cols=','.join(SCHEMAS[name][0])
                    db.execute(f'INSERT INTO {name} ({cols}) SELECT {cols} FROM source_cache.{name}')
                db.execute('INSERT INTO source_originals SELECT * FROM source_cache.source_originals')
                for table,key in [('document_links','doc_id'),('image_links','image_id')]:
                    db.execute(f'CREATE INDEX IF NOT EXISTS {table}_material ON {table}({key})')
                db.execute('CREATE INDEX IF NOT EXISTS pages_lookup ON concept_pages(lang,page_id)')
                db.execute("INSERT INTO completed VALUES ('read_sources')");db.commit()
            except BaseException:
                db.rollback();raise
            finally:db.execute('DETACH DATABASE source_cache')

    @classmethod
    def open_saved(cls,run):
        from pathlib import Path
        from .contracts import read
        read(Path(run)/'column_manifest.json')
        return super().open_saved(run)

    def documents(self):
        return self.tables.dataset('documents',JOINS['documents_to_read'])
    def images(self):
        return self.tables.dataset('images',JOINS['images_to_check'])
    def document_chain(self):
        """A real demiflow operator chain; durable read output survives later cleaning failures."""
        return (self.documents()
            .map_async(ReadDocumentColumns(self.dataset))
            .map_async(SaveColumns(self.tables,'documents',list(DOCUMENT_COLUMNS)[:4]))
            .map_async(CleanDocumentColumns())
            .map_async(SaveColumns(self.tables,'documents',list(DOCUMENT_COLUMNS)[4:])))
    def image_chain(self):
        return (self.images().map_async(CheckImageColumns(self.dataset))
            .map_async(SaveColumns(self.tables,'images',IMAGE_COLUMNS)))

    def _pending(self,input_table,output_table,sql=None):
        if input_table in BUSINESS_SCHEMAS:
            key=self.tables.schemas[output_table][1][0]
            query=f'SELECT x.* FROM ({sql or "SELECT * FROM "+input_table}) x WHERE NOT EXISTS (SELECT 1 FROM {output_table} y WHERE y.{key}=x.{key})'
            return self.tables.dataset(input_table,query)
        return super()._pending(input_table,output_table,sql)

    async def step(self,step):
        if step=='summarize_concepts':
            if not all(self.tables.db.execute('SELECT 1 FROM completed WHERE step=?',(s,)).fetchone() for s in ['clean_documents','check_images']):
                raise ValueError('Both document and image branches must finish before summary')
        if step not in {'read_documents','clean_documents','check_images','process_documents','process_images'}:
            result=await super().step(step)
            if step=='read_sources':
                with run_lock(self.run):
                    self.tables.db.execute('CREATE INDEX IF NOT EXISTS concept_source_ref ON concept_sources(concept_ref)')
                    # Only identity metadata is grouped here; documents/images remain separate rows.
                    self.tables.db.execute("""UPDATE concepts SET source_details=(
                      SELECT json_group_array(json_object('name',s.name,'aliases',json(s.aliases),'qid',s.qid,
                        'taxonomy',json(s.taxonomy),'source_id',s.source_id,'source_path',s.source_path,'source_row',s.source_row))
                      FROM concept_sources s WHERE s.concept_ref=concepts.concept_ref) WHERE source_details IS NULL OR source_details='null'""")
                    for table,key,links,unmatched in [('documents','doc_id','document_links','unmatched_documents'),('images','image_id','image_links','unmatched_images')]:
                        self.tables.db.execute(f"CREATE INDEX IF NOT EXISTS {unmatched}_material ON {unmatched}({key})")
                        self.tables.db.execute(f"""UPDATE {table} SET concept_refs=(SELECT json_group_array(concept_ref) FROM {links} l WHERE l.{key}={table}.{key}),
                          association_issues=(SELECT json_group_array(json_object('concept_ref',u.concept_ref,'reason',u.reason)) FROM {unmatched} u WHERE u.{key}={table}.{key})
                          WHERE concept_refs IS NULL OR concept_refs='null'""")
                    self.tables.db.commit()
            return result
        def work():
            self.raw._check()
            with run_lock(self.run):
                t=self.tables
                labels={'process_documents':['read_documents','clean_documents'],'process_images':['check_images']}.get(step,[step])
                if all(t.db.execute('SELECT 1 FROM completed WHERE step=?',(s,)).fetchone() for s in labels):return {'step':step,'reused':True}
                prerequisite='join_images' if step in {'read_documents','process_documents','process_images','check_images'} else 'read_documents'
                if not t.db.execute('SELECT 1 FROM completed WHERE step=?',(prerequisite,)).fetchone():raise ValueError(f'Run {prerequisite} first')
                if step=='process_documents':ds=self.document_chain()
                elif step in {'process_images','check_images'}:ds=self.image_chain()
                elif step=='read_documents':ds=self.documents().map_async(ReadDocumentColumns(self.dataset)).map_async(SaveColumns(t,'documents',list(DOCUMENT_COLUMNS)[:4]))
                else:ds=self.documents().map_async(CleanDocumentColumns()).map_async(SaveColumns(t,'documents',list(DOCUMENT_COLUMNS)[4:]))
                ds.run_stream(log_every=0)
                for s in labels:t.db.execute('INSERT OR IGNORE INTO completed VALUES (?)',(s,))
                t.db.commit();return {'step':step,'reused':False}
        return await asyncio.to_thread(work)
