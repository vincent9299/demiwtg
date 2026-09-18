# -*- coding: utf-8 -*-
"""生成 36 份独立任务书：14 个通用子树（任务A实例+任务B检索源）+ 22 个 IP 子树（仅任务B）。
数字实时取自最新 CSV；输出到 /Users/meng/qwenwork/任务派发/，文件名带执行者与队列序号。
"""
import csv, os

SRC = '/Users/meng/qwenwork'
OUT = os.path.join(SRC, '任务派发')
os.makedirs(OUT, exist_ok=True)

rows = list(csv.reader(open(os.path.join(SRC, 'taxonomy_tree_instances.csv'), encoding='utf-8-sig')))[1:]
inst = {r[0]: (r[1] if len(r) > 1 else '') for r in rows}
pathset = set(inst.keys())

def stats(prefix):
    sub = [p for p in pathset if p.startswith(prefix)]
    leaves = [p for p in sub if not any(q.startswith(p + ' / ') for q in pathset)]
    filled = sum(1 for p in leaves if inst[p].strip())
    d4 = sum(1 for p in sub if len(p.split(' / ')) == 4)
    return len(sub), len(leaves), filled, d4

# ---------------- 域内锚点源 ----------------
A = {
'食物': """- Wikimedia Commons：Category:Food、Category:Cuisines、Category:Dishes、Category:Ingredients + API
- 开源数据集：Food-101（101类菜品）、ISIA Food-500、Open Images V7（大量食物类）、ImageNet-1K（食物类）
- 图库：Pixabay/Unsplash 美食图库（API）""",
'建筑与基础设施': """- Wikimedia Commons：Category:Buildings、Category:Bridges、Category:Tunnels、Category:Dams、Category:Skyscrapers、Category:Roads
- ArchDaily 建筑媒体图库：`https://www.archdaily.com`（定向采集）
- 数据集：Open Images V7（Building/Bridge/Skyscraper 等）、ImageNet-1K""",
'交通工具': """- Wikimedia Commons：Category:Vehicles、Category:Cars、Category:Trains、Category:Ships、Category:Aircraft、Category:Bicycles
- 数据集：Stanford Cars、CompCars（综合汽车数据集）、Open Images V7 与 ImageNet-1K（大量车辆类）""",
'自然景观': """- Wikimedia Commons：Category:Landscapes、Category:Mountains、Category:Rivers、Category:Lakes、Category:Deserts、Category:Glaciers、Category:Waterfalls
- 数据集：Places365（场景识别365类）、SUN397、Open Images V7""",
'场景': """- Wikimedia Commons：按场景选分类（Category:Cities、Category:Beaches、Category:Forests、Category:Railway stations 等）
- 数据集：Places365、SUN397、ADE20K（场景解析，`https://groups.csail.mit.edu/vision/datasets/ADE20K/`）
- 备注：本子树机翻场景名多，实例=该场景的典型构成要素与具体场景名""",
'节日与符号': """- Wikimedia Commons：Category:Holidays、Category:Symbols、Category:Flags、Category:Emblems、Category:Logos
- 图库：Flickr/Pixabay/Unsplash 节日场景（API）""",
'属性与状态': """- 抽象概念，实例一律用**具体视觉载体**（如"破碎"→碎玻璃、破碗、开裂墙面；"潮湿"→结露玻璃、湿漉路面）
- 源以 Commons 对应具象分类 + LAION-5B / Conceptual 12M 关键词过滤为主""",
'材料与物质': """- Wikimedia Commons：Category:Materials、Category:Textiles、Category:Metals、Category:Wood、Category:Glass、Category:Ceramics、Category:Plastics
- 数据集：DTD 可描述纹理数据集（`https://www.robots.ox.ac.uk/~vgg/data/dtd/`）、Open Images V7""",
'时间数量与度量': """- Wikimedia Commons：Category:Clocks、Category:Calendars、Category:Measuring instruments、Category:Scales
- 备注：抽象概念多，实例=计时器/量具/计量场景等具体视觉实体""",
'文字与信息图形': """- Wikimedia Commons：Category:Writing systems、Category:Scripts、Category:Infographics、Category:Charts、Category:Diagrams、Category:Maps
- 备注：文字类实例=各文字系统的字样样本/书法形式""",
'声音': """- 声音本身无视觉形态，实例一律生成**声源及其视觉载体/场景**（如"雷声"→雷暴云、闪电场景；"鸟叫声"→鸣禽+林地场景）
- Wikimedia Commons：声源相关场景分类（Category:Thunderstorms、Category:Waterfalls、Category:Speakers 等）
- 音频数据集仅在类目明显偏音频采集时列：Freesound（`https://freesound.org`）、AudioSet（`https://research.google.com/audioset/`），并注明为音频数据""",
'文化艺术与媒介': """- WikiArt 艺术品图库：`https://www.wikiart.org`（定向采集）
- Wikimedia Commons：Category:Paintings、Category:Sculptures、Category:Photography、Category:Films
- 大都会开放藏品 `https://www.metmuseum.org` / 史密森尼开放藏品 `https://www.si.edu/openaccess`（均有 API）
- 数据集：WikiArt 数据集、Open Images V7""",
'知识与学科': """- ⚠ 本子树用户手改过、机翻错位极多（如"傍晚天空""V形菜园"挂在知识下），实例按字面最接近的可检索实体保守生成
- Wikimedia Commons：Category:Sciences、Category:Academic disciplines、Category:Research + 按解读后的实际类别选分类
- 兜底：LAION-5B / CC12M 关键词过滤""",
'组织机构与社会事件': """- Wikimedia Commons：Category:Protests、Category:Ceremonies、Category:Conferences、Category:Organizations
- 图库：Flickr/Pixabay/Unsplash 事件场景（API）""",
'IP': """- Wikimedia Commons 对应分类（版权内容的 Commons 页面有限，优先官方/公有领域条目）
- Fandom Wiki（`https://www.fandom.com`）：IP 词条与图片（定向采集；**版权风险高，只提供入口，采集合规由使用者自评**）
- 各 IP 官方网站
- 不硬凑通用数据集；如列，仅 LAION-5B 关键词过滤并注明版权自评""",
}

