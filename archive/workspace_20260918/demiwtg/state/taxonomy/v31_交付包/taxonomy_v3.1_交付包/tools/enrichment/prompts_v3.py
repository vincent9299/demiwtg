# -*- coding: utf-8 -*-
"""v3 prompts: context + instance definition + granularity + few-shot example."""

SYSTEM_EN = ('You are enriching "Fused World", a world-knowledge taxonomy used for tagging '
             'multimodal image-text collections. Your job is to supply concrete, real, taggable '
             'instances for leaf categories. Output strict JSON only.')

PROMPT_EN_V3 = '''Task: add 25 new instances for one leaf node of the taxonomy.

Taxonomy path: {path}
Existing instances (already covered): {inst}

Rules:
1. No repeats: skip anything literally or semantically identical to the path name or to any existing instance.
2. What an instance is: a concrete member of this category that could be tagged on a real photo or document.
   - If the node is a broad class, give concrete subtypes or famous real exemplars.
   - If the node is already narrow, give specific named real-world exemplars.
3. Form: each item is a short noun phrase (about 1-6 words). No sentences, no numbering, no bracketed notes.
4. World coverage: draw from many regions (Europe, East Asia, South & Southeast Asia, the Middle East, Africa, the Americas, Oceania); do not pile most items into one country.
5. Truth: only real, verifiable things; no invented combinations, no generic filler.
6. Category discipline: every item must itself be an instance of this category, not a part, accessory, tool, ingredient, or attribute related to it. Self-check: if the node is a type of restaurant, then menu items like "grilled beef tongue" or "dipping sauce" are not restaurants and must never appear.
7. Language: English only; no Chinese or other non-English characters anywhere.
8. Output exactly one JSON array of 25 strings, nothing else.

Example (shows format and granularity only; never reuse these items):
Path: ... / Animals / Birds / Birds of Prey / Eagles
Existing: golden eagle, bald eagle
Output: ["harpy eagle","Philippine eagle","martial eagle","wedge-tailed eagle","steppe eagle","crowned eagle","lesser spotted eagle","eastern imperial eagle"]'''

SYSTEM_CN = ('你在为「融合世界」世界知识标签体系补充实例。该体系用于多模态图文数据的标注，'
             '叶子节点需要具体、真实、可标注的实例。只输出严格 JSON。')

PROMPT_CN_V3 = '''任务：为标签树的一个叶子节点新增 25 个实例。

分类路径：{path}
已有实例（已覆盖）：{inst}

规则：
1. 不重复：与路径名或已有实例在字面或语义上相同的条目都不要。
2. 什么是实例：该类目下具体、可标注的成员——真实照片或文档里能被打上这个标签的东西。
   - 节点是宽泛类别时，给出具体子类或著名的真实个例；
   - 节点已经很窄时，给出有名字的真实个例（具体地点、菜品、作品、人物等）。
3. 形式：每条为简短名词短语（约1~6个词），不要句子、不要编号、不要括号注释。
4. 世界覆盖：跨地区取材（欧洲、东亚、南亚与东南亚、中东、非洲、美洲、大洋洲），不要堆在同一个国家。
5. 真实性：只要真实、可查证的事物，不造词，不用泛化填充。
6. 类目纪律：每条本身必须是该类目下的实例，而不是它的部件、配件、工具、原料或属性。自检：若节点是某类餐厅，则「烤牛舌」「干碟」这类菜品/配料不是餐厅，绝不能出现。
7. 只输出一个包含25个字符串的 JSON 数组，不要任何其他文字。

示例（仅示意格式与粒度，切勿复用其中条目）：
路径：... / 动物 / 鸟类 / 猛禽 / 鹰
已有：金雕、白头海雕
输出：["角雕","菲律宾鹰","草原雕","楔尾雕","冠雕","蛇雕","花雕","白肩雕"]'''
