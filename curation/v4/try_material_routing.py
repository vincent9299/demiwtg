"""Encode the same pre-extraction inputs with native demiflow; freeze every stage."""
import argparse,json,time
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code,runtime_version,run_lock
from .try_paragraph_similarity import file_sha
from .ops.material_routing import PrepareRoutingMaterials,RawPassageRows,EncodeImageTextMaterials
from .ops.paragraph_similarity import EmbedParagraphBatch


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--text-model',type=Path,required=True);p.add_argument('--image-model',type=Path,required=True);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    with run_lock(a.run):
        manifest={'source':str(a.source.resolve()),'source_sha256':file_sha(a.source),'models':{str(m.resolve()):{f.name:file_sha(f) for f in m.iterdir() if f.is_file() and f.suffix in ['.json','.safetensors','.txt','.model']} for m in [a.text_model,a.image_model]},'source_code':source_code(),'runtime':runtime_version(),'config':{'device':'cpu','dtype':'float32','text_batch':2,'siglip_batch':8,'siglip_text_windows':'model context size with 16-token overlap; all windows retained','native_policy':'exact URL + closest selected source span within 500 chars and compatible section'}}
        immutable(a.run/'manifest.json',manifest);version=digest(manifest);data=local_data();started=time.monotonic()
        materials=data.read_json(str(a.source)).map(PrepareRoutingMaterials()).checkpoint(a.run/'materials.jsonl',version=version)
        text=materials.flat_map(RawPassageRows()).group_batches('embedding_bucket',max_rows=2,output='items').checkpoint(a.run/'text_batches.jsonl',version=version)
        text.map_cached(EmbedParagraphBatch(a.text_model),cache_dir=a.run/'cache/text_embedding',version=version).checkpoint(a.run/'text_embeddings.jsonl',version=version)
        text_done=time.monotonic();print('raw text embeddings complete',flush=True)
        materials.map_cached(EncodeImageTextMaterials(a.image_model),cache_dir=a.run/'cache/image_text',version=version).checkpoint(a.run/'image_text_embeddings.jsonl',version=version)
        immutable(a.run/'timing.json',{'text_stage_s':text_done-started,'image_text_stage_s':time.monotonic()-text_done,'scope':'includes actor/model load and checkpoint I/O; excludes model download and later generation'})
        print('complete',a.run,flush=True)

if __name__=='__main__':main()
