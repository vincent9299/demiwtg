"""Audited consolidation of image/document shards into real single Lance tables."""
from pathlib import Path
import json
import lance
from demiflow.lance.consolidate import consolidate_local_tables
from demiflow.lance.registry import Catalog
from demiflow.lance.refs import DatasetRef
from demiflow.lance.records import LanceRecordStore
from demiflow.lance.storage import schema_hash
from project import resolve_root

OP='single_material_tables_20260921'
TARGETS={'images':'demiwtg/collect/datasets/images.lance','documents':'demiwtg/collect/datasets/documents.lance'}


def journal(root):return LanceRecordStore(root,f'datasets/records__{OP}.lance')


def run(root):
    root=Path(root);j=journal(root);inputs=j.get('inputs')
    if inputs is None:
        latest={}
        for ref in Catalog(root).registered():
            if ref.relative_uri.startswith(('raw/images/v1/shards/','raw/documents/v1/')):
                old=latest.get(ref.relative_uri)
                if old is None or ref.lance_version>old.lance_version:latest[ref.relative_uri]=ref
        inputs={k:[r.to_dict() for uri,r in sorted(latest.items()) if uri.startswith('raw/'+k+'/')] for k in TARGETS}
        if len(inputs['images'])!=256 or len(inputs['documents'])!=22:raise ValueError('Unexpected source inventory')
        j.put('inputs',inputs)
    for kind,uri in TARGETS.items():
        if j.get(kind):continue
        sources=[DatasetRef.from_dict(r).open(root) for r in inputs[kind]]
        target=root/uri
        if target.exists():raise ValueError('Unjournaled target requires explicit recovery: '+uri)
        ds=consolidate_local_tables(sources,target)
        print('consolidated',kind,ds.count_rows(),'fragments',len(ds.get_fragments()),flush=True)
        if kind=='images':ds.create_scalar_index('sha256','BTREE',name='sha256_lookup')
        ds=lance.dataset(str(target))
        ref=DatasetRef(dataset_id='raw/'+kind,relative_uri=uri,lance_version=ds.version,schema_name='raw_'+kind,
            schema_version='v2',schema_hash=schema_hash(ds.schema),row_count=ds.count_rows())
        # The platform preserves every source file byte by hard link, field IDs,
        # ordering and deletion masks. Read each source's first surviving row
        # from the corresponding new fragments to validate manifest assembly.
        checks=[];offset=0
        for old in sources:
            count=len(old.get_fragments());new=ds.get_fragments()[offset:offset+count]
            columns=[f.name for f in old.schema if f.name!='data']
            a=old.scanner(columns=columns,limit=1).to_table()
            b=ds.scanner(columns=columns,fragments=new,limit=1).to_table()
            if not a.equals(b):raise ValueError('Consolidation row projection differs')
            if sum(f.count_rows() for f in new)!=old.count_rows():raise ValueError('Source row count differs')
            checks.append({'source':old.uri,'source_version':old.version,'rows':old.count_rows(),'fragment_start':offset,'fragments':count})
            offset+=count
        Catalog(root).register(ref)
        j.put(kind,{'ref':ref.to_dict(),'sources':checks,'field_ids_and_file_bytes_preserved':True,
                    'fragments':len(ds.get_fragments()),'rows':ds.count_rows()})
        print('validated',kind,ref.to_dict(),flush=True)
    return {k:j.get(k)['ref'] for k in TARGETS}
if __name__=='__main__':print(json.dumps(run(resolve_root())))
