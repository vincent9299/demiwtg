"""Re-read raw documents named by a frozen boundary; no model calls."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest,immutable,source_code,runtime_version,run_lock
from .ops.dataset_operators import ReadDocument,CleanDocument

class CompareCleaning:
    def __init__(self,dataset):self.reader=ReadDocument(dataset);self.cleaner=CleanDocument()
    async def __call__(self,row):
        old=row['record'];read=await self.reader(old)
        if read['read_status']!='readable' or read['raw_sha256']!=old['raw_sha256']:raise ValueError('Raw changed/unavailable')
        new=await self.cleaner(read)
        for b in new['clean_blocks']:
            assert new['raw_text'][b['raw_start']:b['raw_end']]==b['raw_text']
            if 'clean_start' in b:assert new['clean_text'][b['clean_start']:b['clean_end']]==b['text']
        return {'material_id':row['material_id'],'document':new,'before_text':old['clean_text'],
                'before_chars':len(old['clean_text']),'after_chars':len(new['clean_text'])}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['source','dataset','run']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    with run_lock(a.run):
        rows=[m for line in a.source.open() for m in json.loads(line)['identity_materials'] if 'cleaning' in m]
        manifest={'purpose':'Re-read raw documents from saved record metadata; program cleaning only',
                  'source':str(a.source),'source_sha256':digest(a.source.read_bytes()),
                  'code':source_code(),'runtime':runtime_version(),'model_calls':0}
        immutable(a.run/'manifest.json',manifest)
        ds=local_data().from_items(rows).map_async(CompareCleaning(a.dataset),concurrency=1).checkpoint(a.run/'documents.jsonl',version=digest(manifest))
        results=list(ds.iter_rows())
        report={'documents':len(results),'model_calls':0,'source_and_clean_offsets_checked':True,
                'rows':[{'material_id':r['material_id'],'before_chars':r['before_chars'],'after_chars':r['after_chars'],
                         'engine':r['document']['clean_extraction']['engine'],'media':len(r['document']['source_media']),
                         'status':r['document']['clean_status']} for r in results]}
        immutable(a.run/'report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
