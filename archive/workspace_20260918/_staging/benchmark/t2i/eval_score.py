"""t2i（生成）赛道判分器（参照 Qwen-Image-Bench 2605.28091 §3.3-3.4，2026-08-26 v4 修订）。

- 双线判分一次 judge 调用：知识线（本题 implicit_checks，逐条 {0,1,2}，含 rubric + acceptable_variants）
  + 通用线（本模块 FACETS 裁剪子集，按题面 facet_tags 激活，{0,1,2,NA}）；
- 非线性映射 phi：0->0、1->60、2->100（Pass 定在及格线，放大不合格/合格落差）；
- 单题总分 = 0.7*知识线 + 0.3*通用线；
- 知识熔断：知识线 < 40 时总分封顶 20；
- v5 critical 熔断：任一 critical=true 的 check 得 0 档时总分封顶 20；
- 主体缺失/主题跑偏（gate=true）时总分封顶 20；
- 通用线剔除 Real-world Fidelity 支柱（避免与知识线重复计分，RWF facet 作诊断标签进 facet_diagnostic）。

- v6.0 三维通用规则判分：判分契约全文内联在 judge_prompt_gen_v6.0_V2.md
  （代码零词表依赖），运行时整篇 USER 模板作 judge prompt（只填 gen_prompt）；
  输出 = 对齐十轴 + 质量八项 + 美感四项 + 三个 *_reasons，键集合校验（缺键/
  多项/越界 → 重判一次，仍坏该题记 fail）；φ 映射线分聚合（0→0/1→60/2→100，
  N/A 剔除），三线分报告不出总分。旧批（v5.2-v5.7）路径零改动。
- baseline 子命令：判官能力基线探针（计数 / 中文 OCR / 镜像一致性，
  PIL 构造确定性样本，判分启动硬前提）。

responses 契约（每题一行）：{"qid": "...", "image": "产出图路径"}

用法：
    python3 benchmark/t2i/eval_score.py score --questions Q.jsonl --responses R.jsonl
    python3 benchmark/t2i/eval_score.py score --v60 V2 --endpoint http://127.0.0.1:4001/v1/chat/completions \
        --model openrouter/openai/gpt-5.6-sol --questions Q.jsonl --responses R.jsonl
    python3 benchmark/t2i/eval_score.py baseline --endpoint http://127.0.0.1:4001/v1/chat/completions \
        --model openrouter/openai/gpt-5.6-sol
    python3 benchmark/t2i/eval_score.py dump   # 物化 judge prompt 到 data/judge_prompts（审计用）
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

SUB_DIR = Path(__file__).resolve().parent                    # t2i/
EVAL_DIR = SUB_DIR / "data"
JUDGE_PROMPTS_DIR = EVAL_DIR / "judge_prompts"

DEFAULT_ENDPOINT = "http://localhost:8000/v1/chat/completions"
DEFAULT_MODEL = "qwen3.8-27b"
# judge 是「先写图像分析、再输出 JSON」的描述型输出，max_tokens 不足会在 JSON
# 出现前被截断（曾致 9/30 判分失败）。预算 = 上下文上限 - 文本 prompt - 图像预留，
# 逐题动态计算；不改变判分口径（temp=0/seed=42 确定性解码，加长只是补全续写）。
JUDGE_CTX_LEN = 32768       # 与 vLLM 部署 --max-model-len 保持一致（2026-08-26 自 8192 提升：
                            # 8192 时 17/30 题 thinking 烧光预算、JSON 未出即截断判 0 分）
JUDGE_IMG_RESERVE = 600     # 512×512 图约 ~350 vision token，留余量
JUDGE_IMG_RESERVE_HI = 2200  # 上下文超限(400)回退时的保守图像预留
JUDGE_BUDGET_MARGIN = 64
PHI = {0: 0.0, 1: 60.0, 2: 100.0}       # QIB 式非线性映射（1=及格线 60）
GATE_CAP = 20.0                          # 主体缺失/主题跑偏总分封顶（v4 收紧自 30；仅旧 v4/v5 流程使用）
KNOWLEDGE_WEIGHT = 0.7                   # 知识线 : 通用线 = 0.7 : 0.3（v4 调整自 0.5）

# ---------------------------------------------------------------------------
# 通用线 facet 表（权威源在此，出题 prompt 的 facet_tags 词表与它一致；
# eval_synthesize 导入本表做审计）。以 Qwen-Image-Bench（2605.28091）Tab.7
# 的 L1 支柱→L2 子能力→L3 细则三级结构为基础裁剪并扩充（41 项）：
#   - 剔除 Creative Generation 支柱（用户拍板）；其 Logical Resolution 细则以
#     causal_reasoning 名义升格入 Real-world Fidelity/知识推理；
#   - 剔除 Fairness 与 Safety & Compliance（论文自报各模型均匀贴 60 分，
#     对知识探针题无区分度）；
#   - Real-world Fidelity 重点扩充：World Knowledge 新增地标/角色/自然/器械 4 项，
#     并新增知识推理子能力（因果/关系/反事实）3 项；
#   - Alignment 新增 Subject 子能力 1 项（subject_prominence 主体性：QIB 未覆盖的
#     「主体在场但不主导」灰区，与 gate 的缺失/跑偏判定互补）。
#
# v4 判分侧变化（2026-08-26）：Real-world Fidelity 支柱的 facet 作诊断标签
# 进 facet_diagnostic，不进入通用线均值，避免与知识线重复计分（gpt5.6
# 结构性证明）。
# ---------------------------------------------------------------------------
FACETS = [
    # key, 所属 L1 支柱, L2 子能力, 判分准则（中文化自 QIB Tab.7）
    # —— Quality ——
    ("physical_logic", "Quality", "Realism",
     "画面是否存在可见的物理矛盾？无支撑而悬浮的物体、与画面唯一光源方向相反的投影、违背镜面或水面规律的倒影、结构上无法自立的堆叠或姿势，均属本项缺陷。判档（QIB 刻度）：0 Fail＝出现明显且多处物理矛盾；1 Pass＝整体物理关系自洽（合格基线，不因「只是达标」降档）；轻微瑕疵（仅个别轻微不一致（影子角度略偏、反射细节简化））不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。慢门拖影、景深虚化等摄影语言不算物理矛盾。"),
    ("material_texture", "Quality", "Realism",
     "物体表面材质是否呈现符合场景要求的真实质感？以画面题材应呈现的材质为准：石雕判石材的颗粒与亚光，织物判纤维与垂坠，金属判反光与硬度，食物判湿润或酥脆的表面状态；不要求题材之外的材质（如雕塑不判皮肤毛孔）。判档（QIB 刻度）：0 Fail＝材质呈现整体失真、被画成另一种材质；1 Pass＝材质属性准确且细节可信（合格基线，不因「只是达标」降档）；轻微瑕疵（材质大类正确但质感细节缺乏或部分区域失真）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("detail_richness", "Quality", "Detail",
     "画面细节是否充分？判分对象是细节密度与信息量：画面应呈现题材应有的细节层次（物体结构细节、表面纹理、场景元素）。判档（QIB 刻度）：0 Fail＝大面积细节缺失、表面被过度简化为色块；1 Pass＝细节充分且层次分明（合格基线，不因「只是达标」降档）；轻微瑕疵（细节局部缺失或简化但不影响主体判读）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。风格化扁平插画按其风格规制判，不按写实苛求。"),
    ("artifacts", "Quality", "Detail",
     "画面是否干净无伪影？判分对象是生成噪声与压缩痕迹：噪点、颗粒、色带、块状伪影、涂抹感。判档（QIB 刻度）：0 Fail＝出现大面积明显伪影；1 Pass＝画面干净（合格基线，不因「只是达标」降档）；轻微瑕疵（伪影局部存在、不影响主体判读）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。胶片颗粒等题材刻意要求的质感不算伪影。"),
    ("edge_clarity", "Quality", "Detail",
     "物体轮廓与边缘是否清晰？判分对象是非意图性的模糊与锯齿：主体轮廓应清晰可辨、边缘过渡干净。意图性的摄影语言（浅景深虚化、运动模糊、氛围雾气）不算缺陷。判档（QIB 刻度）：0 Fail＝主体轮廓明显发虚、边缘锯齿严重或糊成一片无法判读；1 Pass＝边缘整体清晰锐利（合格基线，不因「只是达标」降档）；轻微瑕疵（次要物体边缘模糊但主体清晰）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("naturalness", "Quality", "Detail",
     "画面是否自然、无 AI 生成典型的塑料感或油腻感？判分锚点：人物皮肤呈蜡质、无次表面散射、高光油腻，或物体表面过度光滑、反光方式不真实，均属本项缺陷。判档（QIB 刻度）：0 Fail＝整体大面积塑料感或油腻感显著；1 Pass＝质感自然（合格基线，不因「只是达标」降档）；轻微瑕疵（局部（如面部、手部）出现但不严重）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。本项只判质感真实度，不判美观程度。"),
    # —— Aesthetics ——
    ("composition", "Aesthetics", "Composition",
     "画面构图是否成立？本项判可指认的构图问题：主体与视觉焦点是否明确、画面是否严重失衡、元素安排是否混乱。判档（QIB 刻度）：0 Fail＝主体被裁切出画、地平线穿过头部等结构性错误或画面严重失衡无焦点；1 Pass＝主体突出、布局平衡、视觉引导清晰（合格基线，不因「只是达标」降档）；轻微瑕疵（构图平淡、视觉引导弱但无结构性错误）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。本项不判个人审美偏好，只判构图缺陷与成立度。"),
    ("color_harmony", "Aesthetics", "Color Harmony",
     "画面色彩是否协调无缺陷？本项判可指认的色彩缺陷：色彩断层、突兀的局部色斑、非题材要求的白平衡失真、相邻元素色彩冲突。判档（QIB 刻度）：0 Fail＝出现明显色彩缺陷；1 Pass＝色彩过渡自然、整体协调（合格基线，不因「只是达标」降档）；轻微瑕疵（缺陷局部存在、不影响画面整体）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。题材刻意要求的风格化配色（霓虹、复古色调等）不算缺陷。"),
    ("lighting_atmosphere", "Aesthetics", "Lighting",
     "画面光影氛围是否成立？本项判两层：一是光源逻辑是否自洽（明暗方向一致、存在合理光源、无互相矛盾的多重投影）；二是光影氛围是否与题面设定（时间、天气、光照条件）相符。判档（QIB 刻度）：0 Fail＝光源逻辑混乱、或与题面设定的光照条件明显相悖；1 Pass＝光源自洽且准确呈现题面设定的氛围（合格基线，不因「只是达标」降档）；轻微瑕疵（光源基本自洽但氛围与题面设定的贴合较弱）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("anatomical_fidelity", "Quality", "Realism",
     "人物或动物形体是否符合解剖学？本项判五官比例、骨骼结构、肢体数量与关节朝向的正确性：多余或缺失的肢体或手指、关节反折、五官比例严重失调，均属缺陷；皮肤微观质感（毛孔、细纹）仅作辅助证据，不单独决定档位。判档（QIB 刻度）：0 Fail＝形体存在明显解剖错误；1 Pass＝解剖结构正确（合格基线，不因「只是达标」降档）；轻微瑕疵（比例或结构可疑但不显著、或个别细节（手指数量等）出错）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。雕塑、绘画等风格下的形体按其艺术规制判，不按照片写实苛求。"),
    ("style_control", "Alignment", "Style",
     "画面是否准确呈现题面指定的艺术风格？判风格体系是否正确落实：题面点名的水墨、油画、赛博朋克等风格，其标志性语言（笔触、配色逻辑、构图习惯、材质处理）是否被一致执行。判档（QIB 刻度）：0 Fail＝画成完全不同的风格体系；1 Pass＝风格语言准确且全画一致（合格基线，不因「只是达标」降档）；轻微瑕疵（风格要素混杂、执行不稳定）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。题面未指定风格时本项记 N/A。"),
    # —— Alignment ——
    ("subject_prominence", "Alignment", "Subject",
     "题面点名的主体是否占据画面主导地位？以体量占比、画面位置与视觉焦点综合判断。判档（QIB 刻度）：0 Fail＝主体完全缺失、或画面被另一无关事物占据；1 Pass＝主体明确占据视觉主导（合格基线，不因「只是达标」降档）；轻微瑕疵（主体在场但被边缘化（占比过小、位置偏离、背景喧宾夺主））不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。主体被画成其他事物的情况由保真检查判，本项只判在场主体是否主导。"),
    ("quantity", "Alignment", "Attributes",
     "画面中指定物体的数量是否与题面一致？逐一点数后判分。判档（QIB 刻度）：0 Fail＝数量与题面明显不符（指定八张画成六张、指定两颗画成四颗）；1 Pass＝可数物体与题面指定完全一致（合格基线，不因「只是达标」降档）；轻微瑕疵（总数正确但个别物体的归属或边界模糊、或物体总数超过十个时相差一件）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。题面未指定数量的物体不纳入本项。"),
    ("facial_expression", "Alignment", "Attributes",
     "人物或动物的面部表情是否与题面指定的情绪一致？以五官状态（眉、眼、嘴、耳）的组合判读。判档（QIB 刻度）：0 Fail＝表情与指定情绪相反（要求痛苦而画面微笑）或面部完全无表情层次；1 Pass＝表情准确传达指定情绪（合格基线，不因「只是达标」降档）；轻微瑕疵（情绪方向正确但表现力度不足或五官细节含糊）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。面部过小过远无法判读时，按该项可见条件处置。"),
    ("material_properties", "Alignment", "Attributes",
     "物体的材质是否与题面的材质描述一致？题面点名材质（木纹、陶瓷、金属、绢布等）时判其是否落实。判档（QIB 刻度）：0 Fail＝材质完全不符（题面说大理石而画面呈塑料感）；1 Pass＝材质与题面描述一致（合格基线，不因「只是达标」降档）；轻微瑕疵（材质大类正确但呈现强度或细分类型有偏差）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。材质呈现得真实与否由 Quality 组 material_texture 判，本项只判与题面指定的一致性。"),
    ("color", "Alignment", "Attributes",
     "物体颜色是否与题面的颜色指定一致？以题面点名的主色判。判档（QIB 刻度）：0 Fail＝主体颜色与指定属不同色系；1 Pass＝颜色与指定一致（允许受光与环境色的合理影响）（合格基线，不因「只是达标」降档）；轻微瑕疵（色系正确但色调、明度或饱和度有明显偏差）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。题面未指定颜色的物体不纳入本项。"),
    ("shape", "Alignment", "Attributes",
     "物体的形状是否与题面的形状描述一致？题面点名形状特征（竖向圆角矩形、卷状、额前独角状突起等）时判其是否落实。判档（QIB 刻度）：0 Fail＝形状类别错误（指定竖卡画成横卡、指定卷状画成块状）；1 Pass＝形状与题面描述一致（合格基线，不因「只是达标」降档）；轻微瑕疵（形状大类正确但比例或细节特征缺失）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("size", "Alignment", "Attributes",
     "物体尺寸是否与题面的规格指定一致？题面给出绝对规格（巨型、迷你）时，以画面内其他常见物体的相对比例判断；题面给出相对规格（比某物大或小）时直接按相对关系判断。判档（QIB 刻度）：0 Fail＝尺寸关系明显颠倒；1 Pass＝符合题面规格（合格基线，不因「只是达标」降档）；轻微瑕疵（尺寸方向正确但程度不足）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。题面未涉及尺寸时不选本项。"),
    ("contact_interaction", "Alignment", "Actions",
     "题面涉及的主体间物理接触是否按描述发生且方式一致？判两层：接触是否发生（题面说握手而未接触即未执行）、接触部位与方式是否与描述相符。判档（QIB 刻度）：0 Fail＝完全未发生接触、或接触方式与题面描述明显不同；1 Pass＝接触按题面描述准确发生（合格基线，不因「只是达标」降档）；轻微瑕疵（接触发生但部位或方式部分不符）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。接触画得自然与否（穿模、悬空的手）由 Quality 组 physical_logic 判，本项只判与题面的一致性。"),
    ("noncontact_interaction", "Alignment", "Actions",
     "题面指定的主体间非接触关系是否按描述成立？对视、跟随、环绕、回避等空间或社会关系。判档（QIB 刻度）：0 Fail＝关系完全未呈现（题面要求对视而两主体无互动方向）；1 Pass＝关系按题面描述清晰成立（合格基线，不因「只是达标」降档）；轻微瑕疵（关系方向正确但表现不明确、需要猜测）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。关系本身合不合常理由知识相关检查判，本项只判与题面指定的一致性。"),
    ("fullbody_action", "Alignment", "Actions",
     "主体的整体姿态与肢体动作是否执行了题面描述的活动？以躯干朝向、四肢分工、重心位置综合判断。判档（QIB 刻度）：0 Fail＝动作完全未执行（题面要求腾空而主体静止站立）；1 Pass＝动作按题面描述准确执行（合格基线，不因「只是达标」降档）；轻微瑕疵（动作方向正确但幅度或阶段与描述有偏差）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。肢体结构是否正确由解剖相关项判，本项只判动作的执行。"),
    ("spatial_2d", "Alignment", "Layout",
     "物体在二维画面上的相对位置是否符合题面的空间指令？题面使用左右、上下、前景背景等平面方位词时逐项核对。判档（QIB 刻度）：0 Fail＝方位关系明显颠倒（题面要求风从左向右而画面呈相反方向）；1 Pass＝平面位置关系与题面指令一致（合格基线，不因「只是达标」降档）；轻微瑕疵（方位大体正确但个别物体的层次归属模糊）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("spatial_3d", "Alignment", "Layout",
     "物体在三维空间中的布局、遮挡与相对位置是否符合题面描述？判深度方向的关系：前后遮挡、远近层次、物体间的立体相对位置。判档（QIB 刻度）：0 Fail＝三维关系与题面明显不符（遮挡颠倒、远近关系错误）；1 Pass＝三维布局与题面一致（合格基线，不因「只是达标」降档）；轻微瑕疵（立体关系基本正确但个别遮挡或层次表达不清）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("composition_relationship", "Alignment", "Relations",
     "多个元素是否按题面指定的组织方式整合为整体？题面指定排列、分组、朝向关系（两行四列、扇形展开、围成一圈等）时逐项核对。判档（QIB 刻度）：0 Fail＝组织方式与题面明显不符；1 Pass＝组织关系与题面指定一致（合格基线，不因「只是达标」降档）；轻微瑕疵（组织大致成型但行列或分组不严格、有出入）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。画面整体美观与否由 Aesthetics 组判，本项只判组织方式与题面的一致性。"),
    ("difference_similarity", "Alignment", "Relations",
     "题面指定的物体间差异或相似是否被准确表现？题面点名对比关系（一高一矮、颜色不同、材质相近等）时逐项核对。判档（QIB 刻度）：0 Fail＝对比关系缺失或颠倒；1 Pass＝差异或相似按题面准确呈现（合格基线，不因「只是达标」降档）；轻微瑕疵（对比存在但程度与题面描述有偏差）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("containment", "Alignment", "Relations",
     "题面指定的包含或围合关系是否被正确描绘？盘中盛放、盒内装着、手中握持、框架围合等。判档（QIB 刻度）：0 Fail＝关系完全未呈现（题面说盘装而物体直接置于桌面）；1 Pass＝包含或围合关系明确正确（合格基线，不因「只是达标」降档）；轻微瑕疵（关系呈现但边界不清（物体与容器穿插、悬浮））不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("real_world_scene", "Alignment", "Scene",
     "场景类型与环境设定是否与题面描述的地点一致？题面点名博物馆、庙会、祠堂、外太空等场景时，判画面是否呈现该场景的标志性要素。判档（QIB 刻度）：0 Fail＝场景与题面完全不符（要求庙会摊位而呈纯色摄影棚背景）；1 Pass＝场景准确呈现题面设定（合格基线，不因「只是达标」降档）；轻微瑕疵（场景可辨但标志性要素不足、地域或时代特征含糊）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。"),
    ("virtual_scene", "Alignment", "Scene",
     "虚构或反事实场景是否内部自洽？题面设定了现实中不存在的场景（奇幻世界，或真实实体置于反事实前提，如空间站出现在火星附近）时，判场景自身一致性。判档（QIB 刻度）：0 Fail＝前提要素之间相互矛盾（火星上出现地球云层与蓝天）；1 Pass＝虚构前提及其衍生视觉细节整体自洽（合格基线，不因「只是达标」降档）；轻微瑕疵（前提成立但衍生细节局部不一致）不明显降低画面质量时记 1、明显降低时记 0；2 Excel＝不仅达到 Pass，且该维度执行卓越、可指认具体过人之处，拿不准给 1。反事实按题面前提判分，不因前提本身非真实而降档。"),
]
FACET_KEYS = [f[0] for f in FACETS]

# ---------------------------------------------------------------------------
# v5.3 四维判分（真实保真度 0.5 / 文本一致性 0.2 / 质量 0.2 / 美感 0.1）
# 口径定稿（项目记忆 t2i_v53_dim_weights_critical）：
#   - 保真检查 5~7 条（v5.4 起恰好 7 条），锚定出题侧注入的「已核硬约束清单」；三档 rubric 闭合；
#   - 关键项封顶为判分侧机械规则（出题端零标注）：
#       负向约束检查（锚点引用 must_not_have 约束）得 0 档 → 总分封顶 20；
#       文本一致性检查得 0 档 → 总分封顶 20；
#       质量（缺陷类）判分项得 0 档 → 总分封顶 20；
#       正向保真不设关键项，保真整线 <40 熔断封顶 20；美感永不封顶。
#   - φ 映射与 v5.2 一致：0->0 / 1->60 / 2->100，N/A 剔除后均值。
# ---------------------------------------------------------------------------
V53_WEIGHTS = {"fidelity": 0.5, "alignment": 0.2, "quality": 0.2, "aesthetics": 0.1}
V53_LINE_FUSE_THRESHOLD = 40.0    # 保真整线熔断阈值（< 阈值封顶 20）
# v5.7（2026-08-30 拍板）：保真乘子去地板。total = G × (FLOOR+(1-FLOOR)×K/100)，
# FLOOR=0 ⇒ 纯乘子：知识画错多少，通用分如实折算多少；历史批 0.2 地板不再沿用
# （已落盘分数不受影响，重判旧批将按纯乘子出分）。
FIDELITY_FLOOR = 0.0

T2I_SYSTEM_V53 = ("你是文生图评测判官。给定题面、判分清单与生成图，你逐项判分，"
                  "严格输出 JSON，不输出其他内容。判每一项前，先在生成图中"
                  "定位图中可见的具体证据，再据证据落档；题面与检查文本的"
                  "描述不构成证据，不得把「检查这么写了」当作「画面做到了」"
                  "的依据；档位拿不准时向下降档。")

T2I_USER_TPL_V53 = """# 题面（发给被评测模型的指令）
{gen_prompt}

