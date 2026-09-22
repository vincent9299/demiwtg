"""Real material, isolated lake, explicitly simulated model responses.

Run only with DEMIWTG_ACCEPTANCE_SOURCE_ROOT. No production writes, network,
GPU allocation, formal training publication, or factual-quality assertion.
"""
import os
import json
import io
from pathlib import Path
from contextlib import nullcontext
import pytest
import lance
import pyarrow as pa
from demiflow.lance.registry import Catalog,write_registered_table
from demiflow.lance.refs import DatasetRef
from demiflow.standalone import local_data
from collect.materials import sha,source_record
from collect.material_writer import write_images
from collect.material_schema import DOCUMENTS_URI,IMAGES_URI,IMAGES
from curation.preparation.image_schema import IMAGES_URI as CURATED_IMAGES_URI
from curation.preparation.articles import ARTICLES_URI
from curation.training.tests.test_authoring import publish_fixture,ingest,design_result,TASK_CHECKS,TARGET_CHECKS
from curation.preparation.records import saved_stage,run_records,read_record,run_state
from curation.training.tests.pipeline_runtime import config,load_pipeline

pytestmark=pytest.mark.skipif(not os.environ.get('DEMIWTG_ACCEPTANCE_SOURCE_ROOT'),reason='explicit read-only production source required')


