"""Display-only version provenance and summaries of each frozen review protocol."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
from collections import Counter
from pipeline import STATE, read
from classification_notebook import md_table

BATCHES = [('pilot', 'V1（首轮12题）'), ('expansion20_v1', 'V2（20题）'), ('version3_20', 'V3（20题）')]


def overall():
    rows=[]
    for batch,label in BATCHES:
        cases=read(STATE/batch/'cases.json')['cases']
        reviews=read(STATE/batch/('reviews_combined.json' if batch=='pilot' else 'reviews.json'))['reviews']
        lookup={(r['model'],r['job_id']):r for r in reviews}
        for model in ['bagel','gemini']:
            for task in [None,'edit','t2i']:
                selected=[c for c in cases if task is None or c['task']==task]
                results=[]
                for supplied in [False,True]:
                    rs=[]
                    for c in selected:
                        condition=('multimodal' if batch=='pilot' or (batch=='version3_20' and c['reference_images']) else ('text' if batch=='version3_20' else c['knowledge_condition'])) if supplied else 'baseline'
                        rs.append(lookup[model,c['question_id']+'__'+condition+'__r1'])
                    k=[bool(r['knowledge']) and all(v=='pass' for v in r['knowledge'].values()) for r in rs]
                    joint=sum(ok and r['execution_pass'] is True for ok,r in zip(k,rs))
                    n=len(rs)
                    fmt=lambda v:f'{v}/{n}（{v/n:.1%}）'
                    qs=[r["quality"] for r in rs if isinstance(r.get("quality"),(int,float))]
                    quality=(f"{sum(qs)/len(qs):.2f}（{len(qs)}/{n}有评分）" if qs else "未评分")
                    results.append((fmt(sum(k)),fmt(joint),quality))
                rows.append([label,model.upper(),{None:'全部','edit':'编辑','t2i':'文生图'}[task],results[0][0]+' → '+results[1][0],results[0][1]+' → '+results[1][1],results[0][2]+' → '+results[1][2]])
    s='## 三版整体通过统计：各轮原有审核口径\n\n每格均为 **无资料 → 有资料**，同题、同模型配对。优先看“全部”行，另列编辑／文生图。\n\n'
    s+=md_table(['轮次','模型','范围','全部知识项通过','知识＋执行通过','画质均值（1–5）'],rows)
    s+='\n**口径与限制：**\n\n- V1读取原 `reviews_combined.json`：全部冻结K项通过，联合项再要求原 `execution_pass=true`；不混入后来另存的执行补审。只统计首轮12题，页面后续6题不混入。\n- V2读取原 `reviews.json`，使用该轮冻结K与原执行判断；不把V1或V3的判据追套到V2。\n- V3读取原 `reviews.json`：全部冻结K通过，联合项再要求全部冻结执行项通过；后补指令审核单独显示，不回写这张历史统计。原协议中可选“若”项未出现可判通过，不能解释成展示了该知识。\n- “知识＋执行通过”是依据各轮已保存字段汇总的联合通过数，**不是经完整身份、所有遗漏要求及画质共同验收的整题成功率，也不是bench200正式主成绩**。三轮未在此关联T2I v6.0 V2／编辑QIB正式成绩，不新增总分或及格线。\n- 画质沿用各轮保存的 `quality` 均值；V3还有逐项画质记录。画质不加入联合通过，不因均值相同就视为细则一致。\n- 有资料：V1全部图文；V2预定7题图文＋13题文字；V3预定16题图文＋4题文字。图片／文字消融及显式答案诊断不混入主对比。无资料编辑仍输入编辑原图。\n- 每条件单次生成、助手审核；预选资料，无自动检索、无微调。批次题目、资料与判据不同，不能用这张表判定难度或知识收益变化的因果。Gemini指各批实际保存的模型请求，并非所有闭源SOTA。\n'
    return s


def v3_overview():
    cs=read(STATE/'version3_20/cases.json')['cases']
    s='## V3整体出题说明与题目索引\n\n本批尝试将知识复杂度与画面复杂度分开：保留条件、关系及多知识组合，增加能支持具体部件、状态或连接知识的参考图，增加文字／图片消融及预选显式视觉要求诊断。实际选材仍包含历史概念和材料复用，不应描述为已通过新版curation对新概念广泛随机采样。\n\n**实际输入来源：**10道编辑的原图是6张imagegen场景＋4张代码绘制示意底图；这些仅作待编辑场景，不是事实证据。知识来源和参考图的支持范围、缺口在各题展开。这是历史V3实际做法，与V4已确认的“不使用生成输入图”不同，不能用V4决定改写V3。\n\n**维度来源：**延续V2的领域／taxonomy／概念、六类知识内容、题型、四种知识应用方式、资料形态，并保留当时使用的场景复杂度、组合类型、前提类别。后三项是历史出题字段；标签数不代表难度，也不是目前V4仍全部采纳的维度。\n\n**实验安排：**20题，两模型各78张，共156张；16题无资料／文字／图片／图文，4题无资料／文字，6道预选题另加显式视觉要求。诊断用于解释行为，单次失败不能证明模型内部原因。所有失败保留；本批是开发实验，不是正式测试。\n\n'
    dims=[('主知识领域','domain'),('题型','task'),('知识应用方式','application_level')]
    s+=md_table(['维度','分布'],[[name,'；'.join(f'{v}：{n}' for v,n in Counter(c[key] for c in cs).items())] for name,key in dims])
    s+='\n'+md_table(['题号','概念','主域','题型','知识应用方式','主对比有资料条件'],[[str(i),c['concept'],c['domain'],c['task'],c['application_level'],'图文' if c['reference_images'] else '文字'] for i,c in enumerate(cs,1)])
    return s


def apply(nb,batch):
    """Idempotent frontmatter edit; never touches case outputs or stored scores."""
    cells=[c for c in nb['cells'] if c.get('id') not in ['three-version-results','v3-overview']]
    for c in cells:
        if c.get('id')=='classification-guide':
            s=''.join(c['source']) if isinstance(c['source'],list) else c['source']
            if batch=='pilot':
                s=s.replace('# 出题分类设计与阅读说明','# V1知识应用设计与阅读说明')
                for title in ['场景复杂度来源','组合类型','前提类别']:
                    start=s.find('\n## '+title+'\n')
                    if start>=0:
                        end=s.find('\n## ',start+1)
                        s=s[:start]+(s[end:] if end>=0 else '')
                s='\n'.join(line for line in s.split('\n') if not any(line.startswith('| '+v+' |') for v in ['场景复杂度来源','组合主类','前提类别']))
                note='**版本归属校正：**V1首轮12题围绕概念知识的直接、条件、关系和组合应用组织；其冻结题目没有场景复杂度／组合类型／前提类别字段。页面总表及详情中的相关标签是后补的回顾性分析，不能当作V1最初出题依据。完整旧维度设计说明移至V2；本页另含后续6题，与首轮12题区分。'
            else:
                note='**版本归属说明：**场景复杂度来源、组合类型、前提类别在V2／V3已作为冻结出题字段使用。下列完整旧维度说明归于V2；V1首轮12题的同类展示标签是后补分析，不是原始冻结维度。这里保留历史设计，不代表V4继续采用所有旧维度。'
            if note not in s:s=s.split('\n',1)[0]+'\n\n'+note+'\n'+s.split('\n',1)[1]
            c['source']=s
        if c.get('id')=='batch-review-summary':
            s=''.join(c['source']) if isinstance(c['source'],list) else c['source']
            s=s.replace('新规则仍待物化为统一评分协议；当前通过数沿用冻结K，不是重评分。QIB与现有1–5画质不直接换算；同一缺陷不能在新总分中重复累加。','主评分保留旧T2I／编辑协议；知识诊断独立记录，统一评分草案未采纳。当前通过数沿用冻结K，不是重评分，QIB与现有1–5画质不直接换算。')
            if batch=='pilot':s='\n'.join(line for line in s.split('\n') if not any(line.startswith('| '+v+' |') for v in ['场景复杂度来源','组合类型','前提类别']))
            c['source']=s
    front=[dict(cell_type='markdown',id='three-version-results',metadata={},source=overall())]
    if batch=='version3_20':front.append(dict(cell_type='markdown',id='v3-overview',metadata={},source=v3_overview()))
    # Retain the original introduction/guide first, then show statistics before cases.
    cells[1:1]=front
    nb['cells']=cells
    return nb