# 生成图
<image>

# 判分总则（四个维度全部对齐 Qwen-Image-Bench 刻度）
1. 三档 + 不适用（QIB 刻度）：
   - 0 ＝ Fail：存在可明确指认的缺陷——画错、缺失、违反题面规定或准则所述毛病可见且明显降低画面质量；
   - 1 ＝ Pass：合格——缺陷不存在、要求被做到。这是合格品的预期档，不因「只是达标」降档；
   - 2 ＝ Excel：卓越——不仅合格，且该维度执行显著超常规；reason 必须写出可指认的过人之处（细节精度、处理难度、完成度等具体证据）；拿不准 1 与 2 之间时给 1；
   - "N/A"：不适用（按该项可见条件/准则处置）。
2. 各节 rubric/准则中「0 分档 / 及格档 / 满分档」措辞按本刻度映射：0 分档情形＝Fail(0)；满分档情形＝要求被做到＝Pass(1)；及格档描述的「部分做到」＝知识/要求在场但有欠缺——欠缺构成明显缺陷记 0，仅为轻微瑕疵记 1；卓越执行才记 2。
3. 严格按 rubric/准则给分，不凭整体印象；reason 必须引用图中可见的具体内容作为证据，不得用画面外知识脑补。
4. 因遮挡/出画/构图原因看不到的内容，按该项「可见条件」写明的处置，不得强判。
5. 画面失真/不够写实不是直接判死理由：该项考察的内容在场且画对时按 rubric 落档；只有该内容完全缺失或画错才落 0。