def test_real_material_cross_chain(tmp_path,monkeypatch):
    source_root=Path(os.environ['DEMIWTG_ACCEPTANCE_SOURCE_ROOT']).resolve()
    root=tmp_path/'lake';monkeypatch.setenv('DEMIWTG_DATASETS_ROOT',str(root))
    concept='基本流程图';refs=Catalog(source_root).registered()
    article_ref=max((r for r in refs if r.relative_uri==ARTICLES_URI),key=lambda r:r.lance_version)
    visual_ref=max((r for r in refs if r.relative_uri==CURATED_IMAGES_URI),key=lambda r:r.lance_version)
    from curation.preparation.articles import article_record,write_articles
    from curation.preparation.images import assessment_record,write_curation
    article_row=article_ref.open(source_root).to_table(filter=f"concept = '{concept}' AND review_status = 'reviewed'").to_pylist()[0]
    article=article_record(article_row)
    visual_rows=visual_ref.open(source_root).to_table(columns=['sha256','concept_assessments'],filter=f"array_contains(published_concepts, '{concept}')").to_pylist()
    for r in visual_rows:
        r['concept_assessments']=[a for a in r['concept_assessments'] if a['concept']==concept and a['published']]
    assert article['published_passages'] and visual_rows
    visual_values=[assessment_record(r,a) for r in visual_rows for a in r['concept_assessments']]
    all_shas={m['sha256'] for m in visual_values}|{i.get('bytes',{}).get('sha256') for i in article['images']+article['published_images']}
    all_shas.discard(None)
    copied=[];missing=[];parents=[]
    for key in sorted(all_shas):
        uri=IMAGES_URI
        ref=max((r for r in refs if r.relative_uri==uri),key=lambda r:r.lance_version)
        idcol='sha256'
        ds=ref.open(source_root)
        values=ds.scanner(columns=[idcol,'ext'],filter=f"{idcol} = '{key}'",with_row_id=True).to_table().to_pylist()
        if not values:missing.append(key);continue
        blob=ds.take_blobs('data',ids=[values[0]['_rowid']])[0]
        if blob is None:missing.append(key);continue
        raw=blob.read();assert sha(raw)==key
        src=source_record({'concepts':[concept],'content_url':'urn:sha256:'+key},system='acceptance_copy',source_file=ref.relative_uri)
        copied.append({'sha256':key,'ext':values[0]['ext'],'data':raw,'byte_size':len(raw),'storage_mode':'lance_blob',
            'concepts':[concept],'sources':[src],'availability':'available','resolution':None})
        if ref.to_dict() not in parents:parents.append(ref.to_dict())
    write_images(root,copied)
    from curation.preparation.published import published_record
    delivery=published_record({**article,'_knowledge_sha256':sha(json.dumps(article,sort_keys=True))})
    assert not delivery['delivery_issues']
    assert all(i['bytes']['sha256'] in {r['sha256'] for r in copied} for i in article['published_images'])
    # All payload bytes are now in the isolated lake; there are no legacy image files.
    isolated_articles=write_articles(root,[article_row])
    article_input={'dataset_ref':isolated_articles.to_dict(),'release_id':article_row['release_ids'][0]}
    raw_ref=max((r for r in Catalog(root).registered() if r.relative_uri==IMAGES_URI),key=lambda r:r.lance_version)
    raw_version_before=raw_ref.lance_version
    isolated_visual=write_curation(root,visual_rows,source_ref=raw_ref)
    assert raw_ref.open(root).version==raw_version_before
    visual_input={'dataset_ref':isolated_visual.to_dict(),'release_id':visual_rows[0]['concept_assessments'][0]['release_ids'][0]}
    docs_ref=max((r for r in refs if r.relative_uri==DOCUMENTS_URI),key=lambda r:r.lance_version)
    ds=docs_ref.open(source_root);docs=ds.to_table(filter=f"document_type = 'web' AND array_contains(concepts, '{concept}')").to_pylist()
    isolated_docs,_,_=write_registered_table(root,DOCUMENTS_URI,schema=ds.schema,schema_name='raw_documents',schema_version='v1',rows_factory=lambda:iter(docs),fingerprint='real-docs')
    master=max((r for r in refs if r.relative_uri=='master/concepts/v1/concepts.lance'),key=lambda r:r.lance_version)
    concept_rows=master.open(source_root).to_table(filter=f"name = '{concept}'").to_pylist()
    master_copy,_,_=write_registered_table(root,'master/acceptance/concepts.lance',schema=master.open(source_root).schema,schema_name='reference_concepts',schema_version='v2',rows_factory=lambda:iter(concept_rows),fingerprint='real-concept')
    from curation.preparation import lake_inputs
    original=lake_inputs.resolve_source
    def resolve(dataset,kind):
        if kind=='legacy_concepts':return master_copy,{'kind':kind,'dataset_ref':master_copy.to_dict()}
        return original(dataset,kind)
    monkeypatch.setattr(lake_inputs,'resolve_source',resolve)
    from curation.preparation.pipeline import load_pipeline as knowledge_graph
    knowledge_run=tmp_path/'curation/training/runs/raw_knowledge'
    graph=knowledge_graph()
    gathered=graph(knowledge_run,root,project=tmp_path,ids=['legacy:'+concept],through='gather').take_all()
    assert gathered
    assert any(r['read_status']=='readable' for r in __import__('curation.preparation.stages',fromlist=['read_stage']).read_stage(knowledge_run,'documents_processed').take_all())
    assert graph(knowledge_run,root,project=tmp_path,ids=['legacy:'+concept],through='gather').take_all()==gathered
    # Exercise the actual independent visual graph on real pixels, with labeled
    # synthetic protocol responses. It must not touch a model service.
    from curation.preparation.visual_pipeline import load_graph as visual_graph
    from curation.preparation.tests.test_confirm_images import decision
    from curation.training.tests.test_v2_visual_training import description
    vgraph=visual_graph();calls=[]
    dataset_type=type(local_data().from_iter(lambda:iter([])))
    def respond(self,name,**kwargs):
        assert name=='select_images'
        def answer(row):
            calls.append(row['batch_id'])
            return {**row,'prompt_result':{'images':[{**decision(i,'keep'),'reason':'ACCEPTANCE STUB ONLY',
                'image_metadata':description(),'visual_support':{'supports':'ACCEPTANCE STUB ONLY','region':'whole','limitations':'not a factual review'}}
                for i in row['image_prompt']['image_ids']]},'prompt_call':{'acceptance_stub':True}}
        return self.map(answer)
    monkeypatch.setattr(dataset_type,'map_prompt_async',respond)
    vgraph['image_prompt_data']=lambda *a,**k:local_data()
    vgraph['image_review_service']=lambda *a,**k:nullcontext()
    visual_source=raw_ref.to_dict()
    visual_run=tmp_path/'curation/training/runs/visual'
    vgraph['run_visual_pipeline'](visual_run,visual_source,root,project=tmp_path,through='export',model_config={'image_annotations_ref':None,'concepts':[concept]}).take_all()
    before=len(calls)
    vgraph['run_visual_pipeline'](visual_run,visual_source,root,project=tmp_path,through='export',model_config={'image_annotations_ref':None,'concepts':[concept]}).take_all()
    assert len(calls)==before and before>=2
    monkeypatch.undo();monkeypatch.setenv('DEMIWTG_DATASETS_ROOT',str(root))
    # Feed the untouched historical machine-reviewed article + visual publication.
    from curation.preparation import records as storage
    actual_code=storage.code_version()
    monkeypatch.setattr(storage,'ROOT',tmp_path)
    monkeypatch.setattr(storage,'code_version',lambda:actual_code)
    upstream=[article_input];visuals=[visual_input]
    draft={'status':'ok','instruction':'为需要根据条件选择后续步骤的流程画出基本流程图。',
        'condition':'根据条件选择后续步骤','knowledge_application':'依据所给流程图知识选择判断符号及连接方式',
        'criteria':[{'requirement':'判断节点和分支连接符合给定资料','evidence':[1],
            'observable_region':'判断节点及连线','allowed_variation':'布局和颜色可不同'}],
        'edit_type':None,'anchor':'','preserve':[]}
    outputs={}
    for branch in ('benchmark','training'):
        run=tmp_path/('curation/training/runs/'+branch)
        cfg=config('offline',concepts=[concept],max_units=1,task_types=['t2i'],max_context_chars=2000000,
            max_reference_images=256,training_design='target_aware',design_target_candidates=2,target_candidates_per_task=2,
            scene_search={'image_ref':None,'external_providers':[]})
        pipeline=load_pipeline(branch)
        pipeline(run,upstream,cfg,visual_runs=visuals)
        requests=[r for k,r in run_records(run).items().items() if k.startswith('request/design_candidates/')]
        assert requests, saved_stage(run,'design')
        design_row=saved_stage(run,'design')[0]
        materials=design_row['materials']
        available=set(range(1,len(materials)+1))
        if branch=='training':
            option=next(o for o in design_row['target_reference_options']
                        if {'text','image'} <= {materials[n-1]['kind'] for n in o['evidence']})
            available=set(option['evidence'])
        text_index=next(i+1 for i,m in enumerate(materials) if m['kind']=='text' and i+1 in available)
        image_index=next(i+1 for i,m in enumerate(materials) if m['kind']=='image' and i+1 in available)
        evidence_indices=[text_index,image_index]
        current_draft={**draft,'criteria':[{**draft['criteria'][0],'evidence':evidence_indices}]}
        result=design_result(current_draft);result['candidates'][0]['evidence']=evidence_indices
        if branch=='training':
            result['candidates'][0].update(target_candidates=[option['target_candidate']],
                reference_selection_reason='ACCEPTANCE STUB ONLY: compatible published references')
        ingest(run,'design_candidates',result);pipeline(run,upstream,cfg,visual_runs=visuals)
        ingest(run,'review_task',{'checks':{k:True for k in TASK_CHECKS},'reason':'ACCEPTANCE STUB ONLY, not factual approval'})
        pipeline(run,upstream,cfg,visual_runs=visuals)
        if branch=='training':
            requests=[r for k,r in run_records(run).items().items() if k.startswith('request/review_target/')]
            assert requests,saved_stage(run,'incomplete')
            # Multiple requests are bound independently; no unbound success injection.
            from demiflow.operator_llm.lance_journal import submit_response
            for req in requests:
                native=req['native_offline'];model=read_record(native['request_ref'])['model']
                submit_response(root,native['request_ref'],{'result':{'checks':{k:True for k in TARGET_CHECKS},
                    'reason':'ACCEPTANCE STUB ONLY, not factual approval'}},model=model,metadata={'reviewer':'acceptance_stub','reviewer_kind':'assistant'})
            pipeline(run,upstream,cfg,visual_runs=visuals)
        ready=saved_stage(run,'ready');assert ready,saved_stage(run,'incomplete')
        before=run_state(run)['stages'];resumed=pipeline(run,upstream,cfg,visual_runs=visuals)
        assert resumed['new_stages']==[] and resumed['stages']==before
        outputs[branch]={'run':run,'config':cfg,'ready':ready}
    for row in outputs['training']['ready']:
        assert all(not s['loss'] for s in row['training_sample']['sequence'] if s['role']!='target')
        assert row['target']['sha256'] not in json.dumps(row['answer_input'])
    # The exact benchmark output becomes evaluation input, with frozen references.
    from curation.evaluation.native.runtime import load_graph
    from curation.evaluation.native.contracts import default_config
    from curation.evaluation.tests.test_native_evaluation import complete_rubrics
    from curation.evaluation.tests.test_judge_pipeline import install_fixture_generation,submit_judges
    from curation.preparation.records import store_blob
    questions=publish_fixture(tmp_path/'curation/training/runs/questions','selected',outputs['benchmark']['ready'])
    from curation.evaluation.native import contracts as ec
    monkeypatch.setattr(ec,'ROOT',Path(__file__).resolve().parents[3])
    actual_evaluation_code=ec.implementation()
    monkeypatch.setattr(ec,'ROOT',tmp_path)
    monkeypatch.setattr(ec,'implementation',lambda:actual_evaluation_code)
    erun=tmp_path/'curation/training/runs/evaluation';eg=load_graph();cfg=default_config()
    cfg['backends']={'t2i':['qwen2512'],'edit':['qwen2511']}
    eg['run_pipeline'](erun,questions,upstream,cfg,visual_runs=visuals)
    base_eval=eg['run_pipeline']
    eg['run_pipeline']=lambda *a,**k:base_eval(*a,**{**k,'visual_runs':visuals})
    prepared=(erun,eg,questions,upstream,cfg)
    from demiflow.operator_llm.lance_journal import submit_response
    for row in saved_stage(erun,'rubric_requests'):
        context=row['rubric_context'];author=context['author_criteria'][0]
        native=read_record(row['rubric_binding']['request_ref'])['native_offline']
        result={'status':'ready','audit':{'status':'ok','detail':'ACCEPTANCE STUB ONLY'},
            'criteria':[{'id':'R1','source_criterion_ids':[author['source_criterion_id']],
                'dimension':'alignment','check_category':'form_structure','importance':'core',
                'basis':'entailed','evidence_ids':author['evidence_ids'],'condition':draft['condition'],
                'visibility_required':True,**{k:author[k] for k in ('requirement','observable_region','allowed_variation')}}],
            'author_dispositions':[{'id':author['source_criterion_id'],'decision':'retained','criterion_ids':['R1'],'reason':'ACCEPTANCE STUB ONLY'}]}
        submit_response(root,native['request_ref'],{'result':result},model=cfg['judge']['model'],
            metadata={'reviewer':'acceptance_stub','reviewer_kind':'assistant','reasoning_effort':cfg['judge']['reasoning_effort']})
    eg['run_pipeline'](erun,questions,upstream,cfg,through='rubrics')
    assert all(r['rubric_status']=='frozen' for r in saved_stage(erun,'rubrics'))

    raw=copied[0]['data'];blob=store_blob(erun,raw)
    asset={'sha256':sha(raw),'blob_ref':blob.to_dict(),'path':None}
    gcalls=install_fixture_generation(prepared,asset)
    eg['run_pipeline'](erun,questions,upstream,cfg,through='judge',visual_runs=visuals)
    submit_judges(erun);eg['run_judging'](erun)
    assert saved_stage(erun,'scores') and all(r['judge_status']=='scored' for r in saved_stage(erun,'scores'))
    count=len(gcalls);assert eg['run_judging'](erun)['new_stages']==[] and len(gcalls)==count
    # Rendering must read actual lake pixels even though no old files exist.
    from curation.preparation.review_notebooks import write_review
    note=write_review(outputs['training']['run']);assert note.is_file()
    evidence={'status':'passed','scope':'real materials + simulated model responses in isolated lake',
        'formal_training_data_published':False,'real_model_calls':0,'concept':concept,
        'article_ref':article_ref.to_dict(),'visual_ref':visual_ref.to_dict(),'document_ref':docs_ref.to_dict(),
        'image_parent_refs':parents,'copied_images':len(copied),'missing_original_candidates':missing,
        'raw_documents':len(docs),'knowledge_gather':len(gathered),'visual_protocol_calls':len(calls),
        'benchmark_tasks':len(outputs['benchmark']['ready']),'training_stub_sequences':len(outputs['training']['ready']),
        'evaluation_scores':len(saved_stage(erun,'scores')),'resume_and_render':True,'isolated_lake':str(root)}
    destination=Path(os.environ.get('DEMIWTG_ACCEPTANCE_REPORT',str(tmp_path/'acceptance.json')));destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
