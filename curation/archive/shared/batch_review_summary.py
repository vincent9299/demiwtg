"""Notebook-only, read-only comparison of frozen development reviews."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
from collections import Counter
from pipeline import ROOT, STATE, read
from classification_notebook import md_table, row_values


def summary(batch):
    batches={b:read(STATE/b/'cases.json')['cases'] for b in ['pilot','expansion20_v1']}
    reviews={b:{(r['model'],r['job_id']):r for r in read(STATE/b/('reviews_combined.json' if b=='pilot' else 'reviews.json'))['reviews']} for b in batches}
    def passed(b,c,model,knowledge):
        cond=('multimodal' if b=='pilot' else c['knowledge_condition']) if knowledge else 'baseline'
        r=reviews[b][model,c['question_id']+'__'+cond+'__r1']
        return bool(r['knowledge']) and all(v=='pass' for v in r['knowledge'].values())
    def count(b,cs,m,k):return sum(passed(b,c,m,k) for c in cs)
    s='## 两批开发题的资料效果与评分解释\n\n这里统计的是**每题所有已列K项通过**，不是整题答对率，也不是知识增强训练后的成绩。K1／K2是判据编号；结果为通过、冲突或不可观察。来源资料为预先选定，并未运行自动检索器。所有结果为助手审核、每条件一次的开发观察。\n\n'
    rows=[]
    for b,label in [('pilot','旧12题'),('expansion20_v1','新20题')]:
        for task,suffix in [(None,'整体'),('edit','中的编辑题'),('t2i','中的文生图题')]:
            cs=[c for c in batches[b] if task is None or c['task']==task];n=len(cs)
            rows.append([label+suffix]+[f'{count(b,cs,m,False)}/{n} → {count(b,cs,m,True)}/{n}' for m in ['bagel','gemini']])
    s+=md_table(['集合','BAGEL 无资料→有资料','Gemini 无资料→有资料'],rows)
    s+='\n旧12题此处为无资料／图文对比；新20题为无资料／各题预定资料（7题图文、13题文本）。旧notebook另含第13—18题，**不计入此处旧12题的结果或分布**。不同题目、资料形态和审核口径不能当作严格批次难度消融；旧12题另有执行补充审核，不与原分混算。\n\n'
    rows=[]
    for m in ['bagel','gemini']:
        transitions=Counter((passed(batch,c,m,False),passed(batch,c,m,True)) for c in batches[batch])
        rows.append([m.upper(),str(transitions[False,True]),str(transitions[True,False]),str(transitions[True,True]),str(transitions[False,False])])
    s+='### 本页所属批次：逐题配对变化\n\n'+md_table(['模型','未通过→通过','通过→未通过','两组均通过','两组均未通过'],rows)
    s+='\n“未通过”包括冲突和不可观察；净增加不等于改善题总数。旧12题BAGEL局部知识收益较明显，新20题收益有限，且存在编辑保持、绘制与多图角色使用障碍。旧马来貘条斑正确但身份错误，说明局部K通过不能代替完整知识与任务验收。新竖琴不可计数、童子军参考图覆盖场景等失败仍保留；不能据这些数值宣称追平闭源模型，或确定差距仅由知识造成。\n\n'
    s+='### 评分整合：如何区分问题\n\n'+md_table(['问题／记录','处理'],[
      ['可见结果与知识冲突','记录具体知识判据与证据；仅凭失败图不能断言模型内部不懂知识。'],
      ['题面明确要求展示，模型却未展示','知识项未验证，同时对应展示／执行项未完成；不从有效题分母删除。'],
      ['题目没有保证核心可观察／要求相互冲突','题目复审，按同一规则对各模型成对处理；不把题目缺陷当模型知识失败。'],
      ['审核不确定','复看原生分辨率和局部，必要时独立复核；仍不足则保留待审，不猜测通过。'],
      ['非知识执行／保持错误','逐项按题面授权、必要连带变化及具体前后差异判断；不要求像素一致。'],
      ['完整评分','结合bench200任务验收与来源绑定的知识项；身份、执行、保持、质量独立记录。现有case分数未据此重评。']])
    s+='\n新规则仍待物化为统一评分协议；当前通过数沿用冻结K，不是重评分。QIB与现有1–5画质不直接换算；同一缺陷不能在新总分中重复累加。完整经验见 [DESIGN第18节]('+str(ROOT/'curation/DESIGN.md')+':356)。\n\n'
    s+='### 从题目问题中总结的经验\n\n'+md_table(['问题','下一版预防'],[
      ['原图大但知识部位小','先定考点再准备原图；主体突出、目标区域足够大，不优先复用归档复杂图。'],
      ['判据要求比题面多','题面锁定必要展示部位与视角，但不写出知识答案；检查合法构图是否仍可绕开核心考点。'],
      ['同概念参考图未支持知识','逐图注明支持哪条知识、哪个区域、不能证明什么；不相关实例图不输入。'],
      ['只查局部颜色／形态，遗漏身份','将概念身份和必要结构纳入验收，不能靠一个局部K通过认定整题正确。'],
      ['复杂编辑负担盖过知识','一次核心改变，保留必要保持项；知识可以复杂，画面不必复杂。'],
      ['资料、题面、翻译与例外漂移','冻结前交叉核对完整模型输入及来源；展示勘误不改旧请求。']])
    s+='\n### 本页批次的题目分布（按维度）\n\n'
    cs=batches[batch];dims={k:Counter() for k in ['题型','主知识领域','知识内容','知识应用层次','主对比资料','场景复杂度来源','组合类型','前提类别']}
    for i,c in enumerate(cs,1):
        if batch=='pilot':
            r=row_values(c,i,batch)
            values={'题型':[c['task']],'主知识领域':[r[3]],'知识内容':r[4].split('、'),'知识应用层次':[c['application_level']],'主对比资料':['图文' if c['reference_images'] else '文本'],'场景复杂度来源':r[10].split('、'),'组合类型':[r[11]],'前提类别':r[12].split('、')}
        else:
            d=c.get('scene_dimensions',{})
            values={'题型':[c['task']],'主知识领域':[c['domain']],'知识内容':c['knowledge_types'],'知识应用层次':[c['application_level']],'主对比资料':['图文' if c['reference_images'] else '文本'],'场景复杂度来源':d.get('scene_types') or ['无突出复杂来源'],'组合类型':[d.get('combo_type') or '不适用'],'前提类别':d.get('premise_types') or ['未列明']}
        for k,v in values.items():dims[k].update(set(v))
    for tag in ['特征与结构','属性与状态','功能与机制','过程与变化','关系与组织','规则与约定']:dims['知识内容'].setdefault(tag,0)
    for tag in ['direct','conditional','relational','compositional']:dims['知识应用层次'].setdefault(tag,0)
    rows=[[k,'；'.join(f'{name}：{n}题' for name,n in sorted(v.items(),key=lambda x:(-x[1],x[0])))] for k,v in dims.items()]
    s+=md_table(['维度','题目数'],rows)
    s+=f'\n统计范围为本批{len(cs)}题，主域覆盖{len(dims["主知识领域"])}／29。知识内容、场景来源和前提可多选，计数之和可能超过题数；同题同标签只计一次。标签表示内容分布，不等于已验证该类知识或多跳难度。统计沿用本页展示分类，旧冻结标签不改。\n'
    return s