# 一、世界知识线（{nf} 条；考世界知识，逐条判分）
{fidelity_block}

上面 {nf} 条逐条判完之后，再做一轮整体兜底：凭你自己的世界知识审视生成图，找出上述检查**未覆盖**的、关于该概念的显著世界知识错误（概念层面的事实或原理被画错；人有两只手、光影物理自洽这类普适成图常识归质量节判，不在这里找）。找到则逐条列出「错误点」与「应然事实」；没有就输出空数组。兜底结果不计入上面各条的分数。

# 二、文本一致性（{na} 项全判；考题面显式规定是否照做，与世界知识无关；题面未含该类显式规定的项记 N/A 剔除）
{alignment_block}

# 三、质量（{nq} 项全判；考画面硬毛病，按准则与 QIB 刻度判分；不适用按各项条件记 N/A）
{quality_block}

# 四、美感（{ns} 项全判；考画面审美，按准则与 QIB 刻度判分；不适用按各项条件记 N/A）
{aesthetic_block}

# 输出格式（只输出一个合法 JSON 对象，不要 markdown 围栏，不得缺项或多项）
{{"fidelity": [{{"index": 1, "score": 2, "reason": "图中可见证据"}}], "alignment": {{"{a_example}": 1, "{a_example2}": "N/A"}}, "quality": {{"{q_example}": 2}}, "aesthetics": {{"{aes_example}": 1}}, "alignment_reasons": {{"{a_example}": "判该项的图中可见证据（一句话）、N/A 项写明判 N/A 的依据"}}, "quality_reasons": {{"{q_example}": "同上"}}, "aesthetics_reasons": {{"{aes_example}": "同上"}}, "knowledge_fallback": [{{"issue": "错误点", "should_be": "应然事实"}}]}}
score 取值 {{0, 1, 2, "N/A"}}；fidelity 按 index 从 1 起与上面编号一一对应；alignment/quality/aesthetics 按判分项名输出（项名须与上面各节列出的完全一致）；三个 *_reasons 对象对每个判分项各给一条一句话证据（引用图中可见内容，不脑补）；knowledge_fallback 没有发现时输出空数组。"""


# ---------------------------------------------------------------------------
# v5.7 线索制：知识线索 = 难度筛选后的坐标，判官按镜头维度整体判 0/60/100
# ---------------------------------------------------------------------------
LENS_TITLES = {"K1": "形态与构成", "K2": "表面与图样", "K3": "符号与文字",
               "K4": "数量与尺度", "K5": "状态与规律"}
LENS_FOCUS = {
    "K1": "部件构成、组装配置、解剖形态、属于哪一类",
    "K2": "颜色、图案、纹饰、装饰、材质样式",
    "K3": "字样、铭文、徽标与标志的字形、编号——先逐字抄录，再比对",
    "K4": "固有数量、比例、尺度关系",
    "K5": "固有运行规律、情境应用的物理/自然规律、所处状态",
}

T2I_USER_TPL_V57 = """# 题面（发给被评测模型的指令）
{gen_prompt}

# 生成图
<image>

# 判分总则
1. 世界知识维度用 **0 / 60 / 100** 三档刻度（对齐 QIB）；文本一致性/质量/美感用 {{0 Fail / 1 Pass / 2 Excel / N/A}} 四档（见各节）。
2. 严格依据图中可见证据判分；题面与知识线索的描述本身不构成证据，不得用画面外知识脑补。
3. 因遮挡/出画/构图看不到的内容，按该线索写明的可见条件处置，不得强判。

# 一、世界知识维度（{nd} 个镜头维度 · {nf} 条知识线索）
知识线索指出该概念在这张图里的知识难点。以线索为坐标审视生成图，对下面**每个镜头维度**各落一档——**0 ＝ Fail / 60 ＝ Pass / 100 ＝ Excel**：
- 0 ＝ Fail：该维度线索指向的知识事实在图中被明显画错或违反；
- 60 ＝ Pass：该维度线索指向的知识事实成立。这是合格品的预期档，不因「只是做到」降档；
- 100 ＝ Excel：不仅成立，且该维度的知识执行显著超常规，必须可指认具体过人之处（细节精度/完整度/刻画难度）；拿不准 60 与 100 之间时给 60。
线索未列出但落在某个镜头维度内的世界知识错误，同样作为该维度的降档依据，并记入 knowledge_fallback（错误点+应然事实）；没有则输出空数组。

{lens_block}

# 二、文本一致性（{na} 项全判；考题面显式规定是否照做，与世界知识无关；题面未含该类显式规定的项记 N/A 剔除）
{alignment_block}

# 三、质量（{nq} 项全判；考画面硬毛病，按准则与 QIB 刻度判分；不适用按各项条件记 N/A）
{quality_block}

# 四、美感（{ns} 项全判；考画面审美，按准则与 QIB 刻度判分；不适用按各项条件记 N/A）
{aesthetic_block}

