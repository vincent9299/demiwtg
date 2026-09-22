"""Verify every published image and each source-fragment group's Blob bytes."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from demiflow.lance.records import LanceRecordStore
from demiflow.lance.refs import DatasetRef
from demiflow.lance.registry import ReleaseRegistry
from collect.assets import AssetReader
from project import resolve_root


def verify(root):
    journal = LanceRecordStore(root, 'runs/maintenance/single_material_tables_20260921/records.lance')
    ref = DatasetRef.from_dict(journal.get('images')['ref'])
    images = ref.open(root)
    reader = AssetReader(datasets_root=root, version=ref.lance_version)
    visual = ReleaseRegistry(root).get('visual_materials_v2_20260921')
    visual_ref = DatasetRef.from_dict(json.loads(visual['table_refs'])[0])
    keys = set(visual_ref.open(root).to_table(columns=['sha256'],filter='published = true')['sha256'].to_pylist())
    published_unique = len(keys)
    article = ReleaseRegistry(root).get('knowledge_materials_current_20260921')
    article_ref = DatasetRef.from_dict(json.loads(article['table_refs'])[0])
    figure_count = 0
    for row in article_ref.open(root).to_table(columns=['raw_payload']).to_pylist():
        for image in json.loads(row['raw_payload'])['published_images']:
            key = image.get('bytes',{}).get('sha256') or image.get('sha256')
            if not key: raise ValueError('Published figure without SHA')
            keys.add(key); figure_count += 1
    # Each old physical shard is now a set of internal fragments in one table.
    for source in journal.get('images')['sources']:
        start=source['fragment_start']; count=source['fragments']
        rows=images.scanner(columns=['sha256'],fragments=images.get_fragments()[start:start+count],
            filter="availability = 'available'",limit=1).to_table()
        keys.update(rows['sha256'].to_pylist())
    def check(key):
        result = reader.resolve(key)
        if result.status != 'ok': return {'sha256':key,'error':result.status}
        try:
            with Image.open(io.BytesIO(result.data)) as image: image.verify()
        except Exception as exc: return {'sha256':key,'error':str(exc)}
        return None
    errors=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, error in enumerate(pool.map(check, sorted(keys)),1):
            if error: errors.append(error)
            if i%1000==0:print(i,'/',len(keys),flush=True)
    proof=dict(image_ref=ref.to_dict(),published_visual_unique=published_unique,
        article_figures=figure_count,verified_unique=len(keys),source_groups=256,errors=errors)
    if errors: raise ValueError(json.dumps(proof,ensure_ascii=False))
    journal.put('pixels',proof)
    print(json.dumps(proof,ensure_ascii=False),flush=True)
    return proof

if __name__ == '__main__':verify(resolve_root())
