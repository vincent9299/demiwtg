"""Read fixed material tables and explicit business projections."""
from pathlib import Path
import json
from demiflow.lance.registry import Catalog
from collect.concepts import resolve_concept_release
from collect.material_schema import IMAGES_URI,DOCUMENTS_URI


class SourceUnavailable(ValueError):
    pass


def resolve_source(dataset, kind):
    root=Path(dataset)
    if kind=='legacy_concepts':
        master=resolve_concept_release(root);ref=master['dataset_ref']
        source={'kind':kind,'dataset_ref':ref.to_dict(),'master_release':master['release_id']}
    else:
        uri={'legacy_docs':DOCUMENTS_URI,'wiki_pages':DOCUMENTS_URI,'legacy_images':IMAGES_URI,
             'qid_concepts':DOCUMENTS_URI}[kind]
        matches=[r for r in Catalog(root).registered() if r.relative_uri==uri]
        if not matches:raise SourceUnavailable('Required source is not registered: '+uri)
        ref=max(matches,key=lambda r:r.lance_version);source={'kind':kind,'dataset_ref':ref.to_dict()}
    ref.open(root)
    return ref,source



def read_source(data,dataset,ref,source,*,limit=None,ids=None):
    """Read one fixed table; document types are predicates, not separate tables."""
    columns=[f.name for f in ref.open(dataset).schema if f.name!='data']
    predicates=[];kind=source['kind']
    if kind == 'legacy_images':
        # Gathering raw evidence must not copy every annotation/review into every source row.
        from collect.material_schema import IMAGE_METADATA
        columns = ['sha256','ext','byte_size','storage_mode',*IMAGE_METADATA.names]
    if kind == 'qid_concepts':
        columns = ['qid', 'language', 'page_id', 'title']
        predicates.append("document_type = 'wiki' AND qid IS NOT NULL AND page_id IS NOT NULL")
    if kind in ('legacy_docs','wiki_pages'):
        predicates.append("document_type = '"+('web' if kind=='legacy_docs' else 'wiki')+"'")
    if ids and kind in ('legacy_images','legacy_docs'):
        values=[i.removeprefix('legacy:') for i in ids if i.startswith('legacy:')]
        predicates.append('('+' OR '.join("array_contains(concepts, '"+v.replace("'","''")+"')" for v in values)+')' if values else 'false')
    return data.read_lance(ref.resolve(dataset),version=ref.lance_version,columns=columns,
        limit=limit,filter=' AND '.join(predicates) or None).map(DecodeSourceRow(source))


class DecodeSourceRow:
    def __init__(self,source):self.source=source
    def __call__(self,row):
        kind=self.source['kind']
        if kind == 'qid_concepts':
            language = row.get('language')
            value = {'qid': row['qid']}
            if language in ('en', 'zh'):
                value[language] = {'page_id': row['page_id'], 'title': row['title']}
            error = None
        elif kind == 'legacy_concepts':
            try:value,error=json.loads(row['raw_payload']),None
            except (ValueError,TypeError) as exc:value,error=None,str(exc)
            if kind=='legacy_concepts' and isinstance(value,dict):
                value.update({k:row[k] for k in ('name','aliases','carriers') if k in row})
        else:
            value=dict(row);error=None
            if kind in ('legacy_docs','wiki_pages'):
                value['lang']=row.get('language')
                value['url']=row.get('source_identity')
        return {'row':row.get('source_row',0)+1,'value':value,'error':error,
                'source_ref':self.source['dataset_ref'],'source':self.source}