# 输出格式（只输出一个合法 JSON 对象，不要 markdown 围栏，不得缺项或多项）
{{"knowledge": {{"K1": 60, "K3": 0}}, "knowledge_reasons": {{"K1": "图中可见证据"}}, "alignment": {{"{a_example}": 1, "{a_example2}": "N/A"}}, "quality": {{"{q_example}": 2}}, "aesthetics": {{"{aes_example}": 1}}, "alignment_reasons": {{"{a_example}": "判该项的图中可见证据（一句话）、N/A 项写明判 N/A 的依据"}}, "quality_reasons": {{"{q_example}": "同上"}}, "aesthetics_reasons": {{"{aes_example}": "同上"}}, "knowledge_fallback": [{{"issue": "错误点", "should_be": "应然事实"}}]}}
knowledge 的键为上面实际判分的镜头维度（只输出这些，不多不少），取值 {{0, 60, 100}}；knowledge_reasons 给出每维的图中证据；alignment/quality/aesthetics 按判分项名输出，取值 {{0, 1, 2, "N/A"}}；三个 *_reasons 对象对每个判分项各给一条一句话证据（引用图中可见内容，不脑补）。"""


def _is_v57(q: dict) -> bool:
    """v5.7 线索制探测：检查带镜头字段且不带三档 rubric。"""
    fc = q.get("fidelity_checks") or []
    return (bool(fc)
            and any(str(c.get("lens") or "").strip() for c in fc)
            and all("rubric" not in c for c in fc))


def build_v57_user_prompt(q: dict) -> str:
    """v5.7：题目 → judge user prompt（镜头维度分组的线索 + 通用三支柱）。"""
    criteria = {k: c for k, _, _, c in FACETS}
    fc = q.get("fidelity_checks") or []
    by_lens: dict[str, list] = {}
    for c in fc:
        lk = str(c.get("lens") or "?").strip()
        if lk in LENS_TITLES:
            by_lens.setdefault(lk, []).append(c)
    sections = []
    for lk in ("K1", "K2", "K3", "K4", "K5"):
        if lk not in by_lens:
            continue
        lines = []
        for c in by_lens[lk]:
            vis = c.get("visibility_requirement") or "必须可见；不可见按线索描述处置"
            lines.append(f"- {c.get('check', '')}\n  可见条件: {vis}")
        sections.append(f"## {lk} {LENS_TITLES[lk]}（判什么：{LENS_FOCUS[lk]}）\n"
                        + "\n".join(lines))
    alf = [t for t in GENERIC_ALL["Alignment"] if t in FACET_KEYS]
    a_lines = []
    for t in alf:
        a_lines.append(f"- {t}: {criteria[t]}\n  QIB: {QIB_EN.get(t, '')}"
                       f"\n  不适用条件: 题面未含该类显式规定（如数量/颜色/姿态/位置/材质/场景/风格）时记 N/A，不得替题面添加未说的规定")
    qf = GENERIC_ALL["Quality"]
    af = GENERIC_ALL["Aesthetics"]
    qblock = "\n".join(f"- {t}: {criteria[t]}\n  QIB: {QIB_EN[t]}\n  不适用条件: {ACTIVATE[t]}（不适用记 N/A）"
                       for t in qf)
    ablock = "\n".join(f"- {t}: {criteria[t]}\n  QIB: {QIB_EN[t]}\n  不适用条件: {ACTIVATE[t]}（不适用记 N/A）"
                       for t in af)
    return T2I_USER_TPL_V57.format(
        gen_prompt=q.get("gen_prompt", ""),
        nd=len(by_lens), nf=len(fc),
        lens_block="\n\n".join(sections) or "（无线索）",
        na=len(alf), nq=len(qf), ns=len(af),
        alignment_block="\n".join(a_lines) or "（无）",
        quality_block=qblock or "（本题未激活）",
        aesthetic_block=ablock or "（本题未激活）",
        a_example=alf[0] if alf else "quantity",
        a_example2=alf[1] if len(alf) > 1 else "color",
        q_example=qf[0] if qf else "physical_logic",
        aes_example=af[0] if af else "composition",
    )


def finalize_v57(q: dict, parsed: dict, raw: str) -> dict:
    """v5.7：镜头维度分（0/60/100）→ K 线均值；通用三支柱与 v5.3 同。"""
    row = finalize_v53(q, parsed, raw, neg_idx=set(), def_idx=set())
    kn_map = {0: 0.0, "0": 0.0, 1: 60.0, "1": 60.0, 60: 60.0, "60": 60.0,
              2: 100.0, "2": 100.0, 100: 100.0, "100": 100.0}
    dims = {}
    for k, v in (parsed.get("knowledge") or {}).items():
        if str(k) in LENS_TITLES and v in kn_map:
            dims[str(k)] = kn_map[v]
    k = sum(dims.values()) / len(dims) if dims else None
    g = row["generic_score"]
    row.update({
        "schema": "v5.7",
        "fidelity_score": None if k is None else round(k, 2),
        "knowledge_dims": dims,
        "knowledge_reasons": parsed.get("knowledge_reasons") or {},
        "fidelity_scores": None,
        "fidelity_reasons": {},
        "neg_critical_idx": None,
        "neg_critical_violated": False,
        "fidelity_low": k is not None and k < V53_LINE_FUSE_THRESHOLD,
    })
    row["total"] = (round(g * (FIDELITY_FLOOR + (1.0 - FIDELITY_FLOOR) * k / 100.0), 2)
                    if k is not None else g)
    return row


# v5.4.4 全量判分：质量/美感判分项不再由出题人激活（出题时无法预判画面会
# 出现什么——生成侧意外如静物题画出人物，作者端激活必然漏判）。判官对全部
# 10 项判分，适用性按 ACTIVATE 条件在判分时定 N/A（条件来自 v5.4 附录激活
# 条件列，判官拿着图判定）。
# v5.5.3 对齐组出题端废除（2026-08-29 用户拍板，v5.5.2 的「检查附挂」中间
# 层一并取消）：判官基于题面×生成图直判 Alignment 组 18 项全量，题面未含
# 该类显式规定的项记 N/A 剔除出均值（与质量/美感同制，即 QIB 机制）。出题人
# 不再产出 alignment_checks——保真检查保留个性化产出（其携带题面外长尾知
# 识，判官无从自推）；对齐/质量/美感均为通用规则，出题端零翻译。
# v5.4.5 双语准则：QIB Tab.7 英文原文作语义锚（判官术语与源头逐字对齐），
# 中文富准则仍是操作主文。subject_prominence 为本基准新增（QIB 未覆盖）。
QIB_EN = {
    "physical_logic": "Does the image adhere to real-world physical laws (e.g., gravity, reflection, shadow direction, object stability)?",
    "material_texture": "Do the surface materials of objects (such as skin, fabric, metal, wood) exhibit realistic texture and material properties?",
    "detail_richness": "Is the image rich in detail without excessive noise or unnatural smoothing?",
    "artifacts": "Is the image rich in detail without excessive noise or unnatural smoothing?",
    "edge_clarity": "Are the outlines and edges of objects sharp, well-defined, and free from blurring or aliasing?",
    "naturalness": "Does the image appear natural and free from the artificial \"plastic\" or \"greasy\" look commonly associated with AI-generated images?",
    "anatomical_fidelity": "Are the facial feature proportions, skeletal structure, and limb articulation anatomically correct and consistent with human biology? Does the facial skin exhibit realistic micro-level textures such as pores and fine lines?",
    "composition": "Is the composition of the image balanced, visually guided, and aesthetically pleasing?",
    "color_harmony": "Is the overall color palette harmonious, cohesive, and appropriate for the mood of the image?",
    "lighting_atmosphere": "Does the lighting and shadow atmosphere of the image (such as contrast between light and dark, and the overall lighting atmosphere) match the scene setting of the prompt?",
    "style_control": "Does the image accurately capture and represent the specific artistic style requested in the prompt (e.g., Van Gogh's brushwork, Cyberpunk aesthetic)?",
    "subject_prominence": "(本基准新增，QIB 未覆盖：主体在场但不主导的灰区)",
    "quantity": "Does the number of objects in the image match the quantity specified in the prompt?",
    "facial_expression": "Does the facial expression of the person or animal accurately reflect the emotional state specified in the prompt?",
    "material_properties": "Do the materials of objects in the image match the material descriptions in the prompt?",
    "color": "Do the colors of objects in the image match the color specifications in the prompt?",
    "shape": "Do the shapes of objects in the image match the shape descriptions in the prompt?",
    "size": "Do the sizes of objects in the image match the size specifications in the prompt?",
    "contact_interaction": "If the prompt involves physical contact between subjects, is the contact interaction depicted naturally and realistically?",
    "noncontact_interaction": "If the prompt involves non-contact relationships between subjects, is the spatial and social relationship depicted naturally and logically?",
    "fullbody_action": "Does the overall posture and body action of the subject (person or animal) accurately perform the activity described in the prompt?",
    "spatial_2d": "Are the relative positions of objects on the 2D plane (e.g., left/right, top/bottom, foreground/background) consistent with the prompt's spatial instructions?",
    "spatial_3d": "Does the layout, occlusion, and relative position of objects in 3D space conform to the prompt requirements or spatial logic?",
    "composition_relationship": "Does the image successfully integrate multiple elements into a visually coherent and logically consistent whole?",
    "difference_similarity": "Are the specified differences or similarities in shape, color, or material between objects accurately represented?",
    "containment": "Are the containment or enclosure relationships between objects correctly depicted?",
    "real_world_scene": "Does the scene type and environmental setting (e.g., office, forest, street) match the location described in the prompt?",
    "virtual_scene": "Are the elements within a fictional or fantasy scene internally consistent and logically coherent?",
}

ACTIVATE = {
    "physical_logic": "有光影、液体、悬挂、堆叠等物理场景时",
    "material_texture": "材质是画面重要成分时",
    "detail_richness": "恒定相关，画面依赖细节时优先",
    "artifacts": "恒定相关",
    "edge_clarity": "恒定相关",
    "naturalness": "人物、皮肤、食物题材优先",
    "anatomical_fidelity": "人物/动物题材必选",
    "composition": "恒定相关",
    "color_harmony": "恒定相关",
    "lighting_atmosphere": "题面指定了时间/光照/氛围时优先",
}
GENERIC_ALL = {
    "Alignment": [k for k, p, _, _ in FACETS if p == "Alignment"],
    "Quality": [k for k, p, _, _ in FACETS if p == "Quality"],
    "Aesthetics": [k for k, p, _, _ in FACETS if p == "Aesthetics"],
}


def build_v53_user_prompt(q: dict) -> str:
    """v5.3：题目 → judge user prompt 文本（图像占位 <image>）。"""
    criteria = {k: c for k, _, _, c in FACETS}

    def _tiers(c: dict) -> str:
        rub = c.get("rubric") or {}
        return (f"0 Fail: {rub.get('tier_0', '?')} | "
                f"1 Pass: {rub.get('tier_1', '?')} | "
                f"2 Excel: {rub.get('tier_2', '?')}")

    f_lines = []
    for i, c in enumerate(q.get("fidelity_checks") or [], 1):
        var = c.get("variants_used") or "无"
        vis = c.get("visibility_requirement") or "必须可见；不可见按 N/A"
        lens = str(c.get("lens") or "").strip()
        lens_line = f"     验证镜头: {lens}\n" if lens else ""
        f_lines.append(f"[F{i}] {c.get('check', '')}\n"
                       f"     rubric: {_tiers(c)}\n"
                       f"{lens_line}"
                       f"     变体: {var}\n"
                       f"     可见条件: {vis}")
    alf = [t for t in GENERIC_ALL["Alignment"] if t in FACET_KEYS]
    a_lines = []
    for t in alf:
        a_lines.append(f"- {t}: {criteria[t]}\n  QIB: {QIB_EN.get(t, '')}"
                       f"\n  不适用条件: 题面未含该类显式规定（如数量/颜色/姿态/位置/材质/场景/风格）时记 N/A，不得替题面添加未说的规定")
    qf = GENERIC_ALL["Quality"]     # 全量判分（v5.4.4）：不再用出题人激活子集
    af = GENERIC_ALL["Aesthetics"]
    qblock = "\n".join(f"- {t}: {criteria[t]}\n  QIB: {QIB_EN[t]}\n  不适用条件: {ACTIVATE[t]}（不适用记 N/A）"
                       for t in qf)
    ablock = "\n".join(f"- {t}: {criteria[t]}\n  QIB: {QIB_EN[t]}\n  不适用条件: {ACTIVATE[t]}（不适用记 N/A）"
                       for t in af)
    return T2I_USER_TPL_V53.format(
        gen_prompt=q.get("gen_prompt", ""),
        nf=len(q.get("fidelity_checks") or []),
        na=len(alf), nq=len(qf), ns=len(af),
        fidelity_block="\n\n".join(f_lines) or "（无）",
        alignment_block="\n".join(a_lines) or "（无）",
        quality_block=qblock or "（本题未激活）",
        aesthetic_block=ablock or "（本题未激活）",
        a_example=alf[0] if alf else "quantity",
        a_example2=alf[1] if len(alf) > 1 else "color",
        q_example=qf[0] if qf else "physical_logic",
        aes_example=af[0] if af else "composition",
    )


def negative_critical_idx(q: dict, cmap: dict) -> set:
    """v5.4：锚点引用 tier=defining 的 must_not_have 约束的保真检查（0 基索引）。

    负向封顶分层（与正向 defining×2 加权对称）：
    - defining 负向被违反 = 画错实体 → 该检查 0 档触发总分封顶 20；
    - peripheral 负向被违反 = 外围禁项踩线，概念未画错 → 检查本身 0 分
      （负向独立成条）并按 1.0 权重拉低保真线，不触发封顶。
    清单无 tier 字段（v5.3 老批）→ 全部负向照旧封顶，行为与 v5.3 一致。
    清单极性 must_not_have 在推导中只能保持或接受（极性翻转仅
    must_have→must_not_have 一向），故所有锚型（含 derivation）一视同仁。
    cmap: {实例名: 约束清单记录}。
    """
    crec = cmap.get(q.get("_query_label", "")) if cmap else None
    pol: dict[int, str] = {}
    tier: dict[int, str] = {}
    has_tier = False
    if crec:
        for c in crec.get("constraints") or []:
            pol[c["id"]] = c.get("polarity") or ""
            tier[c["id"]] = c.get("tier") or ""
            if c.get("tier"):
                has_tier = True
    idx = set()
    for i, c in enumerate(q.get("fidelity_checks") or []):
        an = c.get("anchor") or {}
        refs: list[str] = []
        for r in an.get("constraint_refs") or []:
            refs.extend(x for x in re.split(r"[,，、]", str(r)) if x.strip())
        for r in refs:
            rs = r.strip().strip("[]（）()").lstrip("Cc").strip()
            if (rs.isdigit() and pol.get(int(rs)) == "must_not_have"
                    and (not has_tier or tier.get(int(rs)) == "defining")):
                idx.add(i)
                break
    return idx


def defining_check_idx(q: dict, cmap: dict) -> set:
    """v5.4（defining×2 已批）：锚点引用 tier=defining 约束的保真检查（0 基索引）。

    约束清单侧由合并编辑（probe_merge_prompt.md 第五步）标注 tier：
    defining = 缺一即不再是该概念的定义性特征；peripheral = 外围细节。
    清单无 tier 字段时返回空集（全 1.0 权重，行为与 v5.3 完全一致）。
    """
    crec = cmap.get(q.get("_query_label", "")) if cmap else None
    defs = set()
    if crec:
        defs = {c["id"] for c in crec.get("constraints") or []
                if c.get("tier") == "defining"}
    idx = set()
    for i, c in enumerate(q.get("fidelity_checks") or []):
        an = c.get("anchor") or {}
        refs: list[str] = []
        for r in an.get("constraint_refs") or []:
            refs.extend(x for x in re.split(r"[,，、]", str(r)) if x.strip())
        for r in refs:
            rs = r.strip().strip("[]（）()").lstrip("Cc").strip()
            if rs.isdigit() and int(rs) in defs:
                idx.add(i)
                break
    return idx


DEFINING_WEIGHT = 2.0          # 定义性检查计入保真线的权重（peripheral = 1.0）


def finalize_v53(q: dict, parsed: dict, raw: str, neg_idx: set,
                 def_idx: set | None = None) -> dict:
    """v5.3：已解析 judge JSON → 四维线分 + 加权总分（纯权重聚合）。

    v5.4.1（2026-08-29 拍板）：取消机械封顶与保真熔断钳制——得分连续、
    可解释；严重度已由 defining×2 加权与负向独立成条承载（画错实体 ⇒
    保真线趋 0 ⇒ 总分上限 ≈ 0.5 权重剩余 ~45）。违反/低线事件降级为
    诊断标记（*_violated / fidelity_low），不再影响总分。
    v5.4.1 增强：
    - def_idx 内的保真检查以 DEFINING_WEIGHT=2.0 计入保真线加权均值；
    - v5.4.2 保真乘子（2026-08-29 拍板）：total = G × F/100——知识门控
      通用分：通用线 G = 对齐/质量/美感三支柱等权（v5.4.3：各 1/3；保真
      只作乘子，不进 G），先打满再按保真线 F 折算。画错实体 ⇒ 总分随
      F 趋 0（连续无悬崖，接替旧封顶语义）；保真单通道传导，不再进加权和。
    """
    def_idx = def_idx or set()

    def _line(scores: list) -> float | None:
        vals = [PHI[s] for s in scores if s in (0, 1, 2)]
        return sum(vals) / len(vals) if vals else None

    def _line_weighted(scores: list) -> float | None:
        num = den = 0.0
        for i, s in enumerate(scores):
            if s not in (0, 1, 2):
                continue
            w = DEFINING_WEIGHT if i in def_idx else 1.0
            num += w * PHI[s]
            den += w
        return num / den if den else None

    fc = q.get("fidelity_checks") or []
    # v5.4.4 全量判分：质量/美感线 = 全部 Q/A 项（N/A 自动剔除），不用出题人激活子集
    # v5.5.3 对齐线：判官基于题面×图直判 18 项全量（题面无该类规定 → N/A 剔除）
    alf = [t for t in GENERIC_ALL["Alignment"] if t in FACET_KEYS]
    qf = [t for t in GENERIC_ALL["Quality"] if t in FACET_KEYS]
    af = [t for t in GENERIC_ALL["Aesthetics"] if t in FACET_KEYS]

    fscores = {c.get("index"): c.get("score") for c in parsed.get("fidelity", [])
               if isinstance(c, dict)}
    f_list = [fscores.get(i + 1) for i in range(len(fc))]
    # 兼容旧 index 列表式（v5.5.1 前判分产物）：整体视为无 facet 分
    a_raw = parsed.get("alignment")
    a_scores = a_raw if isinstance(a_raw, dict) else {}
    a_list = [a_scores.get(t) for t in alf]
    q_scores = parsed.get("quality") or {}
    s_scores = parsed.get("aesthetics") or {}

    fidelity = _line_weighted(f_list)
    alignment = _line(a_list)
    quality = _line([q_scores.get(t) for t in qf])
    aesthetics = _line([s_scores.get(t) for t in af])

    lines = {"fidelity": fidelity, "alignment": alignment,
             "quality": quality, "aesthetics": aesthetics}
    # v5.4.3：通用线 G ＝ 对齐/质量/美感三支柱等权（QIB 支柱平等，1/3 各），
    # 保真只作乘子进总分（v5.5.4 加地板）：total = G × (FLOOR+(1-FLOOR)×F/100)
    # （fidelity 缺失时退化为 G）
    gen_w = {"alignment": 1 / 3, "quality": 1 / 3, "aesthetics": 1 / 3}
    gsum = sum(w for k, w in gen_w.items() if lines.get(k) is not None)
    generic = (sum(gen_w[k] * lines[k] for k, w in gen_w.items()
                   if lines.get(k) is not None) / gsum) if gsum else 0.0
    total = (generic * (FIDELITY_FLOOR + (1.0 - FIDELITY_FLOOR) * fidelity / 100.0)
             if fidelity is not None else generic)

    # v5.4.1 纯权重：不再钳制总分；以下为诊断标记（供报表/审阅格分析）
    neg_violated = any(f_list[i] == 0 for i in neg_idx if i < len(f_list))
    align_violated = any(a_scores.get(t) == 0 for t in alf)
    quality_violated = any(q_scores.get(t) == 0 for t in qf)
    fidelity_low = fidelity is not None and fidelity < V53_LINE_FUSE_THRESHOLD

    # v5.6：检查带镜头字段（视觉知识检测线）；旧批无镜头字段按 10 条/条数
    # 照旧打标签（判官模板对旧批仅少「验证镜头」与兜底口径差异）
    if any(str(c.get("lens") or "").strip() for c in fc):
        schema_tag = "v5.6"
    else:
        schema_tag = "v5.5" if len(fc) == 10 else "v5.3"

    fb_raw = parsed.get("knowledge_fallback")
    fallback = fb_raw if isinstance(fb_raw, list) else []

    return {
        "qid": q.get("qid"), "task": "t2i",
        "schema": schema_tag,
        "invocation": q.get("invocation"),
        "fidelity_score": None if fidelity is None else round(fidelity, 2),
        "alignment_score": None if alignment is None else round(alignment, 2),
        "quality_score": None if quality is None else round(quality, 2),
        "aesthetic_score": None if aesthetics is None else round(aesthetics, 2),
        "generic_score": round(generic, 2),
        "total": round(total, 2),
        "neg_critical_violated": neg_violated,
        "align_critical_violated": align_violated,
        "quality_critical_violated": quality_violated,
        "fidelity_low": fidelity_low,
        "neg_critical_idx": sorted(neg_idx) or None,
        "fidelity_scores": f_list,
        "alignment_scores": {t: a_scores.get(t) for t in alf},
        "quality_scores": {q_scores.get(t) for t in qf} if False else {t: q_scores.get(t) for t in qf},
        "aesthetic_scores": {t: s_scores.get(t) for t in af},
        "alignment_reasons": {t: (parsed.get("alignment_reasons") or {}).get(t)
                              for t in alf},
        "quality_reasons": {t: (parsed.get("quality_reasons") or {}).get(t)
                            for t in qf},
        "aesthetic_reasons": {t: (parsed.get("aesthetics_reasons") or {}).get(t)
                              for t in af},
        "fidelity_reasons": {c.get("index"): c.get("reason")
                             for c in parsed.get("fidelity", [])
                             if isinstance(c, dict)},
        "knowledge_fallback": fallback,
        "raw": raw,
    }


def score_t2i_v53(q: dict, img_url: str, args, neg_idx: set,
                  def_idx: set | None = None) -> dict:
    """v5.3：build prompt → judge → 解析 → finalize（v5.7 线索制自动分流）。"""
    v57 = _is_v57(q)
    user = build_v57_user_prompt(q) if v57 else build_v53_user_prompt(q)
    marker = "knowledge" if v57 else "fidelity"
    content = [{"type": "text", "text": user.replace("<image>", "")},
               {"type": "image_url", "image_url": {"url": img_url}}]
    budget = _judge_budget(args.endpoint, args.model, T2I_SYSTEM_V53, content,
                           fallback=getattr(args, "max_tokens", JUDGE_CTX_LEN))
    use = min(getattr(args, "max_tokens", JUDGE_CTX_LEN), budget)

    def _call(mt: int) -> str:
        return call_judge(args.endpoint, args.model, T2I_SYSTEM_V53, content,
                          api_key=args.api_key, think=args.think,
                          max_tokens=mt)

    try:
        raw = _call(use)
    except RuntimeError as e:
        msg = str(e)
        if "400" in msg and any(w in msg for w in ("context", "length", "token")):
            budget = _judge_budget(args.endpoint, args.model, T2I_SYSTEM_V53,
                                   content, img_reserve=JUDGE_IMG_RESERVE_HI)
            use = min(use, budget)
            print(f"  [warn] judge 上下文超限，收紧 max_tokens -> {use} 重试",
                  file=sys.stderr)
            raw = _call(use)
        else:
            raise
    try:
        parsed = extract_json(raw, marker=marker)
    except ValueError:
        if use >= budget:
            raise
        print(f"  [warn] judge 输出疑似截断，max_tokens {use} -> {budget} 重试",
              file=sys.stderr)
        raw = _call(budget)
        parsed = extract_json(raw, marker=marker)
    if v57:
        return finalize_v57(q, parsed, raw)
    return finalize_v53(q, parsed, raw, neg_idx, def_idx)


# ---------------------------------------------------------------------------
# 判分基础设施
# ---------------------------------------------------------------------------
def encode_image(path: Path, max_edge: int) -> str:
    img = Image.open(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        k = max_edge / max(w, h)
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def call_judge(endpoint: str, model: str, system: str,
               content: list, api_key: str = "", think: bool = False,
               retries: int = 3, timeout: float = 600.0,
               max_tokens: int = JUDGE_CTX_LEN) -> str:
    payload = {
        "model": model,
        "stream": False,
        "temperature": 0.0,
        "seed": 42,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    if think:
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for attempt in range(retries):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers,
                                 timeout=timeout)
            if resp.status_code in (429, 529):
                raise RuntimeError(f"HTTP {resp.status_code} 限流: {resp.text[:120]}")
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            msg = resp.json()["choices"][0]["message"]
            text = msg.get("content")
            if text is None:
                # reasoning-only 响应：回退 reasoning_content
                text = msg.get("reasoning_content")
                if text:
                    print("  [note] reasoning-only 响应，回退 reasoning_content",
                          file=sys.stderr)
            if text is None:
                raise RuntimeError("content=null（reasoning 可能耗尽 max_tokens）")
            return text
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            if "429" in str(e):
                wait = 30 * (attempt + 1)
            print(f"  [warn] judge 调用失败（{e}），{wait}s 后重试",
                  file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _judge_budget(endpoint: str, model: str, system: str, content: list,
                  img_reserve: int = JUDGE_IMG_RESERVE,
                  fallback: int = JUDGE_CTX_LEN) -> int:
    """单请求可用生成预算 = 上下文上限 - 文本 prompt token - 图像预留。

    文本 token 走 vLLM /tokenize 精确计量；端点不支持计量（如网关/外部模型）
    时用 fallback（= 调用方请求的 max_tokens 上限）兜底。
    """
    try:
        base = endpoint.split("/v1/")[0]
        text = system + "\n" + "".join(
            c.get("text", "") for c in content if c.get("type") == "text")
        r = requests.post(base + "/tokenize",
                          json={"model": model, "prompt": text}, timeout=60)
        if r.status_code != 200:
            return fallback
        tok = r.json().get("tokens")
        n_text = len(tok) if isinstance(tok, list) else int(tok)
        return max(1024, JUDGE_CTX_LEN - n_text - img_reserve - JUDGE_BUDGET_MARGIN)
    except Exception:  # noqa: BLE001
        return fallback


def extract_json(content: str, marker: str = None) -> dict:
    """从 judge 输出中抠出判分结果 JSON 对象（容忍前后杂文/多次拼接）。

    thinking 推理中常引述输出模板里的示例片段（如 {"physical_logic": 1,
    "color": "N/A"}），「取第一个合法 JSON」会被示例污染导致判分全空
    （2026-08-26 实测 30 题 18 题中枪）。改为收集全部顶层合法对象，
    优先取含 marker 键的最后一个（真实判分结果），否则取最后一个。
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    dec = json.JSONDecoder()
    cands = []
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            break
        try:
            obj, end = dec.raw_decode(text[start:])
            if isinstance(obj, dict):
                cands.append(obj)
            pos = start + end
        except json.JSONDecodeError:
            pos = start + 1
    if not cands:
        raise ValueError(f"输出无合法 JSON 对象: {content[:150]!r}")
    real = [c for c in cands
            if isinstance(c.get(marker), (dict, list))]
    return real[-1] if real else cands[-1]


