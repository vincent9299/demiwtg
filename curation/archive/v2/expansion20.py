"""Freeze and render an isolated 10-edit/10-t2i development batch."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
import json
from pathlib import Path
from collections import Counter
from pipeline import ROOT, STATE, read
from bagel_runner import publish, encoded, sha, validate_jobs
from review import esc, picture
from case_input_details import actual_input_details
from classification_notebook import CSS, LEVELS, guide, md_table

RUN=STATE/'expansion20_v1'

# Complete presentation translations; frozen requests remain unchanged.
KNOWLEDGE_ZH_FULL = {'石蒜': '红花石蒜（Lycoris radiata）在夏末和初秋于无叶花葶上开花。它的叶片狭长、两侧边缘近于平行，呈灰绿色，中央有一条较浅的条纹；叶片在开花后长出，整个冬季保持，到春末枯退。', '鳞毛蕨': '鳞毛蕨属（Dryopteris）的孢子囊群及其囊群盖位于蕨叶下表面。在上表面，叶轴和羽轴具有沟槽。将蕨叶翻面会改变可见的表面及其结构；孢子囊群不是印在叶片两面的装饰斑点。', '高锰酸钾': '高锰酸钾是一种紫黑色结晶盐，溶于水后形成粉红色至紫色的溶液。完全混合的未饱和溶液中，已溶解的溶质分布于整个溶液；未溶解的晶体和局部浓色条流不属于这一最终状态。', '竖琴': '在常见的现代西方竖琴上，C弦为红色，F弦为蓝色或黑色，其余弦通常不染色。在C大调中，相邻琴弦按C、D、E、F、G、A、B的顺序循环排列。中美洲和南美洲的部分传统采用不同约定。', '尼泊尔国旗': '尼泊尔国旗由两个相连的三角旗片构成，旗面为深红色，带蓝色边框。上方旗片有白色月亮徽记，下方旗片有白色太阳。现代版本的徽记不含人脸。', '石柱': '钟乳石从洞顶向下生长，石笋则在滴水到达地面的位置向上堆积。当石笋与向其供水的钟乳石相接时，两者形成连续的石柱。', '童子军': '做Scouts BSA礼号时，右肘弯曲，手抬至肩部附近。拇指盖住弯曲的小指指甲，食指、中指和无名指并拢并指向上方。相比之下，Cub Scout礼号使用两根分开的手指。', '静水力学': '在均匀重力下的静水平衡中，同一水平高度各点的压强相等，压强随深度增加。因此，与均匀压强气体接触的自由液面保持水平，与瓶子的朝向无关；可以忽略瓶壁附近的小幅毛细效应。', '减速让行标志': '在英国，GIVE WAY（减速让行）是通常采用圆形的管制标志中的一个显著例外：其标志牌为倒三角形，红色边框、白色底面，并有黑色GIVE WAY字样。它要求道路使用者让主路上的交通先行。', '豆娘': '雄性Azure Damselfly（Coenagrion puella）体态纤细，呈蓝黑色，腹部第二节具有平底U形斑纹。豆娘的双眼分离，前翅和后翅形状相似；Coenagrion属休息时将翅合拢并沿身体方向放置，与宽展翅膀的蜻蜓不同。', '绞胎瓷': '绞胎瓷在烧制前将不同颜色的瓷泥揉合成胎。其纹理由胎体产生，内、外表面均有，而不是仅在表面绘制的装饰。表面贴附绞纹泥片属于另一种构造，不能据此假定原本纯色的胎芯内部也有同样的纹理。', '海冰': '在波浪作用下，新生的针状、片状冰晶（frazil ice）和油脂状冰（grease ice）聚集为近圆形的漂浮冰盘，称为饼状冰（pancake ice）。反复碰撞形成上翘的边缘。之后，这些冰盘可能相互叠覆，或冻结为连成一体的冰层。', '落叶松': '落叶松属（Larix）是落叶针叶树，不同于大多数保持常绿的针叶树。其叶为针叶，在长枝上单生，在短枝上簇生。', '扇贝': '扇贝有许多小眼，沿外套膜边缘排列，与其他外套膜结构相间分布。眼的数量有所不同；它们不是长在头部的一对眼睛。', '南非国旗': '南非国旗有一个开口朝向旗杆侧的绿色横向Y形，以及一个镶金色边的黑色旗杆侧三角形；上方为红色，下方为蓝色。绿色与红色、蓝色之间由白色边条隔开。', '深衣': '在所引《深衣》篇的饰边规则中，父母和祖父母在世的儿子使用绣饰衣缘；仅父母在世时，装饰衣缘为蓝色；孤子则使用白色衣缘。这些是所指定文本中的规则，并不是对所有历史深衣的普遍描述。', '浮世绘': '锦绘彩色版画为不同颜色使用不同的木版。位于一角和相邻边上的见当定位标记，有助于使连续多次印刷套准。每块色版只承载整个图案中属于自己的部分，而不是一幅完整的多色画。', '基本流程图': '在ASQ常用流程图符号中，矩形表示过程步骤，菱形表示具有不同结果分支的判断或问题，圆角矩形或椭圆表示开始或结束。箭头表示流程方向。必须指定这一约定，因为形状的含义并非普遍一致。', '多普勒效应': '每一时刻发出的声音，都从发出时声源所在的位置出发，以相对于介质的声速向外传播。当声源以低于声速的速度移动时，相邻波前在声源前方间距较小、在后方间距较大；各波前的圆心仍位于各自发出时的位置。', '加拿大元': '加拿大Frontier系列聚合物钞票具有一个大透明窗，其中包含金属质感的肖像和建筑元素。这些金属元素周围的聚合物是透明的，因此可以透过这些区域看到背景。钞票其余印刷部分不是透明的。'}


def normalize_case(c):
    mapping={'状态与阶段':'属性与状态','关系与系统':'关系与组织','属性与规律':'属性与状态','关系':'关系与组织'}
    c['knowledge_types']=list(dict.fromkeys(mapping.get(k,k) for k in c['knowledge_types']))
    if c['domain']=='行为与动作':c['domain']='行为动作'
    c.setdefault('execution_checks',c.get('preservation_checks',[]))
    q=c['question_id']
    if q=='exp20_t2i_doppler':c['knowledge_types']=['功能与机制','关系与组织']
    if q=='exp20_t2i_cad50':c['knowledge_types']=['特征与结构','功能与机制']
    move={'dev20_edit_fern':['K2'],'dev20_edit_permanganate':['K2'],'dev20_edit_scout':['K2'],'dev20_edit_giveway':['K2']}
    if q in move:
        c['preflight_criteria_revision']={'original_knowledge_checks':c['knowledge_checks'],'reason':'Move explicitly requested preservation/action/text execution out of pure knowledge scoring before generation.'}
        c['execution_checks'] += [k['criterion'] for k in c['knowledge_checks'] if k['id'] in move[q]]
        c['knowledge_checks']=[k for k in c['knowledge_checks'] if k['id'] not in move[q]]
    if q=='dev20_edit_scout':c['application_level']='direct'
    if q=='dev20_edit_column':c['gaps'].append('石柱题含名词到形态的直接应用，不因写了沉积过程就声称强多跳推理。')
    if q=='exp20_t2i_larch':c['gaps'].append('裸枝的精确树种外观难以独立鉴别，K2作为辅助身份判断可能不可观察。')
    presets={
     'jiaotai':(['视点剖示'],'因果',['变体/子类型','视角/暴露']),
     'pancake':(['环境作用','多实例对比'],'因果',['阶段/时刻','环境场所']),
     'larch':(['环境作用'],'因果',['阶段/时刻','环境场所']),
     'scallop':([] ,None,['使用状态','视角/暴露']),
     'flag':([], '文化语境',['规约版本','视角/暴露']),
     'shenyi':([], '文化语境',['规约版本','事件/典故语境']),
     'ukiyoe':(['过程时刻','多实例对比'],'因果',['阶段/时刻','视角/暴露']),
     'flowchart':(['规约场景'],'因果',['规约版本','数量/编组']),
     'doppler':(['动态要素'],'光学传播（声波传播类比）',['数值/阈值','阶段/时刻']),
     'cad50':(['视点剖示'],'光学媒介',['规约版本','视角/暴露']),
     'lycoris':(['环境作用'],'因果',['阶段/时刻','环境场所']),
     'fern':(['视点剖示'],None,['视角/暴露','年龄/生长阶段']),
     'permanganate':(['过程时刻'],'因果',['阶段/时刻','使用状态']),
     'harp':(['细节密度'],'多实例',['规约版本','数量/编组']),
     'nepal':(['环境作用'],'光学媒介',['规约版本','使用状态']),
     'column':(['过程时刻'],'因果',['阶段/时刻','环境场所']),
     'scout':(['光照时段'],'光影投影',['规约版本','使用状态']),
     'hydrostatic':(['环境作用'],'容纳承载',['使用状态','数值/阈值']),
     'giveway':(['规约场景'],'文化语境',['规约版本']),
     'damselfly':([],None,['变体/子类型','年龄/生长阶段','使用状态'])}
    name=q.rsplit('_',1)[1];scene,combo,pre=presets[name]
    if name=='doppler':combo='波动传播（补充）'
    c['scene_dimensions']={'scene_types':scene,'combo_type':combo,'premise_types':pre,'note':'描述性分层，不按标签数推算难度；执行附带关系不自动算知识组合。'}
    return c


def freeze():
    concepts=read(ROOT/'datasets/demiwtg/meta/concepts.json')['concepts']
    names={c['name']:(i,c) for i,c in enumerate(concepts)}
    aliases={a:(i,c) for i,c in enumerate(concepts) for a in c.get('aliases',[]) if isinstance(a,str)}
    cases=[]
    for task in ['edit','t2i']:
        obj=read(RUN/f'{task}_candidate_materials.json')
        candidates=obj if isinstance(obj,list) else obj.get('cases',obj.get('candidates'))
        assert len(candidates)==10,(task,len(candidates))
        for raw in candidates:
            c=normalize_case(dict(raw));c['task']=task;c['runtime_batch']='expansion20_v1'
            key=c.get('concept_name',c['concept']);hit=names.get(key) or aliases.get(key)
            supplied=c.get('taxonomy_record',{})
            if 'taxonomy_paths' not in supplied:
                c['taxonomy_record']={'concept_name':key,'concepts_path':str(ROOT/'datasets/demiwtg/meta/concepts.json'),
                 'status':'existing_concept' if hit else 'proposed_not_written','taxonomy_paths':hit[1].get('taxonomy',[]) if hit else [],
                 'json_pointer':f'/concepts/{hit[0]}' if hit else None,'suggested_semantic_path':c.get('suggested_semantic_path') or supplied.get('proposed_taxonomy_path')}
                if supplied.get('index') is not None:
                    c['taxonomy_record'].update(concept_name=supplied.get('name',key),taxonomy_paths=supplied.get('taxonomy',[]),json_pointer=f"/concepts/{supplied['index']}")
            c.setdefault('reference_images',[]);c.setdefault('execution_checks',['题设主体与操作正确；非目标场景保持；不增加无关文字。'])
            c.setdefault('exceptions',[]);c.setdefault('gaps',[])
            if 'scene_dimensions' not in c: c['scene_dimensions']={'scene_types':[], 'combo_type':None, 'premise_types':[]}
            assert c['prompt'] and c['prompt_zh'] and c['knowledge_text'] and c['sources'] and c['knowledge_checks']
            for im in c['reference_images']+([c['edit_source']] if task=='edit' else []):
                p=Path(im['path']);assert p.is_absolute() and p.exists();digest=sha(p.read_bytes())
                if im.get('sha256'):assert im['sha256']==digest
                im['sha256']=digest
            if task=='edit':assert all(im['sha256']!=c['edit_source']['sha256'] for im in c['reference_images'])
            ids={s['source_id'] for s in c['sources']}
            for k in c['knowledge_checks']: assert set(k['source_ids']) <= ids
            for source in c['sources']:
                if source.get('snapshot_path'):
                    digest=sha(Path(source['snapshot_path']).read_bytes())
                    if source.get('snapshot_sha256'):assert digest==source['snapshot_sha256']
                    source['snapshot_sha256']=digest
            cases.append(c)
    assert len({c['question_id'] for c in cases})==20
    jobs=[]
    for c in cases:
        c['knowledge_condition']='multimodal' if c['reference_images'] else 'text'
        for cond in ['baseline',c['knowledge_condition']]:
            images=[]
            if c['task']=='edit':images.append({k:c['edit_source'][k] for k in ['path','sha256']}|{'role':'edit_source'})
            if cond=='multimodal':images += [{k:im[k] for k in ['path','sha256']}|{'role':'retrieval_reference'} for im in c['reference_images']]
            prompt='TASK\n'+c['prompt']+'\n\nProduce one image fulfilling the task. An edit_source image is the scene to edit; retrieval_reference images are knowledge materials, not the target composition. Use relevant supplied knowledge without copying source-page layouts. Preserve non-target content for editing. Do not add explanatory text unless the task asks for it.'
            if cond!='baseline':prompt+='\n\nSOURCE MATERIALS\n'+c['knowledge_text']
            jobs.append(dict(job_id=c['question_id']+'__'+cond+'__r1',question_id=c['question_id'],task=c['task'],condition=cond,images=images,prompt=prompt,seed=20260913))
    publish(RUN/'cases.json',encoded({'cases':cases}))
    publish(RUN/'jobs.jsonl',''.join(json.dumps(j,ensure_ascii=False)+'\n' for j in jobs).encode());validate_jobs(RUN/'jobs.jsonl')
    publish(RUN/'protocol.json',encoded({'cases':20,'tasks':dict(Counter(c['task'] for c in cases)),'models':['bagel','gemini'],
      'jobs_per_model':40,'gemini_request_cap':40,'repeats':1,'conditions':'baseline vs text or multimodal chosen by source support before outputs',
      'scope':'independent development; all results retained; no formal100 admission or actual retrieval claim',
      'data_policy':'old/new assets and archived input images may be used; preserve provenance and isolate source families from later test',
      'review':'source-bound assistant visual review; knowledge/execution/quality separate; no gain-based filtering'}))
    print(json.dumps({'cases':20,'domains':dict(Counter(c['domain'] for c in cases)),'with_knowledge_images':sum(bool(c['reference_images']) for c in cases)},ensure_ascii=False))


def table(headers,rows):
    return '<div style="overflow-x:auto"><table class="exp-table"><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</table></div>'


def classrow(c,i):
    m=c['taxonomy_record'];d=c.get('scene_dimensions',{})
    return [str(i),c['concept'],'；'.join(m.get('taxonomy_paths',[])) or '待补充；建议 '+str(m.get('suggested_semantic_path') or c['domain']),c['domain'],
     '、'.join(c['knowledge_types']),c['task'],LEVELS.get(c['application_level'],c['application_level']),
     ('图文，参考图'+str(len(c['reference_images']))+'张') if c['reference_images'] else '文本',
     '、'.join(d.get('scene_types',[])) or '无突出复杂来源',d.get('combo_type') or '不适用','、'.join(d.get('premise_types',[])) or '见题面条件']

HEAD=['题号','概念／情境','Taxonomy path','主域','知识内容','题型','应用层次','主对比资料','场景复杂来源','组合','前提']
STYLE='<style>.exp-case{font:16px/1.65 sans-serif;max-width:1300px}.exp-case pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f6;padding:10px}.exp-case img{max-width:100%;max-height:600px}.exp-case figure{margin:8px}.exp-table{border-collapse:collapse;font:14px/1.6 sans-serif}.exp-table td,.exp-table th{border:1px solid #ccd;padding:8px;vertical-align:top;min-width:100px}.outputs{width:100%;table-layout:fixed}.outputs td{width:50%;vertical-align:top;border:1px solid #ddd}.exp-case details{margin:12px 0}.exp-case summary{cursor:pointer;font-weight:bold}</style>'


from batch_review_summary import summary as batch_summary


def render():
    cases=read(RUN/'cases.json')['cases'];errata=read(RUN/'display_errata.json') if (RUN/'display_errata.json').exists() else {};rp=RUN/'reviews.json';reviews=read(rp)['reviews'] if rp.exists() else []
    lookup={(r['model'],r['job_id']):r for r in reviews}
    intro='# 20题开发验证：10编辑＋10文生图\n\n独立新批次，旧18题保持不变。材料可来自旧库、新Wiki及旧benchmark输入图；关联或图注不自动算知识证据。主对比为无额外资料／有资料，每条件一次，全部输出保留；图像和文字是否互补须看每题支持范围。\n\n编辑原图两组相同，知识参考图另列。资料是人工式选定的来源，不冒称自动检索；助手审核不是人工金标准。\n\n'
    intro+='本批覆盖10个主域：7题使用图文资料、13题使用文字资料；11题条件应用、6题直接应用、3题关系应用，没有多知识组合题。80张输出均有助手逐图审核；单次开发验证不能代表总体准确率或29域覆盖。\n\n'
    intro+=md_table(['题号','题型','概念','主域'],[[str(i),c['task'],c['concept'],c['domain']] for i,c in enumerate(cases,1)])
    # Classification definitions are shared, but old batch counts and old case examples stay in its notebook.
    intro+='\n应用层次：direct＝直接用概念知识；conditional＝按条件选择知识；relational＝落实关系／过程；compositional＝联合应用多条知识。这些不是难度等级。\n'
    definitions=guide().split('## 18题分类总表')[0].replace('本轮18题','本批题目').replace('当前例子','旧批示例（不是本页题号）')
    definitions=definitions[:definitions.index('\n第1—12题主对比')]
    cells=[dict(cell_type='markdown',id='classification-guide',metadata={},source=definitions),dict(cell_type='markdown',id='batch-review-summary',metadata={},source=batch_summary('expansion20_v1')+'\n## 本批20题分类总表\n'),dict(cell_type='markdown',id='intro',metadata={},source=intro)]
    pages=[('classification',STYLE+table(HEAD,[classrow(c,i) for i,c in enumerate(cases,1)]),'分类总表')]
    for i,c in enumerate(cases,1):
        correction=errata.get(c['question_id'])
        if correction:
            c=dict(c);c['exceptions']=correction['exceptions']
        out=[STYLE,'<div class="exp-case">',table(HEAD,[classrow(c,i)]),'<h2>'+str(i)+'．'+esc(c['concept'])+'</h2>']
        if correction:out.append('<details open><summary>审核备注勘误（保留冻结原记录）</summary><pre>'+esc(correction)+'</pre></details>')
        out+=['<h3>中文题面</h3><pre>'+esc(c['prompt_zh'])+'</pre><details><summary>英文题面</summary><pre>'+esc(c['prompt'])+'</pre></details>']
        if c.get('edit_source'):out.append('<h3>编辑原图（两组共同输入）</h3>'+picture(c['edit_source']['path'],c['edit_source'].get('observations','待编辑原图'))+'<p>原图来源与生成属性：</p><pre>'+esc(c['edit_source'])+'</pre><p>合成原图只作为编辑场景，不作为现实知识的事实证据。</p>')
        out+=['<h3>有资料组的知识输入</h3><p>以下中文完整翻译实际输入的英文资料；两者均为助手依据来源整理的知识说明，不是来源逐字引文。原文引用与库内定位见来源栏。此次仅修正展示，不修改已运行的请求。</p><h4>完整中文译文</h4><pre>'+esc(KNOWLEDGE_ZH_FULL[c['concept']])+'</pre><h4>实际输入的完整英文资料</h4><pre>'+esc(c['knowledge_text'])+'</pre><p>知识参考图共 '+str(len(c['reference_images']))+' 张，下方逐张显示；编辑原图另列，不计入知识参考图。</p>']
        if not c['reference_images']:out.append('<p>无知识参考图片；编辑原图不算额外知识图。</p>')
        for im in c['reference_images']:out.append(picture(im['path'],'知识参考图：'+im.get('support_scope','待核验')))
        out.append('<details><summary>知识来源、库内定位与支持范围</summary>')
        out.append('<pre>'+esc(c['taxonomy_record'])+'</pre>')
        for s in c['sources']:
            out.append('<h4>'+esc(s['source_id'])+'</h4><p><a href="'+esc(s['url'])+'">'+esc(s.get('title',s['url']))+'</a></p><blockquote>'+esc(s['quote'])+'</blockquote><p>'+esc(s['support_scope'])+'</p><pre>'+esc({k:v for k,v in s.items() if k not in ['quote','support_scope']})+'</pre>')
        out.append('</details><h3>全部生成对比</h3>')
        for model in ['bagel','gemini']:
            out.append('<h4>'+model.upper()+'</h4><table class="outputs"><tr><th>无额外资料</th><th>有'+('图文' if c['reference_images'] else '文字')+'资料</th></tr><tr>')
            for cond in ['baseline',c['knowledge_condition']]:
                jid=c['question_id']+'__'+cond+'__r1';p=RUN/model/'jobs'/jid/'result.json';out.append('<td>')
                if p.exists():
                    r=read(p)
                    if r['ok']:
                        assert sha(Path(r['image']).read_bytes())==r['output_sha256'];out.append(picture(r['image'],model+' '+cond))
                    else:
                        out.append('<p>本次输出失败；原返回保留，不挑选或自动补抽。</p><pre>'+esc(r.get('error'))+'</pre>')
                        response=p.parent/'response.json'
                        if response.exists():
                            obj=read(response);choices=obj.get('choices') or [];msg=choices[0].get('message',{}) if choices else {}
                            blocks=list(msg.get('images') or [])+(msg.get('content') if isinstance(msg.get('content'),list) else [])
                            urls=[]
                            for block in blocks:
                                if not isinstance(block,dict):continue
                                im=block.get('image_url');url=im.get('url') if isinstance(im,dict) else im
                                if isinstance(url,str) and url.startswith('data:image/') and ';base64,' in url and url not in urls:urls.append(url)
                            for n,url in enumerate(urls,1):out.append('<figure><img src="'+esc(url)+'"><figcaption>同次失败响应中的原图 '+str(n)+'，仅供查看，不改契约失败结果。</figcaption></figure>')
                else:out.append('<p>尚无结果。</p>')
                rv=lookup.get((model,jid))
                if rv:out.append('<details open><summary>助手逐图审核</summary><pre>'+esc(rv)+'</pre></details>')
                out.append('</td>')
            out.append('</tr></table>')
        for title,key in [('条件→知识→可见结果','application_links'),('知识判据与例外','knowledge_checks'),('执行与保持','execution_checks'),('合理例外','exceptions'),('证据缺口与题目局限','gaps'),('出图前判据调整（保留原稿）','preflight_criteria_revision'),('出图前题面调整（保留原稿）','preflight_revision')]:
            out.append('<details><summary>'+title+'</summary><pre>'+esc(c.get(key,[]))+'</pre></details>')
        out.append(actual_input_details(c,'expansion20_v1'));out.append('</div>')
        pages.append((f'case-{i:02}', ''.join(out),f'第{i}题：{c["concept"]}'))
    for i,(ident,html,label) in enumerate(pages,1):
        p=RUN/'notebook_cards'/f'{ident}.html';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(html)
        code='from pathlib import Path\nfrom IPython.display import display, HTML\ndisplay(HTML(Path('+repr(str(p))+').read_text()))'
        cells.append(dict(cell_type='code',id=ident,metadata={'jupyter':{'source_hidden':True}},execution_count=i,source=code,outputs=[dict(output_type='display_data',metadata={},data={'text/html':html,'text/plain':label})]))
    nb=dict(cells=cells,metadata={'kernelspec':{'display_name':'demiwtg','language':'python','name':'demiwtg'},'language_info':{'name':'python'}},nbformat=4,nbformat_minor=5)
    from notebook_frontmatter import apply as apply_frontmatter
    apply_frontmatter(nb, 'expansion20_v1')
    (RUN/'cases_review_zh.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=1))
    for cell in nb['cells']:
        if cell['cell_type']=='code':cell['outputs']=[];cell['execution_count']=None
    Path(__file__).with_suffix('.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=1))
    print('Rendered',len(cases),'cases with',len(reviews),'reviews')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','render']);a=p.parse_args()
    freeze() if a.action=='freeze' else render()