GENERIC_TAIL = """
另附通用兜底源（仅当域内源不足时补充，不要无脑全挂）：
- Wikimedia Commons API：`https://commons.wikimedia.org/w/api.php`
- Flickr API `https://www.flickr.com/services/api/`、Pixabay API `https://pixabay.com/api/docs/`
- LAION-5B（`https://laion.ai/laion-5b/`）、Conceptual 12M（`https://github.com/google-research-datasets/conceptual-12m`）：可关键词过滤图文对，适合挂在子树根
- 注：`taxonomy_tree_full.csv` 现有短 key 列（baidu|bing_images 等）不用管、不要动。"""

# ---------------- 通用模板（任务A+B） ----------------
GENERAL_TMPL = """# {agent} 任务 {seq}：「{short}」子树批量扩充（实例+检索源）

> 整段复制给 {agent} 执行。队列位置：第 {seq} 个；请完成上一个任务的交付与汇报后再开始本任务。

你要完成一个多模态图文数据标签体系的扩充任务。任务分两部分：**任务A 为叶子节点扩展实例**，**任务B 为类目节点补充检索源**。全程在本地文件系统操作，允许联网检索、允许启动子代理（sub agent）并行。请严格按本说明执行，**不要自由发挥文件格式和输出位置**。

## 0. 环境与红线（先读三遍）

1. 数据目录：`/Users/meng/qwenwork/`，原始文件：
   - `taxonomy_tree_instances.csv`：列 `node_path,instance清单`，21520 行（含表头）。
   - `taxonomy_tree_full.csv`：列 `node_path,source清单`，同样 21520 行。
2. 均为 **UTF-8 BOM 编码、LF 换行、逗号分隔的 CSV**；列表值用竖线 `|` 分隔。读写用 `encoding='utf-8-sig'`，写出用 `lineterminator='\\n'`。
3. **红线：绝对不要修改、覆盖、重命名两个原始文件。** 只产出新文件。
4. `node_path` 列任何内容不允许改动，行序不允许变动。
5. 工作目录：`/Users/meng/qwenwork/{key}work/`（自己创建）。
6. 交付文件（放 `/Users/meng/qwenwork/`，文件名必须完全一致）：
   - `{key}instances_enriched.csv`
   - `{key}full_enriched.csv`
   两个文件都是原始文件的**完整副本（21520 行）**，只允许修改本子树内的行。**最后一步合并必须执行并产出这两个 CSV，缺少交付文件视为任务未完成。**
7. 同目录还有其它任务的文件（`*_work/`、`*_enriched.csv`），**不要碰不属于你的任何文件**。
8. 开工前重读两个原始文件（用户可能已更新），以最新文件为准。

## 1. 任务范围（数字是死的，别自己重新数）

- 子树前缀：`{prefix}`
- 共 **{nodes} 个节点、{leaves} 个叶子**，其中 **{filled} 个叶子已有实例**（保留原实例并在其后追加）。
- 叶子判定：树中不存在以 `该路径 + " / "` 开头的其它节点。
- 四级类目共 **{d4} 个**（任务B 覆盖它们 + 子树根，共 {d4p1} 个节点）。
- 开工第一步：跑分片脚本并断言叶子数 == {leaves}，对不上立即停下来问用户。
{note}

## 2. 任务A：叶子实例扩展

### 2.1 实例是什么

实例 = 落在该叶子标签下、**具体、真实存在、可在图片搜索中检索到**的视觉实体名称：
- 类目型叶子：尽可能多的**具体成员**（规范中文名）。
- 具体型叶子：**变体/子类/品牌型号/著名个体**，8-25 个。
- 机翻杂项叶子：按字面最合理含义生成**具体可检索视觉实体**，8-20 个。
- 抽象概念叶子：生成该概念的**具体视觉载体**。
- 数量指引：宽泛类目 30-60；一般叶子 10-30；极具体 8-20。**宁缺毋滥，严禁编造名词。**
- 涉及人物群体、宗教、种族等一律使用中性尊重的规范表述。

### 2.2 数据规则

实例用中文；不重复叶子自身名称；不重复 `existing` 已有实例；追加在已有实例之后；输出数据里不得包含解释、注释、标记。

### 2.3 分片与并行（照抄执行）

**第一步**，运行分片脚本：

```python
# /Users/meng/qwenwork/{key}work/make_shards.py
import csv, json, os

SRC = '/Users/meng/qwenwork/taxonomy_tree_instances.csv'
PREFIX = '{prefix}'
OUT = '/Users/meng/qwenwork/{key}work'
os.makedirs(OUT + '/shards', exist_ok=True)

rows = list(csv.reader(open(SRC, encoding='utf-8-sig')))[1:]
inst = {{r[0]: (r[1] if len(r) > 1 else '') for r in rows}}
pathset = set(inst.keys())
sub = [p for p in inst if p.startswith(PREFIX)]
leaves = [p for p in sub if not any(q.startswith(p + ' / ') for q in pathset)]
assert len(leaves) == {leaves}, len(leaves)

TARGET, MAX = 60, 78
def leaf_groups(cat):
    clvs = [p for p in leaves if p == cat or p.startswith(cat + ' / ')]
    if len(clvs) <= MAX:
        return [(cat.split(' / ')[-1], clvs)]
    d5 = [p for p in sub if p.startswith(cat + ' / ') and len(p.split(' / ')) == 5]
    groups = []
    for c5 in d5:
        g = [p for p in clvs if p == c5 or p.startswith(c5 + ' / ')]
        if g: groups.append((c5.split(' / ')[-1], g))
    direct = [p for p in clvs if all(p != c5 and not p.startswith(c5 + ' / ') for c5 in d5)]
    if direct: groups.append((cat.split(' / ')[-1] + '(直属)', direct))
    out = []
    for name, g in groups:
        if len(g) <= MAX: out.append((name, g))
        else:
            for i in range(0, len(g), TARGET):
                out.append((name + f'(部分{{i//TARGET+1}})', g[i:i+TARGET]))
    return out

d4 = sorted([p for p in sub if len(p.split(' / ')) == 4])
shards, cur, cur_n, cur_cats = [], [], 0, []
for c in d4:
    for gname, g in leaf_groups(c):
        if cur and cur_n + len(g) > MAX:
            shards.append((cur, cur_cats)); cur, cur_n, cur_cats = [], 0, []
        cur.extend(g); cur_n += len(g); cur_cats.append(gname)
        if cur_n >= TARGET:
            shards.append((cur, cur_cats)); cur, cur_n, cur_cats = [], 0, []
if cur: shards.append((cur, cur_cats))

manifest = []
for i, (lvs, cats) in enumerate(shards):
    fn = f'shards/shard_{{i:02d}}.json'
    json.dump([{{"path": p, "existing": inst[p].strip()}} for p in lvs],
              open(os.path.join(OUT, fn), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    manifest.append({{"shard": fn, "n_leaves": len(lvs), "categories": cats}})
json.dump(manifest, open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print("shards:", len(shards), "total:", sum(len(s[0]) for s in shards))
```

**第二步**，每个分片交给一个子代理，每波**并行 6-8 个**，一波全部完成并校验后再发下一波。子代理提示词模板（替换 `##SHARD##`、`##N##`、`##CATS##`）：

```
你在为多模态图文数据的标签体系做实例扩展（写数据任务，需产出文件）。
对象：「{prefix}」子树，大量机翻标签，叶子需补充实例。
实例=落在该标签下、具体、真实存在、可在图片搜索中检索到的视觉实体：具体成员、变体/型号、相关物品与场景要素、抽象概念的具体视觉载体等。

输入：读取 /Users/meng/qwenwork/{key}work/shards/##SHARD##.json，每个元素为 {{"path": 叶子节点完整路径, "existing": 已有实例（可能为空）}}。共 ##N## 个叶子。涉及类目：##CATS##。

要求：
1. 数量指引：宽泛类目30-60；一般叶子10-30；具体型叶子列变体/型号8-25；杂项/抽象叶子8-20。
2. 严禁编造名词；以领域知识为主，拿不准可少量网络核实，不要逐条检索。
3. 不要重复 existing 已有实例，不要重复叶子自身名称。
4. 机翻怪名：解读为最合理的类别，按解读后含义生成规范中文实例；输出中不得包含解释、注释、标记。
5. 涉及人物群体、宗教、种族时一律使用中性尊重的规范表述。
6. 工作方式：按输入顺序分批处理，每批完成就把已完成条目合并写入输出文件，避免一次性输出丢失。

输出：写入 /Users/meng/qwenwork/{key}work/out_##SHARD##.json，格式
{{"leaves":[{{"path": 原路径,"instances":["实例1","实例2",...]}}]}}（ensure_ascii=False）。
##N## 个叶子必须全部出现、顺序与输入一致。写完后用 python 校验：JSON 可解析、路径数=##N##、路径与输入完全一致、叶内无重复。最后报告实例总数。
```

**第三步**，每个分片完成后立即校验（注意把分片输入路径拼上工作目录前缀）：

```python
import json
WORK = '/Users/meng/qwenwork/{key}work'
shard = json.load(open(WORK + '/shards/shard_XX.json', encoding='utf-8'))
out   = json.load(open(WORK + '/out_shard_XX.json', encoding='utf-8'))
assert [l['path'] for l in out['leaves']] == [s['path'] for s in shard], '路径不一致'
for l in out['leaves']:
    assert l['instances'] and len(set(l['instances'])) == len(l['instances'])
    assert all(isinstance(x, str) and x.strip() and '|' not in x for x in l['instances'])
```

## 3. 任务B：检索源（挂在四级类目上，不到叶子粒度）

### 3.1 覆盖范围与格式

为 **{d4} 个四级类目 + 子树根** 生成检索源，写入 `{key}full_enriched.csv` 新增第 3 列，列名必须为：`source详情(名称;URL;类型)`。
每条源格式：`名称;URL;类型`，多条用 `|` 连接。类型只能是：`搜索引擎` / `API` / `定向采集网站` / `数据集`。
原则：**宁缺毋滥，只列真正相关的源**；源尽量给 URL，开源数据集给名称+官网。
执行方式：按每批 30-40 个类目拆给子代理生成候选，**禁止子代理逐条联网检索**，候选随后统一 API 验证。

### 3.2 本子树域内锚点源（优先从这里选配）

{anchors}{generic_tail}

### 3.3 Wikimedia Commons 分类必须验证

每个类目给 1-2 个候选英文 Commons 分类，然后用 API 批量验证、只保留真实存在的：

```python
# /Users/meng/qwenwork/{key}work/verify_commons.py
import json, urllib.request, urllib.parse, time
API = 'https://commons.wikimedia.org/w/api.php'
candidates = [...]  # 所有候选分类名（不带 Category: 前缀），去重
existing = {{}}
for i in range(0, len(candidates), 50):
    batch = candidates[i:i+50]
    params = {{'action':'query','titles':'|'.join('Category:'+c for c in batch),
              'prop':'categoryinfo','format':'json','formatversion':'2'}}
    url = API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={{'User-Agent':'TaxonomyResearch/1.0'}})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    for page in data.get('query',{{}}).get('pages',[]):
        t = page.get('title','')
        name = t[len('Category:'):] if t.startswith('Category:') else t
        ok = 'missing' not in page
        existing[name] = {{'exists': ok, 'pages': page.get('categoryinfo',{{}}).get('pages',0) if ok else 0}}
    time.sleep(0.3)
print(sum(1 for v in existing.values() if v['exists']), '/', len(existing))
```

本环境 Commons API 偶发 SSL 超时，失败要退避重试并把结果落盘缓存。每个类目选"存在且页面数最大"的候选；每个四级类目源条目 2-5 条，不要注水。最终整理成 `{key}work/source_rows.json`：`{{完整节点路径: "名称;URL;类型|..."}}`，共 **{d4p1} 个条目**（{d4} 类目 + 子树根）。

## 4. 合并出最终文件（照抄执行，漏掉这步视为未完成）

所有分片校验通过后运行：

```python
# /Users/meng/qwenwork/{key}work/merge.py
import csv, json, os, glob

WORK = '/Users/meng/qwenwork/{key}work'
SRC = '/Users/meng/qwenwork'

new_inst = {{}}
for f in sorted(glob.glob(WORK + '/out_shard_*.json')):
    for l in json.load(open(f, encoding='utf-8'))['leaves']:
        new_inst[l['path']] = [x.strip() for x in l['instances'] if x.strip()]
assert len(new_inst) == {leaves}, len(new_inst)

source_rows = json.load(open(WORK + '/source_rows.json', encoding='utf-8'))

rows = list(csv.reader(open(SRC + '/taxonomy_tree_instances.csv', encoding='utf-8-sig')))
header, body = rows[0], rows[1:]
out = []
for r in body:
    p = r[0]; old = (r[1].strip() if len(r) > 1 else '')
    if p in new_inst:
        seen = dict.fromkeys([e for e in old.split('|') if e] + new_inst[p])
        out.append([p, '|'.join(seen)])
    else:
        out.append(r)
with open(SRC + '/{key}instances_enriched.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f, lineterminator='\\n'); w.writerow(header); w.writerows(out)

rows = list(csv.reader(open(SRC + '/taxonomy_tree_full.csv', encoding='utf-8-sig')))
header, body = rows[0], rows[1:]
out = []
for r in body:
    base = r + [''] * (2 - len(r)) if len(r) < 2 else r
    out.append(base + [source_rows.get(r[0], '')])
with open(SRC + '/{key}full_enriched.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f, lineterminator='\\n')
    w.writerow(header + ['source详情(名称;URL;类型)']); w.writerows(out)
print('merged leaves:', len(new_inst), '| source rows:', sum(1 for v in source_rows.values() if v))
```

## 5. 交付前校验（全部通过才算完成）

```python
import csv
WD = '/Users/meng/qwenwork/'
for fn, orig in [('{key}instances_enriched.csv','taxonomy_tree_instances.csv'),
                 ('{key}full_enriched.csv','taxonomy_tree_full.csv')]:
    new = list(csv.reader(open(WD+fn, encoding='utf-8-sig')))
    old = list(csv.reader(open(WD+orig, encoding='utf-8-sig')))
    assert len(new) == len(old) == 21520
    assert all(n[0] == o[0] for n, o in zip(new[1:], old[1:]))
# {leaves} 个叶子必须全部有实例、且原有 {filled} 个已填叶子的实例保留在开头
# 子树之外的行必须与原文件完全一致（实例文件逐行比对）
# {key}full_enriched.csv 第3列只在本子树的 {d4p1} 个节点非空
```

## 6. 汇报要求

每完成一波分片简短汇报进度；全部完成后汇报：叶子覆盖率（应为 {leaves}/{leaves}）、新增实例总数、源条目数（应为 {d4p1}）、校验结果。数字对不上立即停下来问用户。
"""

