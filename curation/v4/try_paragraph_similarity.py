"""Frozen small-batch embedding experiment using native demiflow operators."""
import argparse,json,importlib.metadata
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .ops.cross_batch import PlanCrossBatchReview
from .ops.paragraph_similarity import ParagraphRows,EmbedParagraphBatch,FindParagraphNeighbors


def file_sha(path):
    import hashlib
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--threshold',type=float,default=.8);p.add_argument('--top-k',type=int,default=3);a=p.parse_args()
    with run_lock(a.run):
        weights={f.name:file_sha(f) for f in a.model.iterdir() if f.is_file() and f.suffix in ['.json','.safetensors','.txt']}
        config={'model':str(a.model.resolve()),'weights':weights,'device':'cpu','dtype':'float32','pooling':'last_token_left_padding','normalize':True,'instruction':None,'representations':['title_newline_body','body'],'batch_paragraphs':2,'max_embedding_tokens':2048,'threshold':a.threshold,'top_k':a.top_k,'max_group_members':4,'max_group_content_tokens':2000}
        manifest={'source':str(a.source.resolve()),'source_sha256':file_sha(a.source),'config':config,'source_code':source_code(),'runtime':runtime_version(),'libraries':{x:importlib.metadata.version(x) for x in ['torch','transformers','numpy']}}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest);data=local_data()
        paragraphs=(data.read_records(str(a.source),format='json',item_prefix='concepts.item').map(lambda r:r['value'] if not r.get('error') else (_ for _ in ()).throw(ValueError(r['error']))).flat_map(ParagraphRows()).checkpoint(a.run/'paragraphs.jsonl',version=version))
        print('paragraph inputs frozen',flush=True)
        batches=paragraphs.group_batches('embedding_bucket',max_rows=2,output='items').checkpoint(a.run/'batches.jsonl',version=version)
        (batches.map_cached(EmbedParagraphBatch(a.model),cache_dir=a.run/'cache/embedding',version=version).checkpoint(a.run/'embedded_batches.jsonl',version=version))
        print('embeddings complete',flush=True)
        embeddings=data.read_json(str(a.run/'embedded_batches.jsonl')).flat_map(lambda r:r['items']).checkpoint(a.run/'embeddings.jsonl',version=version)
        # This experiment has 30 paragraphs. Production needs blockwise/ANN retrieval,
        # not unbounded concept accumulation or independent windows that miss neighbors.
        groups=embeddings.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'items':acc['items']+[r]},initial={'items':[]})
        groups.map(FindParagraphNeighbors(a.threshold,a.top_k)).checkpoint(a.run/'neighbors.jsonl',version=version)
        groups.map(PlanCrossBatchReview(a.threshold,a.top_k)).checkpoint(a.run/'cross_batch_plan.jsonl',version=version)
        print('complete',a.run,flush=True)

if __name__=='__main__':main()
