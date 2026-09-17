"""Render a reviewable scoring proposal; no generation or historical rescoring."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from pathlib import Path
import nbformat
from pipeline import ROOT, STATE

OUT = STATE / 'scoring_protocol_draft_v1'
TEXT = r'''# 统一评分协议 v0.1 · 讨论草案

状态：提出可实施口径，尚未作为任何批次的正式评分规则。此文档属于当前评分讨论，不要求另一个出题会话先实现评分器。旧12题、新20题以及其他已冻结批次均不重写、不自动重评。

## 1．目的与评分对象

评估图文知识获取与应用：给定任务／原图与可选资料，模型是否将有依据的知识落实到正确对象和条件中的可见结果。分别报告知识应用、非知识任务执行、编辑保持、画质与美感。输出违反知识只称“知识要求画错／未满足”，不等于已确定模型内部不懂。

“知识／执行”按**判据的依据**划分，不按猜测的失败原因划分。同一要求的判据归类在出图前固定，不能有资料组改称指令执行、无资料组称知识。比如原题只点名尼泊尔国旗，其旗形属于知识；资料告知旗形后，此考点仍属知识应用。显式答案诊断组也复用同一判据，但单独报告，不混入主知识收益。

## 2．逐题评分合同：出图前冻结

每项要求有ID、完整判据、对象／部位、依据、适用条件、可见证据、容许变化及必需程度。不要把数条可独立成败的要求合成一条；也不要把同一知识拆成大量近重复项增加权重。

| 分组 | 要回答的问题 | 例子 |
|---|---|---|
| K：知识应用 | 哪些正确视觉要求需要从概念、规则或资料中得到？ | 国旗双三角外形；静止液面水平；指定物种的辨识结构；适用的文化饰边 |
| E：非知识执行 | 原题直接要求了什么，是否实现必要的展示与任务前提？ | 左边瓶子倾斜、完整展示断面、指定两只对象、不得添加文字 |
| P：编辑保持 | 哪些内容不能改，允许哪些连带变化？ | 保持另两只瓶子和桌面；允许倾斜引起局部遮挡与投影改变 |
| Q：画质与可用性 | 实际结果是否清晰、连贯，存在独立视觉／物理缺陷吗？ | 可读性、伪影、非知识特定的结构与物理可信性 |
| A：美感 | 在指定表现形式下构图与视觉表达如何？ | 构图、色彩、光影；不以照片标准要求科学示意图 |

K／E／P内每项主归类唯一；另记录bench200视觉轴、前提依赖和关联缺陷ID。知识导致的影子／关系变化可属于K，题面明确给出完整操作的要求可属于E；不能一律把所有影子、数量或动作放进执行。

**身份必须完整覆盖但不重复扣罚。** 先确认被编辑／描绘的是绑定目标（对象对应E/P），再以来源支持的辨识结构检查概念／子类型（K）。若同一条特征已是身份的充分证据，可引用该K构成身份验收，不再复制一个同义K。马来貘条斑通过不能掩盖已可辨的牛样蹄、头部等身份冲突；不可观察部位不凭空追加约束。

**所有题面验收义务都要有落点。** 冻结时逐句核对显式要求与隐含来源要求。主要结果所必需的义务标required；额外精度只作诊断。required／diagnostic及容许微差必须提前定，不能因输出差而降级。常识性质量检查不无限扩展K。

## 3．先检查材料与题目，再看模型输赢

三种审核对象分别记录，不混成一个标签：

- 题目本身：概念清楚、适用条件充分、要求可同时满足、原图目标可定位、核心考点能在合法画法中被验证。状态为valid／review_pending／invalid，附证据和版本。
- 给生成器的资料包：逐项标明支持／部分支持／不支持、图像区域与文字补充；资料错误或不足不自动使原题无效。仅图片消融可能不覆盖全部K，应预先说明，不能把这种失败说成模型不用图片。
- 给判官的评分证据：独立核验、身份和规则来源明确、有合法变体。所有模型／资料条件共享同一评分证据，不把它们各自收到的检索结果当答案。

validity在看到结果后发现新问题时可复审，但需基于题面／来源／原图作出统一决定。同一qid的模型、资料条件、重复样本成对处理；公开原冻结题数、无效题数和理由。不能通过删除难题／失败题提高分数。审核pending不能静默剔除。

## 4．逐项结果：用人能读懂的词

| 结果 | 内部值 | 含义 |
|---|---|---|
| 满足 | met | 正面可见证据证明在容许范围内成立 |
| 未满足 | unmet | 明确可见的错误、缺失或未完成要求；K可显示“知识要求画错”，E/P显示“未完成／未保持” |
| 未能验证 | unverified | 证据不足，不能确认满足或不满足；必须说明原因 |
| 不适用 | not_applicable | 预先规定的条件确实未触发；不能用目标消失来逃避必需检查 |

每行显示“判据全文｜结果｜位置／证据｜原因”，不要只给K1与JSON。轻微变化若在容许范围内即met，并备注；原子要求无需以2分表示“比正确更正确”。核心要求未能验证不作半分通过。

### 不可观察的原因与后果

| 原因 | K如何记 | 其他处理 |
|---|---|---|
| 输出遮挡／裁切／模糊，违反题面清楚展示要求 | unverified，reason=output_visibility_failure | 对应E展示项unmet；整题明确失败，保留分母；不推断被挡结构实际错误 |
| 原题／原图允许考点无法辨认 | unverified，reason=question_observability_gap | 题目复审；确认无效后所有对应条件统一排除，不归为模型知识错误 |
| 预览缩小或判官分辨不清 | unverified，reason=reviewer_uncertainty | 先看原始像素、相同显示尺度与局部，再复核；不得用生成式放大创造判分证据 |
| 模型未实现应用前提／目标对应失败 | unverified，reason=prerequisite_not_realized | 对应E/P失败；直立瓶子的水平液面不能通过“倾斜后仍水平”的条件应用项 |
| 部位可见且证据明确错误 | unmet，不用unverified | 记录错在哪里及来源；不能因细节小就忽略已明确可见反证 |

原因可以并存，按证据保留，不强迫单一内部因果诊断。若某必需K被合法画法变为not_applicable，触发题目复审；核心知识不能靠不画对象而免考。

## 5．任务结论与统计分母（草案默认）

每个输出有四个并列结果：K知识完成、E执行完成、P保持完成（t2i不适用）、J知识＋执行＋保持联合完成。

- 一个分组全部required项met：pass。
- 任一required项unmet：fail，其他未知照样保留。
- 无unmet但存在required项unverified：unresolved，不当通过，也不声称已证明画错。
- 无适用P的t2i直接按K＋E计算J，不给予额外保持分。
- 返回拒绝／空图／不可解码：记model_failure，任务未成功，计入分母；不声称每项知识都被画错。网络／调用设施故障单列infra_pending，按批次既定预算处理，不伪装模型失败、不静默删题。

主要展示：**已证实知识完成率K、已证实联合完成率J**；E/P通过率、知识项冲突率／未验证率、质量与美感分开。K与J都要展示，不能只选看起来最好的一个。不能将K与E/P相加做重复惩罚。

N是同批已确认有效、按协议应评测的输出数（单次时等于题数）。已证实通过P，未决U：报告P/N，以及未决U/N和逻辑区间[P/N, (P+U)/N]。该区间不是统计置信区间；fail和model_failure不进入U。只要有明确失败，联合J即可fail，不因另一个K未知把它提升为J未决。设施缺失未补齐时保留pending，不公布为完整正式结果。

跨模型／条件比较使用同一冻结题目集合，逐题报告未通过→通过、通过→未通过、两组通过、两组未通过，并把涉及unresolved的对比另列“未定”，不称退步或改善。计算百分点差时同时报告双方未决数。正式多重复采用预定每题重复数，按题平均再宏平均，不择优图。

逐项完成比例是诊断：先在每题required K内计算met比例，再按题宏平均，避免K多的题支配总分；unverified留在分母并另报，not_applicable仅限合法非核心诊断项。完整K通过率不受重复拆项改变，但证据准则仍需稳定。按领域、知识内容、应用层次、资料支持形态分层，小样本显示n；多标签分组不相加为总体题数。

## 6．与bench200的衔接

| 旧框架 | 新草案如何保留／调整 |
|---|---|
| T2I对齐十轴 | 每条K/E映射主体、结构、颜色、数量、空间、文字、动作、状态、环境、风格；这是视觉归属，与知识／执行来源归属正交 |
| 编辑按操作类型验收 | 复用对象对应、操作完成、必要后果、授权范围与保持规则；逐项落实为K/E/P，而非一个execution布尔值 |
| 来源／隐含知识 | 使用独立查证的题目证据，而非只依赖判官记忆；不得根据出题人未经核验的解释强加答案 |
| QIB刻度 | 原子K/E/P按满足与否判；质量／美感建议另用0不可用或明显不成立、1可用有局部偏差、2适用要求清楚成立，N/A与unverified单列。此2不要求“超常发挥”，是新刻度，不冒充原T2I Excel |
| 质量八项、美感四项 | 维持完整核验范围，按适用内容记录；不因知识没画对而自动将质量归零。固定分项版本后再聚合，不把旧1–5结果线性换算 |
| 编辑official钳制 | 新知识诊断不沿用对画质的自动钳制；旧官方分仅在独立兼容臂按原prompt重评、保留原汇总规则，不能从新逐项结果机械换算 |

Q默认检查物理逻辑、材质呈现、细节、伪影、有效分辨率、边缘、自然连贯、解剖；A检查构图、色彩、光影和情绪表达（不适用时不强判）。场景特定的液面法则已作为K检查，不再把同一错斜液面重复记为Q物理错误；独立的瓶体穿插可另判Q。关联缺陷ID保留证据归属。

Q/A为各自适用已判项均值并报告覆盖率，未知不硬填0或中档，覆盖不同不能直接拿均值比较。默认不与K/E/P混成一个总分，不将审美设为答对门槛。严重质量问题导致考点不可辨时，已有展示与可观察性规则处理；整图不可解码按model_failure。是否再设独立Q可用性门槛属于待讨论选择。

## 7．三个“怎么判”的例子（协议演示，不是旧图重评分）

| 假设可见结果 | 逐项判定 | 结论 |
|---|---|---|
| 尼泊尔旗形与徽记均对，但广场被换成草地 | required K全部met；P广场保持unmet | K通过，J失败；说明局部知识能实现，不代表编辑完成 |
| 瓶子明确已倾斜，静止液面随瓶倾斜 | E倾斜met；K静止液面规则unmet | 知识要求画错；不确定内部缺知识还是应用／绘制失败 |
| 瓶子仍直立，液面水平 | E倾斜unmet；条件K为unverified／前提未实现 | K未决，J失败；不能奖励未执行条件下的水平液面 |
| 明确要求断面清楚可见，但输出断面太窄不可辨 | K贯穿纹理unverified；E断面展示unmet | K未决，J失败；不能猜断面是对还是错 |
| 固定原图里的竖琴极小，题面未保证可辨七弦组 | K unverified；题目review_pending | 复审题目，不直接判模型知识错；确认缺口后统一修订版本 |
| 动物有目标条斑，但可辨的头部与足明显属于另一类 | 花纹K met；来源支持的身份／结构K unmet | K和J失败，防止局部特征替代概念身份 |

## 8．评审执行与验证步骤

1. 冻结rubric_version、题面／原图／评分来源哈希、required判据与例外、资料支持覆盖、允许编辑范围；审核不依赖模型输出选答案。
2. 判官只看任务、匿名输出、编辑原图（如有）及统一评分证据／判据；不提供模型身份、条件名、另一模型输出、旧分数和预期增益。图像内容可能透露条件，不能宣称绝对盲法。独立知识证据取代出题人自由推理，而非偷偷把旧出题reasoning作为权威。
3. 先查对象和可观察部位，再逐项记录可见证据；原图保持用固定锚点，不凭“重绘感”。对未知先复看原像素；助手审核明确不是人工金标准。
4. 脚本校验必需项齐全、状态及来源引用合法、not_applicable未免除核心、依赖前提成立后才允许应用K通过；聚合器确定性计算，不让判官根据总分倒推细项。
5. 在固定少量已有图上做独立补充审核：包含知识错、执行错、源图不可判、模型隐藏考点、身份错和成功图。所有模型／条件成对纳入；审核分歧逐项保留。通过此校准再冻结协议v1，不立即重评全部题。

## 9．需要用户审阅的三个默认选择

1. 主表同时给K与J，通过率采用“全部必需项满足”；不追求一个混合总分。
2. K/E/P原子项用满足／未满足／未验证／不适用，不设置“卓越正确”；Q/A单独用新0/1/2规则。
3. 未验证单列并给通过率区间；不自动当知识错误，也不删掉来提高通过率。题目确认无效才成对排除。

以上均为草案默认，可继续讨论。出题经验已经在DESIGN第18节；本草案不是给另一个出题会话新增阻塞条件，也没有改动其批次。
'''


def group_outcome(statuses, model_failure=False):
    """Illustrate strict required-item aggregation; no historical data is read."""
    if model_failure:
        return 'fail'
    if not statuses:
        raise ValueError('No required checks: use explicit task-specific applicability')
    if any(x not in {'met', 'unmet', 'unverified'} for x in statuses):
        raise ValueError('Required checks cannot be made not_applicable')
    if 'unmet' in statuses:
        return 'fail'
    return 'unresolved' if 'unverified' in statuses else 'pass'


def completion_bounds(outcomes):
    if not outcomes or any(x not in {'pass','fail','unresolved'} for x in outcomes):
        raise ValueError('Expected nonempty eligible outcome list')
    n=len(outcomes);p=outcomes.count('pass');u=outcomes.count('unresolved')
    return {'n':n,'confirmed_pass':p,'unresolved':u,'confirmed_rate':p/n,'logical_upper':(p+u)/n}


def main():
    # Small arithmetic checks for the proposal only, not model evaluations.
    assert group_outcome(['met','met'])=='pass'
    assert group_outcome(['unmet','unverified'])=='fail'
    assert group_outcome(['met','unverified'])=='unresolved'
    assert group_outcome([],model_failure=True)=='fail'
    try:group_outcome(['not_applicable'])
    except ValueError:pass
    else:raise AssertionError('Required item improperly exempted')
    assert completion_bounds(['pass','fail','unresolved'])['logical_upper']==2/3
    nb=nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell(TEXT)],metadata={'kernelspec':{'display_name':'demiwtg','language':'python','name':'demiwtg'}})
    nbformat.validate(nb)
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/'protocol_review_zh.ipynb'
    nbformat.write(nb,path)
    print(json.dumps({'notebook':str(path),'status':'draft_only_no_rescoring','aggregation_examples_checked':True},ensure_ascii=False))


if __name__=='__main__':main()