# ---------------- IP 模板（仅任务B） ----------------
IP_TMPL = """# {agent} 任务 {seq}：「{short}」IP 子树检索源补充（仅任务B，实例已完成）

> 整段复制给 {agent} 执行。队列位置：第 {seq} 个；请完成上一个任务的交付与汇报后再开始本任务。

你要为一个多模态图文数据标签体系补充检索源。该子树的**实例已全部完成且质量达标（平均每叶 47 条），不要做任何实例相关的工作，也不要产出实例文件**。全程在本地文件系统操作，允许联网检索、允许启动子代理并行。请严格执行，**不要自由发挥文件格式和输出位置**。

## 0. 环境与红线

1. 数据目录：`/Users/meng/qwenwork/`，原始文件 `taxonomy_tree_instances.csv` / `taxonomy_tree_full.csv`（各 21520 行，UTF-8 BOM + LF，列表值 `|` 分隔；读写用 `encoding='utf-8-sig'`、`lineterminator='\\n'`）。
2. **红线：不修改两个原始文件。** `node_path` 列与行序不允许变动。
3. 工作目录：`/Users/meng/qwenwork/{key}work/`（自己创建）。
4. 交付文件：`/Users/meng/qwenwork/{key}full_enriched.csv` —— 原始 `taxonomy_tree_full.csv` 的**完整副本（21520 行）**，新增第 3 列，只在本子树节点非空。**必须产出该文件，否则视为未完成。**
5. 不要碰其它任务的文件（`*_work/`、`*_enriched.csv`）。

## 1. 任务范围（数字是死的）

- 子树前缀：`{prefix}`
- 子树共 **{nodes} 个节点、{leaves} 个叶子（实例已全部完成，勿动）**；四级类目 **{d4} 个**。
- 检索源覆盖 **{d4} 个四级类目 + 子树根，共 {d4p1} 个节点**。
- 开工先断言：树中该前缀下四级类目数 == {d4}，叶子数 == {leaves}，对不上立即停下来问用户。

## 2. 检索源生成

### 2.1 格式

写入第 3 列，列名必须为：`source详情(名称;URL;类型)`。每条源格式：`名称;URL;类型`，多条用 `|` 连接。类型只能是：`搜索引擎` / `API` / `定向采集网站` / `数据集`。
原则：**宁缺毋滥，只列真正相关的源**；源尽量给 URL，数据集给名称+官网。每个节点 2-4 条，不要注水。
可按每批 10-15 个类目拆给子代理生成候选，**禁止子代理逐条联网检索**，候选随后统一 API 验证。

### 2.2 IP 域锚点源（优先从这里选配）

{anchors}{generic_tail}

### 2.3 Wikimedia Commons 分类必须验证

每个类目给 1-2 个候选英文 Commons 分类，用 API 批量验证（`action=query&titles=Category:xxx&prop=categoryinfo`，每批最多 50 个，带退避重试与落盘缓存），只保留真实存在的；选"存在且页面数最大"的候选。
最终整理成 `{key}work/source_rows.json`：`{{完整节点路径: "名称;URL;类型|..."}}`，共 **{d4p1} 个条目**。

## 3. 合并出交付文件（照抄执行）

```python
# /Users/meng/qwenwork/{key}work/merge_sources.py
import csv, json

WORK = '/Users/meng/qwenwork/{key}work'
SRC = '/Users/meng/qwenwork'
source_rows = json.load(open(WORK + '/source_rows.json', encoding='utf-8'))
assert len(source_rows) == {d4p1}, len(source_rows)

rows = list(csv.reader(open(SRC + '/taxonomy_tree_full.csv', encoding='utf-8-sig')))
header, body = rows[0], rows[1:]
out = []
for r in body:
    base = r + [''] * (2 - len(r)) if len(r) < 2 else r
    out.append(base + [source_rows.get(r[0], '')])
with open(SRC + '/{key}full_enriched.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f, lineterminator='\\n')
    w.writerow(header + ['source详情(名称;URL;类型)']); w.writerows(out)
print('source rows:', sum(1 for r in out if r[2].strip()))
```

## 4. 交付前校验（全部通过才算完成）

```python
import csv
WD = '/Users/meng/qwenwork/'
new = list(csv.reader(open(WD+'{key}full_enriched.csv', encoding='utf-8-sig')))
old = list(csv.reader(open(WD+'taxonomy_tree_full.csv', encoding='utf-8-sig')))
assert len(new) == len(old) == 21520
assert all(n[0] == o[0] for n, o in zip(new[1:], old[1:]))
# 第3列只在本子树的 {d4p1} 个节点非空、格式合规（名称;URL;类型，类型∈四类）
# 原始两个文件未被改动
```

## 5. 汇报要求

完成后汇报：源条目数（应为 {d4p1}）、Commons 已验证分类数、校验结果。数字对不上立即停下来问用户。
"""

