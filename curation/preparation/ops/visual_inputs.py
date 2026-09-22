from curation.preparation.records import run_records
"""Single-row adapters for an independently runnable visual-material branch."""
from pathlib import Path

from curation.preparation.contracts import digest, immutable

from curation.preparation.ops.image_pixels import inspect_lake_image
from curation.preparation.ops.visual_materials import ReuseImageAnnotations


class PrepareVisualInput:
    """Accept concept/images rows, including historical knowledge exports.

    Article text and old identity/filter decisions are deliberately not evidence.
    The new pixel review must establish the requested concept association.
    """
    def __init__(self, run, dataset, source, annotation_ref=None, annotation_config_id=None):
        self.run, self.dataset, self.source = Path(run), Path(dataset), source
        self.index = ReuseImageAnnotations(annotation_ref, annotation_config_id)

    def __call__(self, row):
        concept = row.get('concept')
        if not isinstance(concept, str) or not concept.strip() or not isinstance(row.get('images'), list):
            raise ValueError('Visual input requires concept and images[]')
        materials = []
        for image in row['images']:
            record = dict(image.get('record', image))
            prior = image.get('bytes', {})
            expected = record.get('sha256') or prior.get('sha256')
            if prior.get('sha256') and expected != prior['sha256']:
                raise ValueError('Conflicting source image hashes')
            record['sha256'] = expected
            checked = inspect_lake_image({**record, 'path':prior.get('path') or record.get('path')})
            # Known generated provenance must survive the byte verifier.
            origins = [prior.get('generation_origin'), record.get('generation_origin')]
            checked['generation_origin'] = next((x for x in origins if x in {'generated','synthetic','ai_generated'}),
                                                 next((x for x in origins if x), 'not_verified'))
            if checked.get('status') == 'verified_bytes':
                # 观察证据绑定内容身份（SHA + 来源），不依赖可消失的文件路径。
                run_records(self.run).put('observed_asset/' + checked['sha256'],
                          {'path': str(checked.get('path')), 'actual_sha256': checked.get('sha256'),
                           'byte_size': checked.get('byte_size'), 'asset_source': checked.get('asset_source'),
                           'status': 'verified_bytes'})
            indexed = self.index({**record, 'byte_details': checked})
            materials.append({'kind': image.get('kind', 'legacy_images'),
                              'record': indexed, 'bytes': checked,
                              'provenance': image.get('provenance') or self.source})
        # Optional concept metadata disambiguates names; image captions are excluded.
        definition = row.get('concept_record')
        if definition:
            materials.append({'kind':'legacy_concepts', 'record':definition, 'provenance':self.source})
        return {'case_id':'visual_'+digest([concept, materials])[:20],
                'concept_ref':'legacy:'+concept, 'identity':{'target_label':concept},
                'cleaned_materials':materials, 'source_units':[],
                'input_provenance':self.source, 'blocked':None}


def visual_pack(row):
    return {'pipeline_version':'V2', 'publication_kind':'visual_materials',
            'concept':row['identity']['target_label'], 'case_id':row['case_id'],
            'status':'visual_only', 'status_reason':'Article branch not executed',
            'knowledge':[], 'images':[m for m in row['cleaned_materials']
                                      if m['kind'] in {'legacy_images','qid_images'}],
            'visual_materials':row.get('visual_materials', []),
            'visual_publication_issues':row.get('visual_publication_issues', []),
            'audit':{'image_decisions':row.get('image_decisions', []),
                     'image_material_scope':row.get('image_material_scope', {}),
                     'source':row.get('input_provenance')}}


def merge_visual_packs(a, b):
    if a is None:
        return b
    return {**a, 'images':a['images']+b['images'],
            'visual_materials':list({v['publication']['sha256']:v for v in
                                    a['visual_materials']+b['visual_materials']}.values()),
            'visual_publication_issues':a['visual_publication_issues']+b['visual_publication_issues'],
            'audit':{'batches':a['audit'].get('batches', [a['audit']])+[b['audit']]}}


class ReplayVisualResponse:
    """Revalidate saved responses; never request missing independent reviews."""
    def __init__(self, reviews):
        self.reviews = {r['batch_id']:r for r in reviews}
        if len(self.reviews) != len(reviews):
            raise ValueError('Duplicate recorded review batch')

    def __call__(self, original):
        from curation.preparation.ops.image_filter import RecordPrimaryImageSelection, PrepareImageReview, ApplyConfirmedImageSelection
        row = PrepareImageReview()(RecordPrimaryImageSelection()(original))
        review = self.reviews.get(row['batch_id'])
        if review:
            if review['image_prompt'] != row['image_prompt'] or review['pixel_roles'] != row['pixel_roles']:
                raise ValueError('Saved review does not match the original prompt/pixels')
            row.update({k:review.get(k) for k in ('prompt_result','prompt_error','prompt_call')})
        elif row['review_required']:
            row['prompt_error'] = 'No recorded independent response; replay cannot generate one'
        return ApplyConfirmedImageSelection()(row)


def read_visual_input(data, ref, root, concepts=None):
    """Read entity inputs with native Dataset operators and an explicit concept scope."""
    from collect.material_schema import IMAGE_METADATA
    from collect.materials import generation_origin
    from curation.preparation.articles import article_record
    from curation.preparation.records import from_stage_row
    if ref.schema_name == 'pipeline_stage_rows':
        return data.read_lance(ref.resolve(root),version=ref.lance_version).map(from_stage_row)
    if ref.schema_name == 'articles':
        predicate="article_kind = 'knowledge'"
        if concepts:
            predicate += ' AND concept IN ('+', '.join("'"+c.replace("'","''")+"'" for c in concepts)+')'
        return data.read_lance(ref.resolve(root),version=ref.lance_version,filter=predicate).map(article_record).filter(lambda r:bool(r.get('images')))
    if ref.schema_name != 'raw_images':raise ValueError('Unsupported visual input entity')
    if not concepts or any(not isinstance(c,str) or not c.strip() for c in concepts):
        raise ValueError('Image entity visual input requires an explicit concepts list')
    selected=set(concepts)
    predicate=' OR '.join("array_contains(concepts, '"+c.replace("'","''")+"')" for c in sorted(selected))
    columns=['sha256','ext','byte_size','storage_mode',*IMAGE_METADATA.names]
    def expand(row):
        record={**row,'generation_origin':generation_origin(row)}
        for concept in sorted(set(row.get('concepts') or []) & selected):
            yield {'concept':concept,'images':[{'kind':'legacy_images','record':record}]}
    def combine(a,b):
        return {'concept':b['concept'],'images':(a['images'] if a else [])+b['images']}
    return (data.read_lance(ref.resolve(root),version=ref.lance_version,columns=columns,filter=predicate)
        .flat_map(expand).reduce_by_key('concept',combine))
