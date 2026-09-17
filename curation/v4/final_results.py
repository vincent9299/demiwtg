"""Business output projection and read-only notebook inspection of the final sink."""
from pathlib import Path
from .ops.knowledge_stages import material_id


class BuildKnowledgeRecord:
    """One concept material batch -> layered concept/documents/images/knowledge/audit."""
    def __call__(self,row):
        documents=[];images=[];identities=[]
        selected={material_id(dict(m)):m['image_id'] for m in row.get('material_pack',{}).get('images',[])}
        for material in row['cleaned_materials']:
            m=dict(material);kind=m['kind']
            if kind in {'legacy_concepts','qid_concepts','qid_concepts_base'}:
                identities.append({'record':m['record'],'source':m['provenance']});continue
            m['material_id']=material_id(m)
            if kind in {'legacy_images','qid_images'}:
                m['image_id']=selected.get(m['material_id']);images.append(m)
            else:documents.append(m)
        return {'schema':'concept-knowledge-record/2','case_id':row['case_id'],
            'concept':{'concept_ref':row['concept_ref'],'concept_id':row['bundle']['concept_id'],
                       'request':row['bundle']['request'],'source_records':identities,'identity':row.get('identity')},
            'documents':documents,'images':images,'image_selection':image_selection(images,row['export']),
            'knowledge':row['export'],
            'audit':{'input_scope':row.get('input_scope'),'blocked':row.get('blocked'),
                     'identity_unexamined':row.get('identity_unexamined'),
                     'material_selection':row.get('material_pack'),
                     'extraction_call':row.get('extraction_call'),
                     'consolidation_call':row.get('consolidation_call'),
                     'source_block_review': {k:row[k] for k in ('block_scope','block_decisions','block_calls',
                         'source_comparisons','block_comparison_coverage') if k in row},
                     'multimodal_review':{k:row[k] for k in ['joint_batch_scope','identity_boundary_provenance','image_material_scope','image_decisions','image_selection_calls','joint_calls','joint_verification_calls','joint_merge_review'] if k in row},
                     'fidelity_reviews':row.get('fidelity_reviews',[])}}


def image_selection(images,knowledge):
    """Keep source images separate from checked images and scoped positive support."""
    evidence=knowledge.get('image_evidence') or {}
    result=evidence.get('result',{}) if evidence.get('status')=='machine_reviewed' else {}
    known={m['image_id']:m['material_id'] for m in images if m.get('image_id')}
    facts={f['fact_id'] for f in knowledge.get('facts',[])}
    reviewed=[im['image_id'] for im in result.get('images',[]) if im['image_id'] in known]
    positive=[pair for pair in result.get('support',[]) if pair.get('status') in {'full','partial'}
              and pair.get('image_id') in reviewed and pair.get('fact_id') in facts]
    supporting=[{'image_id':image_id,'material_id':known[image_id],
                 'support':[pair for pair in positive if pair['image_id']==image_id],
                 'review_status':'machine_candidate'}
                for image_id in reviewed if any(p['image_id']==image_id for p in positive)]
    return {'original_material_ids':[m['material_id'] for m in images],
            'reviewed_image_ids':reviewed,'supporting_images':supporting,
            'selection_rule':'full/partial support for retained facts only; partial supports only its stated scope; not human approval'}


def knowledge_rows(knowledge, include_deferred=True):
    """One display row per fact; conflicts and image support remain relationships."""
    conflicts={}
    for issue in knowledge.get('unresolved_conflicts',[]):
        for fact_id in issue.get('affected_fact_ids',[]):conflicts.setdefault(fact_id,[]).append(issue['conflict_id'])
    def describe(reason):
        if isinstance(reason,dict):return '关联待核查分歧：'+str(reason.get('conflict_id',reason))
        if reason=='quote_not_in_supplied_source':return '引文无法在提供的原文中逐字匹配'
        if str(reason).startswith('number_not_in_quoted_evidence:'):return '引文未支持该数字：'+str(reason).split(':',1)[1]
        return reason
    rows=[]
    entries=[(fact,'保留，待审核',[],None) for fact in knowledge.get('facts',[])]
    if include_deferred:
        entries.extend((item['fact'],'暂缓',item.get('reasons',[]),item.get('next_action'))
                       for item in knowledge.get('deferred_facts',[]))
    for fact,status,reasons,next_action in entries:
        reviews=[{'reviewer':audit.get('reviewer'),**review}
                 for audit in knowledge.get('fidelity_reviews',[])
                 for review in audit.get('reviews',[]) if review['fact_id']==fact['fact_id']]
        rows.append({'fact_id':fact['fact_id'],'status':status,'statement':fact['statement'],
                     'citation_review':fact.get('citation_review'),'basis':fact.get('basis'),'image_evidence':fact.get('image_evidence',[]),'merged_from':fact.get('merged_from',[]),
                     'statement_mode':fact.get('statement_mode'),
                     'selection_review':fact.get('selection_review'),
                     'condition_storage':fact.get('condition_storage'),
                     'conditions':fact.get('conditions',[]),'exceptions':fact.get('exceptions',[]),
                     'source_evidence':fact.get('evidence',[]),'fidelity_reviews':reviews,
                     'defer_reasons':[describe(reason) for reason in reasons],
                     'related_issue_ids':conflicts.get(fact['fact_id'],[]),'next_action':next_action})
    return rows


