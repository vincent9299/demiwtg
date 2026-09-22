"""Source catalog for current article construction."""
from curation.preparation.contracts import digest


class SourceCatalog:
    def __call__(self,row):
        from curation.preparation.ops.identity import material_id
        records={material_id(m):m['record'] for m in row['cleaned_materials']}
        sources={}
        for p in row['material_pack']['passages']:
            r=records.get(p['material_id'],{})
            sources[p['source_id']]={'title':r.get('title') or r.get('url') or p['source_id'],'url':r.get('url') or r.get('source_url') or ''}
        images={}
        for m in row['material_pack']['images']:
            r=m['record'];images[m['image_id']]={'title':r.get('title') or '图片来源','url':r.get('landing_url') or r.get('content_url') or r.get('url') or ''}
        return {'concept':row['identity']['target_label'],'sources':sources,'image_sources':images}
