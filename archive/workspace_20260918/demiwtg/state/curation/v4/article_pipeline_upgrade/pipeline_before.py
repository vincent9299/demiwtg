def run_pipeline(run, dataset, *, ids=None, sample_rate=1., seed=42,
                 max_records_per_source=None, group_size=32, through='gather',
                 model_config=None, project=ROOT, source_scope="all", reuse_preprocessing=None, reuse_materials=None):
    """Dataset编排直接在这里；业务算子只处理行，不决定上下游。"""
    _assert_current_kernel()
    if through not in ['gather','identity','organize','extract','consolidate','fidelity','evidence','export']:
        raise ValueError('Unknown stopping stage')
    run, dataset = Path(run), Path(dataset)
    if source_scope not in {"all", "collected"}: raise ValueError("invalid source_scope")
    config = {**DEFAULT, **IMAGE_FILTER_DEFAULTS, **(model_config or {})}
    # 全库未关联资料审计是可选旁路；开启时关闭入口下推以保证审计完整。
    global_audit = config.get('global_material_audit', False)
    if config.get('image_annotations_file'):
        config['image_annotations_sha256'] = digest(Path(config['image_annotations_file']).read_bytes())
    settings = dict(ids=ids, sample_rate=sample_rate, seed=seed,
                    max_records_per_source=max_records_per_source, group_size=group_size,
                    model_config=config, source_scope=source_scope)
    if reuse_preprocessing is not None:settings['reuse_preprocessing']=str(Path(reuse_preprocessing).resolve())
    if reuse_materials is not None:
        validate_material_reuse(reuse_materials, config)
        if through not in {'extract','consolidate','fidelity','evidence','export'} or config.get('text_mode')!='multimodal':
            raise ValueError('Material reuse begins at multimodal joint extraction')
        if reuse_preprocessing is not None:raise ValueError('Choose one reuse boundary')
        from curation.v4.notebook_io import frozen_material_inputs, import_frozen_materials
        settings['reuse_materials']=frozen_material_inputs(reuse_materials)
    # 只管并发锁与版本冻结，不隐藏任何业务调度；through可向后续跑。
    with run_lock(run):
        tables = run/'datasets'
        data = local_data()

        # 1. 文件快照仅供版本冻结和定位校验，不负责读取或调度。
        legacy_concepts_source = {'kind':'legacy_concepts', **snapshot(dataset/'meta/concepts.json')}
        qid_concepts_source = {'kind':'qid_concepts', **snapshot(dataset/'meta/qid_concepts.fat.jsonl.gz')}
        collected_documents_source = {'kind':'legacy_docs', **snapshot(dataset/'meta/docs.jsonl')}
        collected_images_source = {'kind':'legacy_images', **snapshot(dataset/'meta/images.jsonl')}
        wiki_pages_source = {'kind':'wiki_pages', **snapshot(dataset/'corpus/pages-en-part1.jsonl.gz')}
        active_sources=[legacy_concepts_source,collected_documents_source,collected_images_source]
        if source_scope=='all':active_sources += [qid_concepts_source,wiki_pages_source]
        version = freeze_run(run, dataset, active_sources, settings, run_pipeline, project)
        if reuse_preprocessing is not None:
            from curation.v4.notebook_io import reuse_preprocessing as import_preprocessing
            import_preprocessing(reuse_preprocessing,run,version,settings)

        if reuse_materials is not None:
            import_frozen_materials(settings['reuse_materials'],run,version)
        else:
            # 原生读取 → 保存原始解码行（含错误）→ 有效对象 → 业务字段转换。
            # read_records负责gzip/JSON解析、行号与扫描报告；map不读文件。
            # 解码失败或非对象行保留在read_*检查点，不静默丢弃原文。
            legacy_concepts_records = (data.read_records(dataset/'meta/concepts.json', format='json', item_prefix='concepts.item',
                max_records=max_records_per_source, missing='empty', report_path=run/'source_status/legacy_concepts.json')
                .filter(SelectSourceRecords('legacy_concepts', ids, sample_rate, seed, enabled=not global_audit))
                .checkpoint(tables/'read_legacy_concepts.jsonl', version=version))
            legacy_concepts = (legacy_concepts_records
                .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                .map(ConceptFromRecord(legacy_concepts_source))
                .checkpoint(tables/'input_legacy_concepts.jsonl', version=version))

            if source_scope=='all':
                qid_concepts_records = (data.read_records(dataset/'meta/qid_concepts.fat.jsonl.gz',
                    max_records=max_records_per_source, missing='empty', report_path=run/'source_status/qid_concepts.json')
                    .filter(SelectSourceRecords('qid_concepts', ids, sample_rate, seed, enabled=not global_audit))
                .checkpoint(tables/'read_qid_concepts.jsonl', version=version))
                qid_concepts = (qid_concepts_records
                    .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                    .map(ConceptFromRecord(qid_concepts_source))
                    .checkpoint(tables/'input_qid_concepts.jsonl', version=version))

            else:
                qid_concepts = data.from_iter(lambda: iter(()))

            collected_documents_records = (data.read_records(dataset/'meta/docs.jsonl',
                max_records=max_records_per_source, missing='empty', report_path=run/'source_status/collected_documents.json')
                .filter(SelectSourceRecords('legacy_docs', ids, sample_rate, seed, enabled=not global_audit))
                .checkpoint(tables/'read_collected_documents.jsonl', version=version))
            collected_documents = (collected_documents_records
                .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                .map(DocumentFromRecord(collected_documents_source))
                .checkpoint(tables/'input_collected_documents.jsonl', version=version))

            collected_images_records = (data.read_records(dataset/'meta/images.jsonl',
                max_records=max_records_per_source, missing='empty', report_path=run/'source_status/collected_images.json')
                .filter(SelectSourceRecords('legacy_images', ids, sample_rate, seed, enabled=not global_audit))
                .checkpoint(tables/'read_collected_images.jsonl', version=version))
            collected_images = (collected_images_records
                .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                .map(ImageFromRecord(collected_images_source))
                .checkpoint(tables/'input_collected_images.jsonl', version=version))

            if source_scope=='all':
                wiki_pages_records = (data.read_records(dataset/'corpus/pages-en-part1.jsonl.gz',
                    max_records=max_records_per_source, missing='empty', report_path=run/'source_status/wiki_pages.json')
                    .checkpoint(tables/'read_wiki_pages.jsonl', version=version))
                wiki_pages = (wiki_pages_records
                    .filter(lambda r: r['error'] is None and isinstance(r['value'], dict))
                    .map(DocumentFromRecord(wiki_pages_source))
                    .checkpoint(tables/'input_wiki_pages.jsonl', version=version))

            else:
                wiki_pages = data.from_iter(lambda: iter(()))

            concepts = (legacy_concepts.union(qid_concepts)
                .reduce_by_key('concept_ref', merge_concept)
                .checkpoint(tables/'concepts.jsonl', version=version))
            images = collected_images.checkpoint(tables/'images.jsonl', version=version)

            # 2. 页面关联：概念的lang/page_id → Wiki文档concept_refs。
            # left join保留未匹配页面；多概念对应不自动消歧。其他文档保留采集关联。
            page_refs = (concepts.flat_map(lambda c:c['page_refs'])
                .reduce_by_key(['lang','page_id','mapped_concept_ref'], distinct)
                .checkpoint(tables/'page_refs.jsonl', version=version))
            wiki_documents = (wiki_pages
                .join(page_refs, on=['lang','page_id'], how='left')
                .reduce_by_key('doc_id', merge_document))
            documents = collected_documents.union(wiki_documents)
            documents = documents.checkpoint(tables/'documents.jsonl', version=version)

            # 3. SelectConcept：概念行 → 增加selected/selection_reason，再filter。
            # 与读取端使用同一采样规则；下游业务算子不接收入口概念ID。
            concept_selection = (concepts.map(SelectConcept(ids, sample_rate, seed))
                .checkpoint(tables/'concepts_selected.jsonl', version=version))
            selected_concepts = concept_selection.filter(lambda c:c['selected'])
            selected_keys = selected_concepts.select_columns(['concept_ref'])
            if ids is not None:
                (data.from_iter(lambda:({'concept_ref':ref} for ref in ids))
                    .join(concepts.select_columns(['concept_ref']), on='concept_ref', how='anti')
                    .checkpoint(tables/'missing_concepts.jsonl', version=version))

            # 4. MaterialLinks：文档/图片行 → concept_ref与doc_id/image_id关联键。
            # 分别semi join入选概念，再用资料ID筛选原表；共享资料只处理一次。
            all_document_links = documents.flat_map(MaterialLinks('doc_id'))
            all_image_links = images.flat_map(MaterialLinks('image_id'))
            document_links = (all_document_links.join(selected_keys, on='concept_ref', how='semi')
                .checkpoint(tables/'documents_links.jsonl', version=version))
            image_links = (all_image_links.join(selected_keys, on='concept_ref', how='semi')
                .checkpoint(tables/'images_links.jsonl', version=version))
            selected_documents = (documents.join(document_links.select_columns(['doc_id'])
                .reduce_by_key('doc_id', distinct), on='doc_id', how='semi')
                .checkpoint(tables/'documents_selected.jsonl', version=version))
            selected_images = (images.join(image_links.select_columns(['image_id'])
                .reduce_by_key('image_id', distinct), on='image_id', how='semi')
                .checkpoint(tables/'images_selected.jsonl', version=version))
            # 未关联任何已读概念的资料另存；不是把未入选概念的资料判为无关。
            if global_audit:
                for objects, links, key, name in [
                    (documents, all_document_links, 'doc_id', 'documents'),
                    (images, all_image_links, 'image_id', 'images')]:
                    associated = (links.join(concepts.select_columns(['concept_ref']), on='concept_ref', how='semi')
                        .select_columns([key]).reduce_by_key(key, distinct))
                    objects.join(associated, on=key, how='anti').checkpoint(tables/f'{name}_unmatched.jsonl', version=version)


            # 5. ReadDocument → CleanDocument → FilterDocumentBlocks：读原文、解析正文、过滤与修复。
            # 原文及排除依据保留；重建clean_text和块定位后才交给身份/相关性判断。
            # 输入：选中文档path/sections；输出：raw_text → clean_text/块定位/准入状态。
            # 不删除原文，pending带原因保留；map_cached复用同输入同版本结果。
            processed_documents = (selected_documents
                .map_cached(ReadDocument(dataset), cache_dir=run/'cache/read_documents', version=version)
                .map_cached(CleanDocument(), cache_dir=run/'cache/clean_documents', version=version)
                .map_cached(FilterDocumentBlocks(), cache_dir=run/'cache/filter_documents', version=version)
                .checkpoint(tables/'documents_processed.jsonl', version=version))
            # 6. CheckImage：图片独立扩列；路径/哈希 → byte_status/byte_details。
            # 文件可用性检查不是图片语义核验。
            processed_images = (selected_images
                .map_cached(CheckImage(dataset), cache_dir=run/'cache/check_images', version=version)
                .checkpoint(tables/'images_processed.jsonl', version=version))

            # 可选：读取预标注JSONL冻结快照，按图片SHA关联；不把机器标签当人工核验。
            if config.get('image_annotations_file'):
                annotations = (data.read_records(config['image_annotations_file'])
                    .map(lambda r: {'sha256':r['value']['sha256'],'preannotation':r['value']} if r['error'] is None else (_ for _ in ()).throw(ValueError('invalid annotation row')))
                    .checkpoint(tables/'image_annotations.jsonl',version=version))
                processed_images = (processed_images.join(annotations,on='sha256',how='left')
                    .checkpoint(tables/'images_annotated.jsonl',version=version))

            # 7. CountMaterial：关联键join处理状态后，按概念归约计数。
            # 输出：每概念文档/图片总数、可读/字节通过数；零资料概念left join保留。
            document_counts = (document_links
                .join(processed_documents.select_columns(['doc_id','read_status']), on='doc_id')
                .reduce_by_key('concept_ref', CountMaterial('document_count','read_status','readable_documents')))
            image_counts = (image_links
                .join(processed_images.select_columns(['image_id','byte_status']), on='image_id')
                .reduce_by_key('concept_ref', CountMaterial('image_count','byte_status','verified_images')))
            concepts_ready = (selected_concepts
                .join(document_counts, on='concept_ref', how='left')
                .join(image_counts, on='concept_ref', how='left')
                .map(fill_material_counts)
                .checkpoint(tables/'concepts_ready.jsonl', version=version))

            # 8. NestMaterial + group_batches：到这里才按概念汇集完整文档/图片。
            # 输出knowledge_inputs：每行一个概念材料批次，含materials及批次索引。
            # 这只是执行分批，尚未完成跨批语义整合。
            concept_documents = document_links.join(
                processed_documents.map(NestMaterial('doc_id','documents')), on='doc_id')
            concept_images = image_links.join(
                processed_images.map(NestMaterial('image_id','images')), on='image_id')
            material_batches = concept_documents.union(concept_images).group_batches(
                'concept_ref', max_rows=group_size, output='materials')
            batches = (concepts_ready.join(material_batches, on='concept_ref', how='left')
                .checkpoint(tables/'knowledge_inputs.jsonl', version=version))
            if through == 'gather': return batches

        # 9. ResolveIdentity：概念与清洗资料 → 身份/逐材料依据/接受拒绝/未查看范围。
        # model_input仅转换联合输入格式；身份歧义blocked，图片此时仅看元数据。
        knowledge_run = run/'knowledge'
        pack, prompt_text = knowledge_prompt_pack(config)
        options = prompt_execution_options(run, config)
        save_prompt_config(run, prompt_text, options)
        prompt_data = local_data(prompt_packs={'knowledge.yaml':pack},
                                 max_prompt_requests=config['max_calls'], prompt_options=options)
        # 同一材料Dataset进入原生提示词执行上下文；请求预算和持久账本跨阶段共享。
        if reuse_materials is None:
            batches = prompt_data.read_json(str(tables/'knowledge_inputs.jsonl'))
            identified = (batches.map(model_input)
                # 准备：保留源资料，生成identity_prompt；不调用模型。
                .map_cached(PrepareIdentity(knowledge_run, config),
                            cache_dir=knowledge_run/'cache/PrepareIdentity', version=version)
                # 调用：demiflow负责异步HTTP、完整请求响应、预算和精确回放。
                .map_prompt_async('identity', config='knowledge.yaml', inputs={'payload':'identity_prompt'},
                                  output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                                  when=lambda r: not r.get('blocked') and 'identity_prompt' in r,
                                  concurrency=1, queue_depth=1)
                # 校验：结果与原文/材料对应检查；错误保留为blocked，不丢行。
                .map_cached(ApplyIdentity(knowledge_run, config),
                            cache_dir=knowledge_run/'cache/ApplyIdentity', version=version)
                .checkpoint(tables/'knowledge_identity.jsonl', version=version))
            if through == 'identity': return identified

        # 10M. 相关材料筛选 → 图文联合提炼 → 引用/像素核验 → 跨批候选去重与冲突处理。
        # 以下分批只控制单次输入；文字组×图片组完整遍历，不按已有知识找图片。
        if config.get('text_mode') == 'multimodal':
            if config.get('integration_rounds',1) != 1: raise ValueError('Slim pipeline performs one conditional integration pass; further repair needs a separate reviewed run')
            if reuse_materials is not None:
                related=prompt_data.read_json(str(tables/'related_materials.jsonl'))
                routing_materials=prompt_data.read_json(str(tables/'routing_materials.jsonl'))
                text_embeddings=(prompt_data.read_json(str(tables/'material_text_embeddings.jsonl'))
                    .flat_map(lambda r:r['items']).reduce_by_key('case_id',
                    lambda acc,r:{'case_id':r['case_id'],'passage_embeddings':{**acc['passage_embeddings'],r['source_id']:r}},initial={'passage_embeddings':{}}))
                image_text_embeddings=prompt_data.read_json(str(tables/'material_image_embeddings.jsonl'))
            else:
                blocks = (identified.map(BuildSourceBlocks(config.get('block_unit_chars',1800), body_only=True))
                    .map(SelectAvailableImages()).checkpoint(tables/'multimodal_materials.jsonl', version=version))
                text_requests = blocks.flat_map(BatchSourceBlocks(config.get('block_batch_chars',8000)))
                text_decisions = (text_requests.map_prompt_async('select_blocks', config='knowledge.yaml',
                    inputs={'payload':'block_prompt'}, output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                    .map_cached(ApplyBlockSelection(relevance_only=True),cache_dir=knowledge_run/'cache/text_relevance',version=version)
                    .checkpoint(tables/'text_relevance.jsonl',version=version)
                    .reduce_by_key('case_id',merge_block_decisions))
                # 10A. Qwen初筛：只给概念身份资料和像素，不给上游图片结论。
                image_requests = blocks.flat_map(BatchImageSelection(config.get('image_batch_size',4),
                    config['image_identity_definitions'], neutral=True)).checkpoint(tables/'image_requests.jsonl',version=version)
                primary_data = image_prompt_data(run, config)
                primary = (primary_data.read_json(str(tables/'image_requests.jsonl'))
                    .map_prompt_async('select_images',config='knowledge.yaml',
                        inputs={'payload':'image_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                    .map_cached(RecordPrimaryImageSelection(),cache_dir=run/'cache/image_primary',version=version)
                    .checkpoint(tables/'image_primary.jsonl',version=version))
                # 10B. 含入选图才复核；保留整个原批次，Gemma看不到Qwen判断。
                review_rows = primary.map(PrepareImageReview()).checkpoint(tables/'image_review_inputs.jsonl',version=version)
                review_data = image_prompt_data(run, config, review=True)
                review_requests = review_data.read_json(str(tables/'image_review_inputs.jsonl')).filter(lambda r:r['review_required'])
                review_path = tables/'image_review_responses.jsonl'
                # 只在有未完成复核时借用GPU；该阶段落盘后恢复Qwen及预标注。
                with image_review_service(run, config, needed=review_needed(review_requests,review_path,version)):
                    reviewed = (review_requests.map_prompt_async('select_images',config='knowledge.yaml',
                        inputs={'payload':'image_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',
                        concurrency=config['image_review_concurrency'],queue_depth=config['image_review_concurrency'])
                        .checkpoint(review_path,version=version))
                # 10C. 原排除/待定继续保留；入选图须独立复核keep，否则暂缓。
                image_decisions = (reviewed.union(review_rows.filter(lambda r:not r['review_required']))
                    .map_cached(ApplyConfirmedImageSelection(),cache_dir=run/'cache/image_confirmed',version=version)
                    .checkpoint(tables/'image_relevance.jsonl',version=version)
                    .reduce_by_key('case_id',merge_image_decisions))
                related = (blocks.join(text_decisions,on='case_id',how='left')
                    .join(image_decisions,on='case_id',how='left').map(SelectRelatedMaterials())
                    .checkpoint(tables/'related_materials.jsonl',version=version))
                save_image_filter_policy(run,config)
                if through=='organize':return related
                # 11. 原生图文关联 → 文本embedding分组 → 图文embedding补充关联。
                routing_materials = related.map(PrepareRoutingMaterials()).checkpoint(tables/'routing_materials.jsonl',version=version)
                text_embeddings = (routing_materials.flat_map(RawPassageRows())
                    .group_batches('embedding_bucket',max_rows=2,output='items')
                    .map_cached(EmbedParagraphBatch(config['text_embedding_model']),cache_dir=run/'cache/material_text_embedding',version=version)
                    .checkpoint(tables/'material_text_embeddings.jsonl',version=version)
                    .flat_map(lambda r:r['items']).reduce_by_key('case_id',
                        lambda acc,r:{'case_id':r['case_id'],'passage_embeddings':{**acc['passage_embeddings'],r['source_id']:r}},initial={'passage_embeddings':{}}))
                image_text_embeddings = (routing_materials
                    .map_cached(EncodeImageTextMaterials(config['image_embedding_model']),cache_dir=run/'cache/material_image_embedding',version=version)
                    .checkpoint(tables/'material_image_embeddings.jsonl',version=version))
            if reuse_materials is not None:save_image_filter_policy(run,config)
            routed = (routing_materials.join(text_embeddings,on='case_id',how='left')
                .join(image_text_embeddings.select_columns(['case_id','text_windows','image_vectors']),on='case_id',how='left')
                .map(RouteByTokenBudget(ROOT.parent/'models/Qwen3.8-27B', config.get('joint_input_tokens',32768)))
                .checkpoint(tables/'material_routing.jsonl',version=version))
            # 12. 容量组装，不做文字×图片全组合；图片数量是单次输入目标，非最终保留上限。
            joint_requests = (routed.flat_map(lambda r:r['requests']).map(BuildRoutedJointRequest())
                .checkpoint(tables/'joint_requests.jsonl',version=version))
            # 13. 真实像素联合提炼：模型负责选图、重复取舍、条件和引文。
            extracted = (joint_requests.map_prompt_async('joint_paragraphs',config='knowledge.yaml',
                inputs={'payload':'joint_prompt','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyParagraphs(),cache_dir=run/'cache/paragraph_extract',version=version)
                .checkpoint(tables/'paragraph_extract.jsonl',version=version))
            if through=='extract':return extracted
            verified = (extracted.map_prompt_async('verify_paragraphs',config='knowledge.yaml',
                inputs={'payload':'verify_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyParagraphReview(),cache_dir=run/'cache/paragraph_verify',version=version)
                .checkpoint(tables/'paragraph_verify.jsonl',version=version))
            # 14. 把已核验段落与实际请求对齐，独立内容继续保留。
            originals = (verified.join(joint_requests.select_columns(['batch_id','pixel_images']),on='batch_id',how='left')
                .map_cached(PrepareVerifiedParagraphs(run), cache_dir=run / "cache/local_verified_rows", version=version).checkpoint(tables/'paragraph_originals.jsonl',version=version))
            # 14A. 仅修复删文后失效/缺正文/核验未通过的主题；内部仍是原生demiflow链。
            originals, initial_repairs = repair_topics(originals,run=run,version=version,stage='initial')
            all_joint_requests = joint_requests.union(initial_repairs)
            # 15—16. 同批/跨批候选批量判断，只做一次按需整合；仅改写内容再核验。
            round_tables = tables / 'integration_1'
            original_groups = originals.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'source_rows':acc['source_rows']+[r]},initial={'source_rows':[]})
            public_rows = originals.map(SelectRetainedParagraphs()).map(lambda r:r['content'])
            paragraph_embeddings = (public_rows.flat_map(ParagraphRows()).group_batches('embedding_bucket',max_rows=2,output='items')
                .map_cached(EmbedParagraphBatch(config['text_embedding_model']),cache_dir=run/'cache/output_text_embedding',version=version)
                .checkpoint(round_tables/'paragraph_embeddings.jsonl',version=version).flat_map(lambda r:r['items']))
            paragraph_groups = paragraph_embeddings.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'items':acc['items']+[r]},initial={'items':[]})
            plans = paragraph_groups.map(PlanCrossBatchReview(include_same_batch=True, skip_single_batch=True)).checkpoint(round_tables/'cross_batch_plan.jsonl',version=version)
            # 15. 仅对同批/跨批的候选对判断关系，再决定是否局部整合。
            cross_reviews = (plans.flat_map(BatchRelationshipReviews(config.get('relationship_batch_pairs',8), config.get('relationship_batch_chars',24000)))
                .map_prompt_async('review_relationships',config='knowledge.yaml',inputs={'payload':'review_payload'},
                    when=lambda r:not r.get('batch_error'), output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyRelationshipReviews(),cache_dir=run/'cache/cross_review',version=version)
                .checkpoint(round_tables/'cross_batch_review_batches.jsonl',version=version)
                .flat_map(lambda r:r['pair_reviews'])
                .checkpoint(round_tables/'cross_batch_reviews.jsonl',version=version)
                .reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'review_rows':acc['review_rows']+[r]},initial={'review_rows':[]}))
            # 16. 协调交叠候选，再建互不覆盖的局部整合任务；原文/像素随任务携带。
            local_groups = (paragraph_groups.join(cross_reviews,on='concept',how='left')
                .join(original_groups,on='concept',how='left').map(BuildLocalMergeGroups())
                .checkpoint(round_tables/'local_merge_plan.jsonl',version=version))
            local_requests = local_groups.flat_map(lambda r:r['requests']).checkpoint(round_tables/'local_merge_requests.jsonl',version=version)
            local_extracted = (local_requests.map_prompt_async('merge_paragraphs',config='knowledge.yaml',
                inputs={'payload':'merge_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyParagraphMerge(),cache_dir=run/'cache/local_merge',version=version)
                .checkpoint(round_tables/'local_merge_extract.jsonl',version=version))
            # 改写后的文字、标题与图片关联再次对照原文和真实像素核验。
            local_verified = (local_extracted.map_prompt_async('verify_merged_paragraphs',config='knowledge.yaml',
                inputs={'payload':'verify_payload','images':'pixel_images'},output='prompt_result',call_output='prompt_call',error_output='prompt_error',concurrency=1,queue_depth=1)
                .map_cached(ApplyParagraphReview(),cache_dir=run/'cache/local_merge_verify',version=version)
                .map_cached(PrepareVerifiedParagraphs(run), cache_dir=run / "cache/local_verified_rows", version=version)
                .checkpoint(round_tables/'local_merge_verify.jsonl',version=version)
                .join(local_requests.select_columns(['batch_id','pixel_images']),on='batch_id',how='left'))
            local_results = local_verified.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'local_results':acc['local_results']+[r]},initial={'local_results':[]})
            assembled = (original_groups.join(local_results,on='concept',how='left').map(ApplyLocalIntegration())
                .checkpoint(round_tables/'paragraph_assembly.jsonl',version=version))
            originals, repaired = repair_topics(assembled.flat_map(lambda r:r['rows']),run=run,version=version,stage='integration_1')
            all_joint_requests = all_joint_requests.union(local_requests).union(repaired)
            # 不再调用模型只为记录残留关系；一次整合不保证完全去重，后续按实际质量问题定向复查。
            final_rows = originals.checkpoint(run/'paragraphs.jsonl',version=version)
            all_joint_requests.checkpoint(run/'requests.jsonl',version=version)
            if through in {'consolidate','fidelity','evidence'}:return final_rows
            # 17. 发布只组织三部分，不再用相似度或张数筛图。
            catalogs = related.map(SourceCatalog()).checkpoint(tables/'source_catalog.jsonl',version=version)
            articles = (final_rows.map(SelectRetainedParagraphs()).map(lambda r:r['content']).flat_map(TopicRows())
                .join(catalogs,on='concept',how='left').map(FormatTopicArticle())
                .checkpoint(tables/'articles.jsonl',version=version))
            concept_articles = articles.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'articles':acc['articles']+[r['article']]},initial={'articles':[]})
            # 18. 知识sink保留概念、原始资料、图片、最终主题内容及过程审计。
            audit = local_groups.map(lambda r:{'concept':r['concept'],'local_audit':{'pending':r['pending'],'local_task_count':len(r['requests'])}})
            plan_audit = plans.map(lambda r:{'concept':r['concept'],'cross_batch_plan':{'pending':r['pending'],'candidate_count':len(r['review_requests']),'passthrough_paragraph_ids':r['passthrough_paragraph_ids']}})
            return (related.map(lambda r:{**r,'concept':r['identity']['target_label']})
                .join(concept_articles,on='concept',how='left').join(audit,on='concept',how='left').join(plan_audit,on='concept',how='left')
                .map(FinalKnowledgeRecord()).checkpoint(run/'knowledge_base.jsonl',version=version))

        # 10A. 原文优先试验：替代生成式提取/忠实性改写审核，仍使用同一Dataset。
        # 1800/8000/16000是单块/请求的软目标，超长完整块单独处理，不截断文档总量。
        # 本分支当前验收文字；原始图片保留，新的知识尚未做像素支持核验。
        if config.get('text_mode') == 'source_blocks':
            # 身份接受文档 → 全部保留块、章节、相邻上下文、原文定位和未处理范围。
            blocks = (identified.map(BuildSourceBlocks(config.get('block_unit_chars',1800), body_only=config.get('body_only',False)))
                .checkpoint(tables/'source_blocks.jsonl', version=version))
            requests = (blocks.flat_map(BatchSourceBlocks(config.get('block_batch_chars',8000)))
                .checkpoint(tables/'block_requests.jsonl', version=version))
            # 模型只选择/暂缓/排除ID，不负责重写原文或生成引文。
            selected_batches = (requests
                .map_prompt_async('select_blocks', config='knowledge.yaml', inputs={'payload':'block_prompt'},
                    output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                    concurrency=1, queue_depth=1)
                .map_cached(ApplyBlockSelection(relevance_only=config.get('relevance_only', True)), cache_dir=knowledge_run/'cache/ApplyBlockSelection', version=version)
                .checkpoint(tables/'block_decisions.jsonl', version=version))
            decisions = selected_batches.reduce_by_key('case_id', merge_block_decisions)
            candidates = (blocks.join(decisions, on='case_id', how='left')
                # statement与quote直接从原文块复制，条件保留在原文内，空conditions不表示无条件。
                .map(BuildVerbatimCandidates())
                .checkpoint(tables/'verbatim_candidates.jsonl', version=version))
            if through in {'organize','extract'}: return candidates
            # 分组之间两两比较：覆盖本材料批的全部候选对，不靠主题标签漏掉差异。
            # 比较仍是模型判断；分组数增加会带来二次方请求量，尚非全量吞吐验收。
            comparisons = (candidates.flat_map(BatchSourceComparisons(config.get('comparison_group_chars',16000)))
                .checkpoint(tables/'comparison_requests.jsonl', version=version))
            compared = (comparisons
                .map_prompt_async('compare_blocks', config='knowledge.yaml', inputs={'payload':'compare_prompt'},
                    output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                    concurrency=1, queue_depth=1)
                .map_cached(ApplySourceComparison(), cache_dir=knowledge_run/'cache/ApplySourceComparison', version=version)
                .checkpoint(tables/'source_comparisons.jsonl', version=version))
            comparison_results = compared.reduce_by_key('case_id', merge_source_comparisons)
            reviewed = (candidates.join(comparison_results, on='case_id', how='left')
                .map(ApplyComparedCandidates())
                .checkpoint(tables/'knowledge_block_review.jsonl', version=version))
            if through in {'consolidate','fidelity','evidence'}: return reviewed
            exported = (reviewed.map_cached(BuildCandidateRecords(knowledge_run,config),
                cache_dir=knowledge_run/'cache/BuildCandidateRecords', version=version)
                .checkpoint(tables/'knowledge_export.jsonl', version=version))
            return exported.map(BuildKnowledgeRecord()).checkpoint(run/'knowledge_base.jsonl', version=version)

        # 10. SelectPassagesAndImages：接受资料 → 去重、选完整章节块及可用图。
        # 输出material_pack：passages/images/duplicates/omissions/image_gaps。
        selected_materials = (identified
            .map_cached(SelectPassagesAndImages(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/SelectPassagesAndImages', version=version)
            .checkpoint(tables/'knowledge_organize.jsonl', version=version))
        if through == 'organize': return selected_materials

        # 11. ExtractKnowledge：本批多份passages → extraction。
        # facts每项含statement/conditions/exceptions/evidence；缺证与争议暂缓。
        extracted = (selected_materials
            # 准备：保留源资料，生成extract_prompt；不调用模型。
            .map_cached(PrepareExtraction(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/PrepareExtraction', version=version)
            # 调用：demiflow负责异步HTTP、完整请求响应、预算和精确回放。
            .map_prompt_async('extract', config='knowledge.yaml', inputs={'payload':'extract_prompt'},
                              output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                              when=lambda r: not r.get('blocked') and 'extract_prompt' in r,
                              concurrency=1, queue_depth=1)
            # 校验：结果与原文/材料对应检查；错误保留为blocked，不丢行。
            .map_cached(ApplyExtraction(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/ApplyExtraction', version=version)
            .checkpoint(tables/'knowledge_extract.jsonl', version=version))
        if through == 'extract': return extracted

        # 12. ConsolidateKnowledge：extraction + 原片段 → knowledge。
        # 比较重复/互补/条件/矛盾，保留changes，未解冲突不混入保留候选。
        reviewed = (extracted
            # 准备：保留源资料，生成consolidate_prompt；不调用模型。
            .map_cached(PrepareConsolidation(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/PrepareConsolidation', version=version)
            # 调用：demiflow负责异步HTTP、完整请求响应、预算和精确回放。
            .map_prompt_async('consolidate', config='knowledge.yaml', inputs={'payload':'consolidate_prompt'},
                              output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                              when=lambda r: not r.get('blocked') and 'consolidate_prompt' in r,
                              concurrency=1, queue_depth=1)
            # 校验：结果与原文/材料对应检查；错误保留为blocked，不丢行。
            .map_cached(ApplyConsolidation(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/ApplyConsolidation', version=version)
            .checkpoint(tables/'knowledge_consolidate.jsonl', version=version))
        if through == 'consolidate': return reviewed

        # 13. 审核陈述是否忠实于原文：knowledge（含暂缓项）+ 完整入选片段 → fidelity_reviews。
        # 不改写陈述；不支持/不确定的保留项转暂缓；此前暂缓项不会因这一步通过而恢复。
        faithful = (reviewed
            .map_cached(PrepareFidelity(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/PrepareFidelity', version=version)
            .map_prompt_async('fidelity', config='knowledge.yaml', inputs={'payload':'fidelity_prompt'},
                              output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                              when=lambda r: not r.get('blocked') and 'fidelity_prompt' in r,
                              concurrency=1, queue_depth=1)
            .map_cached(ApplyFidelity(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/ApplyFidelity', version=version)
            .checkpoint(tables/'knowledge_fidelity.jsonl', version=version))
        if through == 'fidelity': return faithful

        # 14. CheckImageSupport：knowledge.facts + 实际图片 → image_evidence。
        # 逐图×知识记录区域、支持范围和局限，缺图/none与未执行分别记录。
        supported = (faithful
            # 准备：保留源资料，生成evidence_prompt；不调用模型。
            .map_cached(PrepareImageSupport(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/PrepareImageSupport', version=version)
            # 调用：demiflow负责异步HTTP、完整请求响应、预算和精确回放。
            .map_prompt_async('evidence', config='knowledge.yaml', inputs={'payload':'evidence_prompt', 'images':'evidence_images'},
                              output='prompt_result', call_output='prompt_call', error_output='prompt_error',
                              when=lambda r: not r.get('blocked') and 'evidence_prompt' in r,
                              concurrency=1, queue_depth=1)
            # 校验：结果与原文/材料对应检查；错误保留为blocked，不丢行。
            .map_cached(ApplyImageSupport(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/ApplyImageSupport', version=version)
            .checkpoint(tables/'knowledge_evidence.jsonl', version=version))
        if through == 'evidence': return supported

        # 15. BuildCandidateRecords：累计结果 → export（机器候选、暂缓、补证任务）。
        # 不升级为人工核验知识；文档/图片/原始请求响应仍可追溯。
        result = (supported
            .map_cached(BuildCandidateRecords(knowledge_run, config),
                        cache_dir=knowledge_run/'cache/BuildCandidateRecords', version=version)
            .checkpoint(tables/'knowledge_export.jsonl', version=version))
        # 16. 最终文件sink：每行一个概念材料批，原始资料与知识分层保留。
        # checkpoint是demiflow原生文件终结操作：原子写JSONL、版本检查、断点复用。
        knowledge_base = (result.map(BuildKnowledgeRecord())
            .checkpoint(run/'knowledge_base.jsonl', version=version))
        return knowledge_base


# 唯一执行入口；run/dataset/sources是显式参数，没有f/StreamFlow。
if MODE == 'execute':
    final_dataset = await asyncio.to_thread(
        run_pipeline, RUN, DATASET, ids=IDS, sample_rate=SAMPLE_RATE,
        seed=SEED, max_records_per_source=MAX_RECORDS, group_size=GROUP_SIZE,
        through=THROUGH, model_config=MODEL_CONFIG, source_scope=SOURCE_SCOPE, reuse_materials=REUSE_MATERIALS)
else:
    final_dataset = None  # 只读模式由下方最终结果查看器读取FINAL_FILE
print(f'模式：{MODE}；主链终点：{THROUGH}；结果目录：{RUN}')
if MODE == 'execute' and THROUGH == 'export': FINAL_FILE = RUN/'knowledge_base.jsonl'