# ---------------- 任务清单 ----------------
G = '通用分类标签'
IP = 'IP 分类标签'
R = '融合世界标签体系'

GENERAL = [  # (执行者, 短名, key, 备注)
    ('HY3', '食物', 'food_', ''),
    ('HY3', '建筑与基础设施', 'infra_', ''),
    ('HY3', '交通工具', 'vehicles_', ''),
    ('HY3', '自然景观', 'landscape_', ''),
    ('HY3', '场景', 'scenes_', ''),
    ('HY3', '知识与学科', 'knowledge_', '- ⚠ 用户手改过本子树、机翻错位极多，开工必读最新文件，实例按字面保守解读。'),
    ('HY3', '材料与物质', 'materials_', ''),
    ('HY3', '时间数量与度量', 'measures_', '- 抽象类目多，实例一律用具体视觉载体（计时器/量具等）。'),
    ('豆包', '声音', 'sound_', '- 特殊：声音无视觉形态，实例=声源及其视觉载体/场景（见域内锚点）。'),
    ('豆包', '文化艺术与媒介', 'arts_', ''),
    ('豆包', '文字与信息图形', 'textinfo_', ''),
    ('豆包', '属性与状态', 'attrs_', '- 抽象类目多，实例一律用具体视觉载体。'),
    ('豆包', '组织机构与社会事件', 'orgs_', ''),
    ('豆包', '节日与符号', 'festivals_', ''),
]