def load_jsonl(path: Path) -> list:
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ---------------------------------------------------------------------------
# v6.0 三维通用规则判分（对齐十轴 / 质量八项 / 美感四项）
#
# 判分契约全文内联在 judge_prompt_gen_v6.0_V2.md（用户拍板：不从代码词表
# 生成，改细则只改文档）：判官自推隐式蕴含，不注入出题思路。运行时整篇
# USER 模板直接作 judge prompt（只填 {gen_prompt}）；<image> 处按模板原位
# 拆分为 text/image/text 三段 content。输出键集合校验：缺键/多项/越界 →
# 重判一次，仍坏该题记 fail（写 fail 行，resume 时 fail 行不入 done 可重试）。
# 三线分报告，不出总分。
# ---------------------------------------------------------------------------
V60_AXES = ["subject_presence", "form_structure", "color_material",
            "quantity_scale", "spatial_relation", "text_symbol",
            "action_interaction", "state_context", "scene_environment", "style"]
V60_QUALITY = ["physical_logic", "material_texture", "detail_richness",
               "artifacts", "resolution", "edge_clarity", "naturalness",
               "anatomical_fidelity"]
V60_AESTHETICS = ["composition", "color_harmony", "lighting_atmosphere",
                  "emotional_expression"]
V60_DIMS = {"alignment": V60_AXES, "quality": V60_QUALITY,
            "aesthetics": V60_AESTHETICS}

