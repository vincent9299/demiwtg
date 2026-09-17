"""Two source-grounded scene cases, isolated from earlier frozen batches."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from copy import deepcopy
from pipeline import ROOT, STATE, read
from bagel_runner import encoded, publish, sha, validate_jobs

OUT=STATE/'scene_v1'

def main():
    old={c['question_id']:c for c in read(STATE/'pilot/cases.json')['cases']}
    meta={x['question_id']:x['taxonomy_record'] for x in read(STATE/'nonobject_v1/original_case_taxonomy.json')}
    cases=[]
    def build(qid, base, concept, zh, en, sourceids, knowledge, kzh, checks, chain, scene, combo, premises, risks, paired):
        sources={s['source_id']:s for c in old.values() for s in c['sources']}
        src=[deepcopy(sources[s]) for s in sourceids]
        for s in src:
            assert sha(__import__('pathlib').Path(s['snapshot_path']).read_bytes())==s['snapshot_sha256']
        c=dict(question_id=qid,concept=concept,domain=old[base]['domain'],taxonomy_record=deepcopy(meta[base]),
               task='t2i',application_level='compositional',knowledge_family_id=old[base]['knowledge_family_id'],
               knowledge_types=['特征与结构','属性与状态','关系与组织'] if 'ecology' in qid else ['关系与组织','功能与机制'],
               prompt=en,prompt_zh=zh,knowledge_text=knowledge,knowledge_zh=kzh,sources=src,reference_images=[],
               evidence_mode='text',runtime_batch='scene_v1',
               scene_dimensions=dict(scene_types=scene,combo_type=combo,premise_types=premises,paired_simple_cases=paired,
                   note='复杂度标签用于分层，不作难度分数；与旧简单题共享知识家族，但任务和图像输入不同，不是严格的难度消融。'),
               classification_audit=dict(content='特征与结构、属性与状态、关系与组织' if 'ecology' in qid else '关系与组织、功能与机制',
                   mining='状态与阶段、生物规律、可组合关系' if 'ecology' in qid else '概念自身结构、环境交互、物理规律',
                   reason='同场景中分别调用阶段外观与捕食关系；不把两只虫自动算跨域。' if 'ecology' in qid else '依据功能及连接状态选择出水构件，要求水路与建筑关系同时成立。'),
               knowledge_checks=[dict(id=f'K{i}',criterion=k,observable_region=region,source_ids=ids,exceptions=[]) for i,(k,region,ids) in enumerate(checks,1)],
               application_links=chain,
               execution_checks=['主体、阶段和题设位置明确可辨，关键部位不被背景遮挡。','同一连贯场景，不分格、不加文字、不复制资料页面。'],
               exceptions=risks,
               gaps=['本轮只输入文字资料；没有把普通外观参考图当成功能／关系证据，不声称完成图像RAG。','来源复核与生成观察均为助手审核，不是人工金标准。','每模型每条件一次，不按是否提升筛选保留；更复杂场景可能增加绘制与观察负担。'],
               assistant_assessment={'fact_basis':'复用已核验来源快照，逐项标明支持范围。','curation_value':'检查多个知识约束在同场景联合应用，未预设难度或增益。','image_support':'无输入知识图片；生成图仅作输出观察，不作事实来源。'})
        cases.append(c)
    build('dev_scene_ecology','dev_conditional_lady_larva','七星瓢虫·成幼体共同捕食场景',
          '一张自然观察风格的微距照片：同一根花园植物嫩枝上，有一只成年七星瓢虫和一只该物种的四龄幼虫，它们分别正在捕食嫩枝上的小型植食性昆虫。两只捕食者、各自的头部与猎物都清楚可见，放在同一对焦平面内。表现真实尺度和一个连贯场景，不要拼图或文字。',
          'A natural-history macro photograph on one young garden-plant shoot: one adult seven-spot ladybird (Coccinella septempunctata) and one fourth-instar larva of the same species are each actively preying on small herbivorous insects on the shoot. Both predators and their respective heads and prey are clearly visible within the same focus plane. Use realistic scale in one coherent scene, without panels or text.',
          ['lady_larva_local','lady_stage_local','lady_larva_cornell','lady_food_local','lady_food_ncsu'],
          'Source-grounded notes: Fourth-instar Coccinella septempunctata larvae are elongated and segmented, dark grey to black with orange markings. Ladybird larvae have three pairs of prominent legs near the thorax. Adults and larvae prey on aphids and other small insects and their eggs. Aphids are a principal food of this species. These notes give stage and diet knowledge, not a target composition.',
          '有来源的转述：七星瓢虫四龄幼体细长分节，深灰至黑色，带橙色斑块；幼体有三对明显的胸足。成虫与幼虫都捕食蚜虫及其他小昆虫和卵；蚜虫是本物种的主要食物。这些是阶段及食性知识，不指定目标构图。',
          [('幼体细长分节、深色带浅／橙斑；不能把幼体也画成圆拱红色成虫。','幼体全身',['lady_larva_local','lady_stage_local']),
           ('幼体可见的足应与三对胸足相容，不能沿整个腹部画成多足毛虫；被遮挡的足不强数。','幼体胸部和腹部',['lady_larva_cornell']),
           ('成年个体头部与来源支持的昆虫猎物发生可辨取食接触。','成虫口部及猎物',['lady_food_ncsu','lady_food_local']),
           ('幼体头部也与独立的合法昆虫猎物发生可辨取食接触，不是仅同框相邻。','幼体口部及猎物',['lady_food_ncsu'])],
          [dict(given='同一物种成虫和四龄幼体同框。',facts=['lady_larva_local','lady_stage_local','lady_larva_cornell'],visual_result='两个阶段不能复用同一成虫外形，幼体形态和足的分布需同时成立。'),
           dict(given='两者各自正捕食小型植食性昆虫。',facts=['lady_food_local','lady_food_ncsu'],visual_result='各自口部需要有可辨合法猎物接触；只画几只虫在叶上不充分。')],
          ['多实例对比','交互链'], '生态互动',['年龄/生长阶段','数量/编组','使用状态'],
          ['不强制猎物一定是蚜虫；若其他猎物的食性与身份不能判定则待核验。','单幅静图只能支持捕食姿态／接触，不能证明真实摄食行为。','足遮挡记无法观察，不以必须六足全露代替合理视角；不数微小毛刺。','成虫完整身份及物种精确斑纹仍需独立审核，局部知识通过不等于整体身份正确。'],[6,9])
    build('dev_scene_drainage','dev_relational_gargoyle','滴水兽·功能构件与装饰构件的雨天对照',
          '一幅真实建筑观察风格的雨天场景：从侧前方同时看到一段哥特式屋檐、上方集水槽、外墙及下方庭院。左侧伸出的张嘴石兽是接通屋顶集水槽、出口畅通的传统功能性滴水兽，槽中已经有持续径流。右侧是一尊实心的装饰石兽，没有任何内部水路连接。两尊石兽及其口部都清楚可见。表现这一时刻的排水状态；不要剖面、箭头、标签或拼图。',
          'A realistic architectural observation scene in rain, viewed obliquely from the side: a Gothic roof edge, its gutter above, the exterior wall and the courtyard below are visible together. The projecting open-mouthed stone creature on the left is a traditional working gargoyle connected to the roof gutter, with an unobstructed outlet and sustained runoff already in the gutter. The stone creature on the right is a solid decorative carving with no internal water connection. Both carvings and their mouths are clearly visible. Show the drainage state at this moment, without cutaways, arrows, labels or panels.',
          ['gargoyle_local','gargoyle_function_local','gargoyle_historic_england'],
          'Source-grounded notes: A traditional functional gargoyle carries roof runoff in a trough and typically discharges it through an open mouth, away from the masonry wall. A decorative stone grotesque is not automatically a functional waterspout. Whether it carries roof drainage depends on an actual connected water route. Surface rain and drips do not establish such a route.',
          '有来源的转述：传统功能性滴水兽以沟槽引导屋面径流，通常经张开的口部将水排离砌体外墙。装饰石兽并不自动具有排水功能；承担屋顶排水需要实际连接的水路。表面雨水与滴水不等于接通了屋顶排水。',
          [('左侧持续排水起于口部，而不是眼睛、脚部或身体外的空中。','左兽口部与水流起点',['gargoyle_local']),
           ('该股水流先向建筑外排离墙面，而不是紧贴墙面向下流。','口部、水流和墙面相对位置',['gargoyle_function_local']),
           ('右侧无水路的实心装饰兽不凭空出现由内部排出的集中屋顶水柱；正常雨滴、表面滴水允许。','右兽口部与周围降雨',['gargoyle_local','gargoyle_function_local'])],
          [dict(given='左侧已接通、畅通且有持续屋顶径流。',facts=['gargoyle_local','gargoyle_function_local'],visual_result='口部出流并排离墙面，连接、功能和空间结果必须一致。'),
           dict(given='右侧实心且无内部水路。',facts=['gargoyle_function_local'],visual_result='不能因外形相似就复制左侧的内部排水水柱；这是题设条件与功能依据联合推论。')],
          ['环境作用','多实例对比','纵深层次'], '因果',['使用状态','数量/编组','环境场所'],
          ['不要求右兽完全干燥，表面雨水可能滴落；不能把普通滴水当功能性排水错误。','不要求内部通道可见，外观照片不能证明实际建筑内部水路，本题连接状态是明确题设。','不要求精确流速、水量、射程；庭院只是上下文，不额外强制水坑落点。'],[8])
    OUT.mkdir(parents=True,exist_ok=True)
    publish(OUT/'cases.json',encoded({'cases':cases}))
    jobs=[]
    for c in cases:
        for cond in ['baseline','text']:
            prompt=c['prompt']+'\n\nProduce one image fulfilling the task. Use relevant supplied materials without reproducing their page layout.'
            if cond=='text':prompt+='\n\nSOURCE MATERIALS\n'+c['knowledge_text']
            jobs.append(dict(job_id=c['question_id']+'__'+cond+'__r1',question_id=c['question_id'],task='t2i',condition=cond,prompt=prompt,images=[],seed=20260913))
    publish(OUT/'jobs.jsonl',''.join(json.dumps(j,ensure_ascii=False)+'\n' for j in jobs).encode());validate_jobs(OUT/'jobs.jsonl')
    publish(OUT/'protocol.json',encoded({'cases':2,'models':['bagel','gemini'],'conditions':['baseline','text'],'repeats':1,'gemini_request_cap':4,'status':'development_only; retain all outputs','paired_simple_cases_are_not_matched_ablations':True}))
    print(OUT)

if __name__=='__main__':main()