IP_LIST = [  # (执行者, 短名, key)
    ('HY3', '内容作品', 'ip_content_'), ('HY3', '艺术与文物', 'ip_artifact_'),
    ('HY3', '非遗与传统手工艺', 'ip_heritage_'), ('HY3', '组织机构', 'ip_org_'),
    ('HY3', '美食与饮食文化', 'ip_food_'), ('HY3', '城市与地域', 'ip_city_'),
    ('HY3', '历史与文化遗产', 'ip_history_'), ('HY3', '自然生态与动物', 'ip_nature_'),
    ('HY3', '武器', 'ip_weapon_'), ('HY3', '著名载具', 'ip_vehicle_'),
    ('豆包', '品牌', 'ip_brand_'), ('豆包', '地标', 'ip_landmark_'),
    ('豆包', '真人与人物', 'ip_person_'), ('豆包', '教育与科普', 'ip_edu_'),
    ('豆包', '虚构角色', 'ip_fiction_'), ('豆包', '吉祥物与形象', 'ip_mascot_'),
    ('豆包', '赛事', 'ip_event_'), ('豆包', '电竞', 'ip_esports_'),
    ('豆包', '潮玩互动', 'ip_toy_'), ('豆包', '乐园节庆', 'ip_park_'),
    ('豆包', '科技与数字', 'ip_tech_'), ('豆包', '学科与知识', 'ip_subject_'),
]