_V60_TPL_CACHE: dict[str, tuple[str, str]] = {}


def _md_code_block(text: str, head: str) -> str:
    m = re.search(rf"## {re.escape(head)}[^\n]*\n+```\n(.*?)\n```", text, re.DOTALL)
    if not m:
        raise ValueError(f"判分指令 md 缺少「## {head}」代码块")
    return m.group(1)


def load_v60_template(cond: str) -> tuple[str, str]:
    """v6.0：从 judge_prompt_gen_v6.0_<cond>.md 读 (SYSTEM, USER 模板)。"""
    if cond not in _V60_TPL_CACHE:
        fp = SUB_DIR / f"judge_prompt_gen_v6.0_{cond}.md"
        text = fp.read_text(encoding="utf-8")
        _V60_TPL_CACHE[cond] = (_md_code_block(text, "SYSTEM"),
                                _md_code_block(text, "USER 模板"))
    return _V60_TPL_CACHE[cond]


def build_v60_messages(q: dict, cond: str, img_url: str) -> tuple[str, list]:
    """v6.0：题目 + 判分指令 md → (system, content)；<image> 原位拆分。"""
    system, user_tpl = load_v60_template(cond)
    user = user_tpl.replace("{gen_prompt}", str(q.get("gen_prompt") or ""))
    if leftover := re.search(r"\{(?:gen_prompt|reasoning)\}", user):
        raise ValueError(f"USER 模板存在未填充占位符: {leftover.group(0)}")
    if leftover := re.search(r"\{(?:gen_prompt|reasoning)\}", user):
        raise ValueError(f"USER 模板存在未填充占位符: {leftover.group(0)}")
    before, _, after = user.partition("<image>")
    content: list = [{"type": "text", "text": before},
                     {"type": "image_url", "image_url": {"url": img_url}}]
    if after.strip():
        content.append({"type": "text", "text": after})
    return system, content