def view_knowledge(path, *, limit=10, concept_ids=None, sample_rate=1., seed=42,
                   max_images=4, preview_width=200):
    """One table: concept batch / source documents / source images / facts / support."""
    import base64, html, mimetypes
    from demiflow.standalone import local_data
    from IPython.display import display, HTML
    from .notebook_debug import _value_html
    if not isinstance(limit,int) or limit<1:raise ValueError('limit must be positive')
    if max_images is not None and max_images<0:raise ValueError('max_images must be nonnegative or None')
    if not isinstance(preview_width,int) or not 32<=preview_width<=1000:raise ValueError('invalid preview_width')
    if not 0<=sample_rate<=1:raise ValueError('sample_rate must be in [0,1]')
    path=Path(path)
    if not path.exists():
        display(HTML('尚无最终文件：'+html.escape(str(path))));return
    data=local_data().read_json(str(path))
    if concept_ids is not None:
        allowed=set(concept_ids);data=data.filter(lambda r:r['concept']['concept_ref'] in allowed)
    if sample_rate<1:data=data.random_sample(sample_rate,seed=seed)
    rows=data.take(limit)
    esc=lambda value:html.escape(str(value))
    def block(title,body,opened=False):
        return '<details'+(' open' if opened else '')+'><summary>'+esc(title)+'</summary>'+body+'</details>'
    def fields(value):return _value_html(value,chars=220)
    # Store each image once in CSS; repeated knowledge references reuse it.
    # This keeps multi-concept notebooks from embedding the same bytes hundreds of times.
    preview_cache={};image_styles={}
    def preview(material):
        import hashlib,io
        from PIL import Image
        byte_info=material.get('bytes') or {};p=Path(byte_info.get('path') or '')
        if byte_info.get('status')!='verified_bytes' or not p.is_file():return ''
        key=str(p.resolve())
        if key in preview_cache:return preview_cache[key]
        mime=mimetypes.guess_type(str(p))[0]
        if mime not in {'image/png','image/jpeg','image/webp','image/gif'}:return ''
        if p.stat().st_size>16*1024*1024:return '图片超过预览字节限制，路径见记录。'
        raw=p.read_bytes();asset='kbimg_'+hashlib.sha256(raw).hexdigest()
        encoded=base64.b64encode(raw).decode()
        image_styles[asset]='.'+asset+'{background-image:url("data:'+mime+';base64,'+encoded+'");background-size:contain;background-repeat:no-repeat;background-position:left center}'
        with Image.open(io.BytesIO(raw)) as im:w,h=im.size
        scale=min(preview_width/w,240/h,1)
        result=f'<div role="img" aria-label="原始关联图片" class="{asset}" style="width:{max(1,int(w*scale))}px;height:{max(1,int(h*scale))}px"></div>'
        preview_cache[key]=result
        return result
    body=[]
    for row in rows:
        concept=row['concept'];knowledge=row['knowledge'];selection=row.get('image_selection') or image_selection(row['images'],knowledge)
        identity=concept.get('identity') or {}
        concept_cell='<b>'+esc(concept['concept_ref'])+'</b>'+fields({
            '概念ID':concept['concept_id'],'材料批次':row['case_id'],'身份状态':identity.get('status'),
            '知识处理状态':knowledge.get('status'),'阻塞原因':knowledge.get('blocked')})
        concept_cell+=block('身份来源与判断',fields({'来源记录':concept['source_records'],'身份判断':identity}))
        passages=((row.get('audit') or {}).get('material_selection') or {}).get('passages',[])
        by_source={p['source_id']:p for p in passages}
        doc_cells=[f"共 {len(row['documents'])} 篇原始资料"]
        for m in row['documents']:
            record=m['record'];raw=m.get('document',{}).get('text',record.get('raw_text',''))
            doc_cells.append(block(str(record.get('title') or m['material_id']),fields({
                '材料ID':m['material_id'],'来源网址':record.get('url'),'来源定位':m['provenance'],
                '原文':raw,'清洗状态':m.get('cleaning',{}).get('status')})+
                block('清洗版本与逐块定位',fields(m.get('cleaning',{})))))
        reviewed=set(selection['reviewed_image_ids']);supporting={x['image_id'] for x in selection['supporting_images']}
        image_cells=[f"原始关联 {len(row['images'])} 张；已检查 {len(reviewed)} 张；入选支持 {len(supporting)} 张。"]
        shown=0
        ordered=sorted(row['images'],key=lambda m:(m.get('bytes') or {}).get('status')!='verified_bytes')
        for m in ordered:
            image_id=m.get('image_id');inline=''
            if max_images is None or shown<max_images:
                inline=preview(m)
                if inline:shown+=1
            caption=f"{m['material_id']} · "+('入选支持' if image_id in supporting else '已检查，无有效支持' if image_id in reviewed else '未做支持检查')
            image_cells.append(block(caption,inline+fields({'图片ID':image_id,'原始记录':m['record'],
                '字节状态':m.get('bytes'),'来源定位':m['provenance']}),opened=bool(inline)))
        image_cells.insert(1,f'内嵌预览 {shown} 张；全部图片记录均可展开。')
        facts=knowledge_rows(knowledge,include_deferred=True)
        fact_cells=[fields({'处理范围':knowledge.get('coverage_note')}),f"共 {len(facts)} 条：保留待审核 {len(knowledge.get('facts',[]))} 条，暂缓 {len(knowledge.get('deferred_facts',[]))} 条。"]
        issues={x['conflict_id']:x for x in knowledge.get('unresolved_conflicts',[])}
        evidence=knowledge.get('image_evidence') or {}
        support=evidence.get('result',{}).get('support',[])
        images_by_id={m.get('image_id'):m for m in row['images'] if m.get('image_id')}
        labels={'full':'支持','partial':'部分支持','none':'不支持','unobservable':'无法判断，不计入支持'}
        documents_by_id={m['material_id']:m for m in row['documents']}
        for fact in sorted(facts,key=lambda f:f['fact_id']):
            content='<p>'+esc(fact['statement'])+'</p>'
            # Necessary qualifications remain part of the visible knowledge text.
            for label,key in [('条件','conditions'),('例外','exceptions')]:
                if fact[key]:content+='<p>'+label+'：'+esc('；'.join(str(v) for v in fact[key]))+'</p>'
            fact_support=[p for p in support if p.get('fact_id')==fact['fact_id']]
            for pair in fact_support:
                if pair.get('status') not in {'full','partial'}:continue
                material=images_by_id.get(pair['image_id'])
                if material:content+='<div>'+preview(material)+'</div>'
            sources=[];seen=set()
            for e in fact['source_evidence']:
                source=by_source.get(e['source_id'],{})
                material=documents_by_id.get(source.get('material_id'),{})
                record=material.get('record',{})
                title=record.get('title') or source.get('title') or '原始材料'
                url=record.get('url') or source.get('url') or ''
                if not url:
                    local=record.get('path') or ''
                    if local and Path(local).is_absolute() and Path(local).is_file():url=local
                key=(title,url)
                if key in seen:continue
                seen.add(key)
                safe=url.startswith(('https://','http://','/')) and not url.startswith('//')
                sources.append('<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(title)+'</a>' if safe else esc(title))
            if not sources:
                for e in fact.get('image_evidence',[]):
                    material=images_by_id.get(e['image_id'],{})
                    record=material.get('record',{})
                    url=record.get('landing_url') or record.get('content_url') or record.get('url') or ''
                    title=record.get('title') or '原始图片'
                    sources.append('<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(title)+'</a>' if url.startswith(('http://','https://')) else esc(title))
            content+='<p>来源：'+('、'.join(sources) or '未提供来源链接')+'</p>'
            status=fact['status']
            if fact_support:
                states=list(dict.fromkeys(labels.get(p.get('status'),'未核验') for p in fact_support))
                status+='；图片'+ '、'.join(states)
            content+='<p>审核状态：'+esc(status)+'</p>'
            fact_cells.append('<div style="border-top:1px solid #ccc;padding:8px 0">'+content+'</div>')
        cells=[concept_cell,'<br>'.join(doc_cells),'<br>'.join(image_cells),''.join(fact_cells)]
        body.append('<tr>'+''.join('<td>'+cell+'</td>' for cell in cells)+'</tr>')
    headers=['概念','原始资料','原始图片','知识条目（含状态）']
    table='<table class="knowledge-overview"><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(body)+'</tbody></table>'
    display(HTML('<style>'+''.join(image_styles.values())+'</style><style>.knowledge-overview{border-collapse:collapse;width:100%;table-layout:fixed}.knowledge-overview td,.knowledge-overview th{border:1px solid #ccc;padding:10px;vertical-align:top;text-align:left;overflow-wrap:anywhere}.knowledge-overview th:nth-child(1){width:12%}.knowledge-overview th:nth-child(2){width:20%}.knowledge-overview th:nth-child(3){width:23%}.knowledge-overview th:nth-child(4){width:45%}.knowledge-overview summary{cursor:pointer}.knowledge-overview pre{font-size:12px}</style>'+
        '<p>最终文件：'+esc(path)+f' · 显示 {len(rows)} 个概念材料批次。知识保留/暂缓与图片支持状态分别记录；均不代表人工核验通过。</p>'+
        '<div style="overflow:auto;max-height:1100px;min-width:900px">'+table+'</div>'))