# 用户决定：36 个任务全部交给 HY3 执行，队列按单一顺序 01-36 编排
GENERAL = [('HY3', s, k, n) for _, s, k, n in GENERAL]
IP_LIST = [('HY3', s, k) for _, s, k in IP_LIST]

index = []
seq = {'HY3': 0, '豆包': 0}
for agent, short, key, note in GENERAL:
    prefix = f'{R} / {G} / {short}'
    nodes, leaves, filled, d4 = stats(prefix)
    seq[agent] += 1
    body = GENERAL_TMPL.format(agent=agent, seq=seq[agent], short=short, key=key, prefix=prefix,
                               nodes=nodes, leaves=leaves, filled=filled, d4=d4, d4p1=d4 + 1,
                               anchors=A[short], generic_tail=GENERIC_TAIL,
                               note=(f'\n**特别注意**\n{note}' if note else ''))
    fn = f'{agent}_{seq[agent]:02d}_{short}_任务书_实例+源.md'
    open(os.path.join(OUT, fn), 'w', encoding='utf-8').write(body)
    index.append((agent, seq[agent], short, fn, f'{leaves}叶/{d4}类目'))

for agent, short, key in IP_LIST:
    prefix = f'{R} / {IP} / {short} IP'
    nodes, leaves, filled, d4 = stats(prefix)
    seq[agent] += 1
    body = IP_TMPL.format(agent=agent, seq=seq[agent], short=short, key=key, prefix=prefix,
                          nodes=nodes, leaves=leaves, d4=d4, d4p1=d4 + 1,
                          anchors=A['IP'], generic_tail=GENERIC_TAIL)
    fn = f'{agent}_{seq[agent]:02d}_{short}IP_任务书_仅检索源.md'
    open(os.path.join(OUT, fn), 'w', encoding='utf-8').write(body)
    index.append((agent, seq[agent], short + ' IP', fn, f'仅源/{d4}类目'))

with open(os.path.join(OUT, '00_任务索引.md'), 'w', encoding='utf-8') as f:
    f.write('# 任务派发索引（按执行者队列顺序）\n\n')
    seen = []
    for agent, *_ in index:
        if agent not in seen: seen.append(agent)
    for ag in seen:
        f.write(f'## {ag} 队列\n\n| 序号 | 子树 | 规模 | 文件 |\n|---|---|---|---|\n')
        for agent, s, short, fn, scale in index:
            if agent == ag:
                f.write(f'| {s:02d} | {short} | {scale} | `{fn}` |\n')
        f.write('\n')

print(f'生成完成: {len(index)} 份任务书 -> {OUT}')
for agent, s, short, fn, scale in index:
    print(f'  {agent} #{s:02d} {short:12} {scale:14} {fn}')