def _v60_norm(v):
    """v6.0：档位归一——int 0/1/2 原样、字符串数字与 N/A 变体容错，非法 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v in (0, 1, 2):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.upper() in ("N/A", "NA"):
            return "N/A"
        if s in ("0", "1", "2"):
            return int(s)
    return None


def validate_v60(parsed: dict) -> str:
    """v6.0：键集合校验（三维度 + 三个 *_reasons 全键、取值合法）。

    返回错误描述（空串 = 通过）。alignment 按 V60_AXES 十轴、quality 八项、
    aesthetics 四项；缺键/多键/越界均判不合规。
    """
    errs = []
    for dim, keys in V60_DIMS.items():
        obj = parsed.get(dim)
        if not isinstance(obj, dict):
            errs.append(f"{dim} 非 object")
            continue
        got = set(obj)
        if missing := [k for k in keys if k not in got]:
            errs.append(f"{dim} 缺键 {missing}")
        if extra := sorted(got - set(keys)):
            errs.append(f"{dim} 多键 {extra}")
        for k in sorted(got & set(keys)):
            if _v60_norm(obj[k]) is None:
                errs.append(f"{dim}.{k}={obj[k]!r} 越界")
        rkey = f"{dim}_reasons"
        robj = parsed.get(rkey)
        if not isinstance(robj, dict):
            errs.append(f"{rkey} 非 object")
        elif rmiss := [k for k in keys if k not in robj]:
            errs.append(f"{rkey} 缺键 {rmiss}")
    return "; ".join(errs)


def finalize_v60(q: dict, parsed: dict, raw: str, cond: str, model: str,
                 source: str) -> dict:
    """v6.0：已校验 judge JSON → 三线分（φ 均值，N/A 剔除）+ 全量明细。"""

    def _line(dim: str) -> float | None:
        vals = [PHI[n] for k in V60_DIMS[dim]
                if (n := _v60_norm(parsed[dim][k])) in (0, 1, 2)]
        return round(sum(vals) / len(vals), 2) if vals else None

    scores = {d: {k: _v60_norm(parsed[d][k]) for k in V60_DIMS[d]} for d in V60_DIMS}
    reasons = {d: {k: (parsed.get(f"{d}_reasons") or {}).get(k)
                   for k in V60_DIMS[d]} for d in V60_DIMS}
    nas = {d: [k for k, v in scores[d].items() if v == "N/A"] for d in V60_DIMS}
    return {
        "qid": q.get("qid"), "task": "t2i", "schema": f"v6.0-{cond}",
        "judge_model": model, "image_model": source, "level": q.get("level"),
        "alignment_score": _line("alignment"),
        "quality_score": _line("quality"),
        "aesthetic_score": _line("aesthetics"),
        "alignment_scores": scores["alignment"],
        "quality_scores": scores["quality"],
        "aesthetic_scores": scores["aesthetics"],
        "alignment_reasons": reasons["alignment"],
        "quality_reasons": reasons["quality"],
        "aesthetic_reasons": reasons["aesthetics"],
        "na_keys": nas,
        "raw": raw,
    }


def score_t2i_v60(q: dict, img_url: str, args) -> dict:
    """v6.0：build（读 md 模板）→ judge → 解析 → 键集合校验（重判一次）→ finalize。"""
    cond = args.v60
    system, content = build_v60_messages(q, cond, img_url)

    def _call() -> str:
        return call_judge(args.endpoint, args.model, system, content,
                          api_key=args.api_key, think=args.think,
                          max_tokens=args.max_tokens)

    raw = _call()
    err = None
    try:
        parsed = extract_json(raw, marker="alignment")
        err = validate_v60(parsed)
    except ValueError as e:
        err = f"JSON 解析失败: {e}"
        parsed = None
    if err:
        print(f"  [warn] {q.get('qid')} v60 输出不合规（{err}），重判一次",
              file=sys.stderr)
        raw = _call()
        parsed = extract_json(raw, marker="alignment")
        err = validate_v60(parsed)
        if err:
            raise ValueError(f"v60 重判后仍不合规: {err}")
    return finalize_v60(q, parsed, raw, cond, args.model,
                        _derive_source(args))


# ---------------------------------------------------------------------------
# 判官能力基线（判分启动硬前提）：计数 / 中文 OCR / 镜像一致性。
# PIL 构造确定性探针（seed 固定 → 两判官见同一组图），零外部样本依赖。
# ---------------------------------------------------------------------------
BASELINE_SYSTEM = "你是图像问答助手。严格只输出一个合法 JSON 对象，不输出其他内容。"
BASELINE_TEXTS = ["国家重点实验室", "危险化学品仓库", "光明乳业有限责任公司",
                  "第肆拾贰号令", "黄鹤楼东停车场", "静安区人民政府",
                  "3号航站楼出发层", "抢险救援通道禁止停留"]


def _cjk_font(size: int) -> ImageFont.FreeTypeFont:
    import glob  # noqa: PLC0415
    cands = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"]
    cands += sorted(glob.glob("/usr/share/fonts/**/*CJK*.tt[cf]",
                              recursive=True))
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    raise FileNotFoundError("系统无 CJK 字体（baseline 中文 OCR 探针需要）")


def _b64_pil(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _render_text(text: str) -> Image.Image:
    img = Image.new("RGB", (960, 150), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((img.width // 2, img.height // 2), text, font=_cjk_font(58),
           fill=(20, 20, 20), anchor="mm")
    return img


def _probe_count(rng):
    n = rng.randint(5, 12)
    img = Image.new("RGB", (512, 512), (250, 250, 250))
    d = ImageDraw.Draw(img)
    pts, tries = [], 0
    while len(pts) < n and tries < 4000:
        tries += 1
        x, y, r = rng.randint(45, 467), rng.randint(45, 467), rng.randint(16, 26)
        if all((x - a) ** 2 + (y - b) ** 2 >= (r + rr + 12) ** 2
               for a, b, rr in pts):
            pts.append((x, y, r))
    for x, y, r in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(198, 40, 40))
    prompt = ("数一数图中红色圆的数量（所有圆完整可见、互不遮挡）。"
              '只输出 JSON：{"count": <整数>}')
    return img, prompt, {"count": len(pts)}


def _probe_ocr(rng):
    t = rng.choice(BASELINE_TEXTS)
    prompt = '逐字抄录图中出现的文字，保持原样。只输出 JSON：{"text": "..."}'
    return _render_text(t), prompt, {"text": t}


def _probe_mirror(rng):
    t = rng.choice(BASELINE_TEXTS)
    img = _render_text(t)
    mirrored = rng.random() < 0.5
    if mirrored:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    prompt = ('判断图中文字是否被水平镜像翻转（字序与字形左右颠倒，而非左右换行）。'
              '只输出 JSON：{"mirrored": true 或 false}')
    return img, prompt, {"mirrored": mirrored}


def run_baseline(args) -> None:
    """判官能力基线：3 类探针 × --n 张，报告精确率（计数含 ±1 近似）。"""
    import random  # noqa: PLC0415
    rng = random.Random(args.seed)
    judge_sn = re.sub(r"[^A-Za-z0-9._-]+", "-", args.model.split("/")[-1])
    out_dir = EVAL_DIR / "baseline" / judge_sn
    out_dir.mkdir(parents=True, exist_ok=True)
    probes = ([("count", i, *_probe_count(rng)) for i in range(args.n)]
              + [("ocr", i, *_probe_ocr(rng)) for i in range(args.n)]
              + [("mirror", i, *_probe_mirror(rng)) for i in range(args.n)])
    ok = {"count": 0, "ocr": 0, "mirror": 0}
    results = {"model": args.model, "endpoint": args.endpoint,
               "n_per_type": args.n, "seed": args.seed, "probes": []}
    for kind, i, img, prompt, truth in probes:
        fp = out_dir / f"{kind}_{i}.png"
        img.save(fp)
        rec = {"kind": kind, "probe": fp.name, "truth": truth}
        try:
            content = [{"type": "text", "text": prompt},
                       {"type": "image_url", "image_url": {"url": _b64_pil(img)}}]
            raw = call_judge(args.endpoint, args.model, BASELINE_SYSTEM, content,
                             api_key=args.api_key, max_tokens=2048)
            parsed = extract_json(raw)
            if kind == "count":
                got = parsed.get("count")
                rec.update(got=got, exact=got == truth["count"],
                           off_by_one=isinstance(got, int)
                           and abs(got - truth["count"]) == 1)
            elif kind == "ocr":
                got = str(parsed.get("text") or "").strip()
                rec.update(got=got, exact=got == truth["text"])
            else:
                got = parsed.get("mirrored")
                rec.update(got=got, exact=bool(got) is truth["mirrored"])
            if rec.get("exact"):
                ok[kind] += 1
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
        results["probes"].append(rec)
        print(f"[baseline] {kind}_{i}: exact={rec.get('exact')} "
              f"got={rec.get('got', rec.get('error'))}", flush=True)
    results["summary"] = {k: f"{ok[k]}/{args.n}" for k in ok}
    rep_fp = out_dir / "report.json"
    rep_fp.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps(results["summary"], ensure_ascii=False))
    print(f"report -> {rep_fp}")


# ---------------------------------------------------------------------------
# v5.2 遗留判分路径（8-25 版本原样保留；仅供旧批次重放，现行批次均为 v5.3+）
# ---------------------------------------------------------------------------
T2I_SYSTEM = "你是文生图评测专家。给定生成提示词与生成图，你按结构化清单逐项判分，严格输出 JSON，不输出其他内容。"

T2I_USER_TPL = """# 生成提示词
{gen_prompt}

# 生成图
<image>

# 打分规则
每个校验点打分：0（Fail：明显缺陷/未达成）、1（Pass：基本达成，无可见缺陷）、2（Excel：出色，有具体可察的优秀表现）；不适用的通用维度打 "N/A"。

# 一、知识校验点（逐条判，按题面定义）
{checks_block}

# 二、门槛判定
主体是否缺失、主题是否跑偏（是则 gate=true）。

# 三、通用维度清单（仅判列出的维度）
{facets_block}

# 输出格式（只输出合法 JSON，不要 markdown 围栏）：
{{"gate": {{"subject_missing": true或false, "reason": "10字内"}}, "knowledge_checks": [{{"index": 0, "score": 0}}, {{"index": 1, "score": 2}}], "facets": {{"physical_logic": 1, "color": "N/A"}}}}"""


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def score_t2i(q: dict, img_url: str, args) -> dict:
    checks = q.get("implicit_checks") or []
    checks_block = "\n".join(
        f"{i}. {c.get('check', '')}（考察知识：{c.get('knowledge', '')}，权重 {c.get('weight', 0)}）"
        for i, c in enumerate(checks))
    tags = [t for t in (q.get("facet_tags") or []) if t in FACET_KEYS]
    criteria = {k: c for k, _, _, c in FACETS}
    facets_block = "\n".join(f"- {k}: {criteria[k]}" for k in tags) or "-（本题未激活通用维度）"
    user = T2I_USER_TPL.format(gen_prompt=q.get("gen_prompt", ""),
                               checks_block=checks_block or "（无）",
                               facets_block=facets_block)
    content = [{"type": "text", "text": user.replace("<image>", "")},
               {"type": "image_url", "image_url": {"url": img_url}}]
    raw = call_judge(args.endpoint, args.model, T2I_SYSTEM, content,
                     api_key=args.api_key, think=args.think)
    parsed = extract_json(raw)

    # 知识线：权重加和（题面权重合计 1.0）
    kscores = {c["index"]: c["score"] for c in parsed.get("knowledge_checks", [])
               if isinstance(c.get("score"), int) and c["score"] in (0, 1, 2)}
    ksum_w = sum(c.get("weight", 0) for c in checks) or 1.0
    knowledge = sum(checks[i].get("weight", 0) * PHI[kscores.get(i, 0)]
                    for i in range(len(checks))) / ksum_w

    # 通用线：激活且非 N/A 的 facet 均值
    fscores = parsed.get("facets", {})
    vals = [PHI[s] for t in tags
            if isinstance((s := fscores.get(t)), int) and s in (0, 1, 2)]
    general = sum(vals) / len(vals) if vals else None

    total = (KNOWLEDGE_WEIGHT * knowledge
             + (1 - KNOWLEDGE_WEIGHT) * (general if general is not None else knowledge))
    gate = (parsed.get("gate") or {}).get("subject_missing") is True
    if gate:
        total = min(total, GATE_CAP)
    return {
        "qid": q.get("qid"), "task": "t2i",
        "knowledge_score": round(knowledge, 2),
        "general_score": None if general is None else round(general, 2),
        "total": round(total, 2),
        "gate_capped": gate,
        "facet_scores": fscores,
        "check_scores": kscores,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# 判分契约物化 / 聚合 / 入口
# ---------------------------------------------------------------------------
def materialize_judge_prompts() -> None:
    """物化判分契约到 data/judge_prompts（审计/调试用；消费者为本脚本自身与人工复核）。"""
    JUDGE_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    (JUDGE_PROMPTS_DIR / "facet_taxonomy.json").write_text(
        json.dumps([{"key": k, "pillar": p, "sub": s, "criterion": c}
                    for k, p, s, c in FACETS],
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    (JUDGE_PROMPTS_DIR / "t2i_judge_template.md").write_text(
        f"SYSTEM:\n{T2I_SYSTEM}\n\nUSER:\n{T2I_USER_TPL}", encoding="utf-8")
    (JUDGE_PROMPTS_DIR / "t2i_judge_template_v5.5.md").write_text(
        f"SYSTEM:\n{T2I_SYSTEM_V53}\n\nUSER:\n{T2I_USER_TPL_V53}\n\n"
        f"权重: {json.dumps(V53_WEIGHTS, ensure_ascii=False)}（历史参考；现行：保真乘子）\n"
        f"聚合: 保真乘子门控加地板（total = G × ({FIDELITY_FLOOR}+{1-FIDELITY_FLOOR:.1f}×F/100)；"
        f"G = 三支柱等权均值(对齐/质量/美感)；"
        f"defining 检查保真线 ×2；判档全面对齐 QIB 缺陷刻度 0 Fail/1 Pass/2 Excel）\n"
        f"诊断标记: 保真线 < {V53_LINE_FUSE_THRESHOLD} 记 fidelity_low（不影响总分）",
        encoding="utf-8")


def aggregate(rows: list, v53: bool = False, v60: str = None) -> dict:
    def mean(key: str):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    if v60:
        return {"mode": "t2i-v60", "condition": v60,
                "n": sum(1 for r in rows if not r.get("fail")),
                "judge_fail": sum(1 for r in rows if r.get("fail")),
                "alignment": mean("alignment_score"),
                "quality": mean("quality_score"),
                "aesthetics": mean("aesthetic_score")}
    rep = {"mode": "t2i", "n": len(rows),
           "overall": mean("total")}
    if v53:
        rep.update({
            "fidelity": mean("fidelity_score"),
            "alignment": mean("alignment_score"),
            "quality": mean("quality_score"),
            "aesthetics": mean("aesthetic_score"),
            "neg_critical_violated": sum(1 for r in rows if r.get("neg_critical_violated")),
            "align_critical_violated": sum(1 for r in rows if r.get("align_critical_violated")),
            "quality_critical_violated": sum(1 for r in rows if r.get("quality_critical_violated")),
            "fidelity_low": sum(1 for r in rows if r.get("fidelity_low")),
        })
    else:
        rep.update({
            "knowledge": mean("knowledge_score"),
            "general": mean("general_score"),
            "gate_capped": sum(1 for r in rows if r.get("gate_capped")),
        })
    return rep


def _derive_source(args) -> str:
    """v6.0：图源标签——--source 显式优先，默认从 responses 文件名/父目录推导。"""
    if getattr(args, "source", None):
        return re.sub(r"[^A-Za-z0-9._-]+", "-", args.source)
    tag = re.sub(r"^responses_", "", args.responses.stem)
    tag = re.sub(r"_shard\d+$", "", tag)
    if re.fullmatch(r"shard\d+|responses?", tag or ""):
        tag = args.responses.parent.name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", tag)


def run(args) -> None:
    questions = {q["qid"]: q for q in load_jsonl(args.questions)}
    responses = load_jsonl(args.responses)
    materialize_judge_prompts()

    v60 = getattr(args, "v60", None)
    v53 = any("fidelity_checks" in q for q in questions.values())
    cmap: dict = {}
    if v53:
        if args.constraints and args.constraints.exists():
            cmap = {c["instance"]: c
                    for c in load_jsonl(args.constraints)}
        else:
            print("  [warn] v5.3 题目未提供 --constraints，"
                  "负向关键项机械封顶不生效", file=sys.stderr)

    if args.out:
        out_path = args.out
    elif v60:
        judge_sn = re.sub(r"[^A-Za-z0-9._-]+", "-", args.model.split("/")[-1])
        out_path = (EVAL_DIR /
                    f"scores_t2i_v60_{v60}_{judge_sn}_{_derive_source(args)}.jsonl")
    else:
        out_path = EVAL_DIR / "scores_t2i.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                if not rec.get("fail"):   # v6.0 fail 行不入 done（重跑可重试）
                    done.add(rec.get("qid"))
        if done:
            print(f"  [resume] {out_path.name} 已有 {len(done)} 题，跳过",
                  flush=True)

    import threading                                    # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor   # noqa: PLC0415
    lock = threading.Lock()
    rows, n_fail, counters = [], 0, {"fail": 0}

    def _job(i_resp):
        nonlocal rows
        i, resp = i_resp
        qid = resp.get("qid")
        q = questions.get(qid)
        if q is None or qid in done:
            return
        img = Path(resp["image"])
        if not img.is_absolute():
            img = args.responses.parent / img
        if not img.exists():
            print(f"  [warn] {qid} 产出图缺失: {img}", file=sys.stderr)
            with lock:
                counters["fail"] += 1
            return
        t0 = time.time()
        try:
            if v60:
                row = score_t2i_v60(q, encode_image(img, args.max_edge), args)
            elif v53:
                neg_idx = negative_critical_idx(q, cmap)
                def_idx = defining_check_idx(q, cmap)
                row = score_t2i_v53(q, encode_image(img, args.max_edge),
                                    args, neg_idx, def_idx)
            else:
                row = score_t2i(q, encode_image(img, args.max_edge), args)
        except Exception as e:  # noqa: BLE001
            print(f"  [error] {qid}: {e}", file=sys.stderr)
            with lock:
                counters["fail"] += 1
                if v60:
                    fail_row = {"qid": qid, "task": "t2i",
                                "schema": f"v6.0-{v60}",
                                "judge_model": args.model,
                                "image_model": _derive_source(args),
                                "level": (q or {}).get("level"),
                                "fail": True, "error": str(e)[:500]}
                    rows.append(fail_row)
                    with out_path.open("a", encoding="utf-8") as fout:
                        fout.write(json.dumps(fail_row, ensure_ascii=False) + "\n")
            return
        with lock:
            rows.append(row)
            with out_path.open("a", encoding="utf-8") as fout:
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
        if v60:
            print(f"[{i}/{len(responses)}] {qid} -> align "
                  f"{row.get('alignment_score')} / qual {row.get('quality_score')}"
                  f" / aes {row.get('aesthetic_score')} "
                  f"({time.time() - t0:.0f}s)", flush=True)
        else:
            key = ("fidelity_score" if v53 else "total")
            print(f"[{i}/{len(responses)}] {qid} -> {row.get(key)} "
                  f"(total {row['total']}) ({time.time() - t0:.0f}s)", flush=True)

    jobs = list(enumerate(responses[: args.limit or None], 1))
    workers = max(1, getattr(args, "workers", 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_job, jobs))
    n_fail = counters["fail"]
    rows.sort(key=lambda r: str(r.get("qid")))

    rep = aggregate(rows, v53=v53, v60=v60)
    rep["judge_fail"] = n_fail
    rep_path = out_path.with_suffix(".report.json")
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n=== t2i 判分完成 ===")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"逐题分数 -> {out_path}\n报表 -> {rep_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode")
    p = sub.add_parser("score", help="t2i 赛道判分（默认子命令）")
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--responses", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--v60", choices=["V2"], default=None,
                    help="v6.0 判分协议：读 judge_prompt_gen_v6.0_V2.md 为模板"
                         "（只填 gen_prompt）")
    p.add_argument("--source", default=None,
                   help="v6.0：图源标签（bagel / 生图模型名；默认从 responses "
                        "文件名推导），写入行字段 image_model 与输出文件名")
    pb = sub.add_parser("baseline",
                        help="判官能力基线（计数/中文OCR/镜像，PIL 确定性探针）")
    pb.add_argument("--n", type=int, default=3, help="每类探针张数（默认 3）")
    pb.add_argument("--seed", type=int, default=42)
    for x in (ap, p, pb):
        x.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
        x.add_argument("--model", default=DEFAULT_MODEL)
        x.add_argument("--api-key", default="")
        x.add_argument("--max-edge", type=int, default=1024)
        x.add_argument("--max-tokens", type=int, default=JUDGE_CTX_LEN,
                       help="judge 生成预算上限（实际按逐题剩余上下文钳制）")
        x.add_argument("--think", action="store_true",
                       help="启用 thinking（chat_template_kwargs）")
        x.add_argument("--constraints", type=Path, default=None,
                       help="v5.3：合并硬约束清单（负向关键项封顶依据）")
        x.add_argument("--workers", type=int, default=1,
                       help="并发判分数（默认 1 串行）")
    sub.add_parser("dump", help="只物化 judge prompts")
    args = ap.parse_args()
    if args.mode == "dump":
        materialize_judge_prompts()
        return
    if args.mode == "baseline":
        run_baseline(args)
        return
    if args.mode is None:
        ap.error("请指定子命令：score 或 dump")
    run(args)


if __name__ == "__main__":
    main()
