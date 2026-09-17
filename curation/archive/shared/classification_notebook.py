"""One classification projection shared by the overview and case headers."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from functools import lru_cache
from pipeline import ROOT, STATE, read
from review import esc
from case_input_details import TAGS, scene_data

LEVELS={'direct':'direct｜直接应用','conditional':'conditional｜条件选择','relational':'relational｜关系／过程应用','compositional':'compositional｜多知识组合'}
CSS='<style>.classification-scroll{overflow-x:auto;max-width:100%;margin:12px 0}.classification-grid{border-collapse:collapse!important;table-layout:auto!important;width:2600px!important;font:14px/1.6 sans-serif}.classification-grid th,.classification-grid td{width:auto!important;min-width:115px;max-width:360px;vertical-align:top;border:1px solid #ccd;padding:8px;overflow-wrap:anywhere}.classification-grid th{background:#edf3f8}.classification-grid td:nth-child(3){min-width:270px}.classification-guide{font:16px/1.7 sans-serif}.classification-guide table{border-collapse:collapse}.classification-guide th,.classification-guide td{border:1px solid #ccd;padding:8px;text-align:left;vertical-align:top}</style>'
HEADERS=['题号／情境','概念','Taxonomy path／建议归属','主知识领域','知识内容（可多选）','题型','知识应用层次','主对比的参考知识','可查看的资料条件','编辑原图','场景复杂度来源','组合主类','前提类别','知识来源属性']

@lru_cache(maxsize=1)
def cases_all():
    return [(batch,c) for batch in ['pilot','nonobject_v1','scene_v1'] for c in read(STATE/batch/'cases.json')['cases']]

@lru_cache(maxsize=1)
def legacy_meta():return {x['question_id']:x['taxonomy_record'] for x in read(STATE/'nonobject_v1/original_case_taxonomy.json')}

@lru_cache(maxsize=3)
def jobs_for(batch):return [json.loads(l) for l in (STATE/batch/'jobs.jsonl').read_text().splitlines() if l.strip()]

# All cases' explicit premises reviewed from their frozen prompts; no prompt edits.
PREMISES={
'dev_direct_pignose':['年龄/生长阶段','环境场所','视角/暴露'],
'dev_direct_youmiank':['阶段/时刻','视角/暴露'],
'dev_direct_cashew':['年龄/生长阶段','阶段/时刻','视角/暴露'],
'dev_conditional_tapir':['年龄/生长阶段'],
'dev_conditional_puffin':['年龄/生长阶段','阶段/时刻'],
'dev_conditional_lady_larva':['年龄/生长阶段'],
'dev_relational_pangolin':['年龄/生长阶段','数量/编组','使用状态'],
'dev_relational_gargoyle':['使用状态','环境场所','阶段/时刻'],
'dev_relational_lady_food':['使用状态'],
'dev_compositional_pignose_sexes':['年龄/生长阶段','变体/子类型','数量/编组','视角/暴露'],
'dev_conditional_nasa_argon_oxygen_18':['变体/子类型','数值/阈值（补充）','规约版本（补充）','视角/暴露'],
'dev_conditional_nasa_argon_oxygen_21':['变体/子类型','数值/阈值（补充）','规约版本（补充）','视角/暴露'],
'dev_nonobject_tir':['环境场所','数值/阈值（补充）','视角/暴露'],
'dev_nonobject_rainbow':['环境场所','数量/编组','视角/暴露'],
'dev_nonobject_wave':['变体/子类型','阶段/时刻','使用状态','视角/暴露'],
'dev_nonobject_capillary':['数量/编组','环境场所','使用状态','数值/阈值（补充）','视角/暴露'],
'dev_scene_ecology':['年龄/生长阶段','数量/编组','使用状态','环境场所','视角/暴露'],
'dev_scene_drainage':['使用状态','数量/编组','环境场所','阶段/时刻','视角/暴露']}

def row_values(c,index,batch):
    m=c.get('taxonomy_record') or legacy_meta()[c['question_id']]
    audit=c.get('classification_audit')
    content=audit['content'] if audit else TAGS[c['question_id']][0]
    d=scene_data(c);scene=d['scene_types'];combo=d['combo_type']
    # Do not count a single contact relation as a multi-link interaction chain.
    if c['question_id'] in ['dev_relational_pangolin','dev_relational_lady_food']:scene=[]
    if c['question_id']=='dev_scene_ecology':scene=['多实例对比','实体密度'];
    if c['question_id']=='dev_nonobject_tir':combo='光学传播'
    paths='；'.join(m.get('taxonomy_paths',[])) or '无现有挂载'
    if m.get('suggested_semantic_path'):paths+='；建议：'+m['suggested_semantic_path']
    js=[j for j in jobs_for(batch) if j['question_id']==c['question_id']]
    main='multimodal' if batch=='pilot' else 'text';job=next(j for j in js if j['condition']==main)
    refs=sum(im['role']=='retrieval_reference' for im in job['images']);edit=sum(im['role']=='edit_source' for im in job['images'])
    modes={'baseline':'无额外资料','text':'文本','image':'图片','multimodal':'文本＋图片','explicit_target':'显式目标（诊断）'}
    conditions='／'.join(dict.fromkeys(modes[j['condition']] for j in js))
    origins={s.get('origin')=='existing_local_clean_document' for s in c['sources']}
    origin='库内＋外部复核' if len(origins)>1 else ('库内已有文档' if True in origins else '外部补充')
    return [f'{index}．{c["concept"]}',m.get('concept_name',c['concept']),paths,c['domain'],content,
            't2i｜文生图' if c['task']=='t2i' else 'edit｜图像编辑',LEVELS[c['application_level']],
            '文本＋图片（'+str(refs)+'张参考）' if refs else '文本（无知识图片）',conditions,
            str(edit)+'张；两组共同输入' if edit else '无', '、'.join(scene) or '无突出复杂来源',combo or '不适用',
            '、'.join(PREMISES[c['question_id']]),origin]

def table(rows):
    return CSS+'<div class="classification-scroll"><table class="classification-grid"><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in HEADERS)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'

def case_row(c):
    i,batch=next((i,b) for i,(b,x) in enumerate(cases_all(),1) if x['question_id']==c['question_id'])
    return table([row_values(c,i,batch)])

def md_table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(r)+' |\n' for r in rows)

def original_enum(section):
    text=(ROOT/'benchmark/t2i/prompts/synthesize_prompt_gen_v6.0.md').read_text()
    part=text.split('### '+section,1)[1].split('\n### ',1)[0]
    rows=[[v.strip() for v in line.strip().strip('|').split('|')] for line in part.splitlines() if line.startswith('|')]
    return [r[:2] for r in rows[2:]]

def guide():
    domains=[x['name'] for x in read(ROOT/'datasets/demiwtg/meta/taxonomy.json')['tree']['children']]
    assert len(domains)==29
    s='# 出题分类设计与阅读说明\n\n这几组维度分别描述**考什么、怎么应用、怎么呈现、提供什么资料**，不是把题目塞进一个互斥分类。领域允许交叉，六类知识内容允许多标签；本轮不为覆盖率凑题。以下为当前展示审核，原题、原请求和冻结标签不被重写。\n\n'
    s+=md_table(['维度','含义与用法'],[
      ['29知识领域','知识属于哪个主题；每题记录一个主域用于分层。Taxonomy path保留库内导航出处，错误或交叉挂载不直接决定主域。'],
      ['概念＋Taxonomy path','概念是可复用知识的锚点，不限物体；路径说明概念在库内哪里。建议新增／调整单列，不伪装已入库。'],
      ['6类知识内容','知识陈述描述什么；允许同题使用多类。'],
      ['题目类型','t2i：从任务和可选资料生成新图；edit：对给定原图做修改，还要检查非目标内容的保持。'],
      ['知识应用层次','如何从条件与资料得到可见结果；四类主层次单选，允许内部包含其他操作，不代表从易到难的等级。'],
      ['参考知识／资料条件','额外给模型的是文本、图片、图文还是无资料；总表主对比列指当前右侧输出，同时列出其他已运行条件。'],
      ['场景复杂度来源','场景的联合约束来自哪里，例如多实例对比、环境作用。简单场景也可能知识很难。'],
      ['组合主类','概念如何关联，例如生态互动、因果；选一个主关系。单独的物体展示可不适用。'],
      ['前提类别','题面明确锁定哪些条件；有前提不等于答案已经给出，也不等于条件选择型题。'],
      ['知识来源属性','库内已有文档、外部补充或混合；这与资料是文字还是图片是两回事。编辑原图也不是知识参考图。']])
    s+='\n## 知识应用层次：英文标签是什么意思\n\n'+md_table(['标签','含义','当前例子'],[
      ['direct｜直接应用','调用概念的相关知识直接决定画面；不要求额外选择不同状态或组合规则。直接不等于简单／模型已知。','第1题：猪鼻龟的鼻部与鳍状肢。'],
      ['conditional｜条件选择','按题给年龄、季节、阈值等选择适用知识或分支，再画结果。','第4题：一周龄貘；第11题：氧含量选标准表行。'],
      ['relational｜关系／过程应用','把知识落实为对象之间的关系，或过程阶段的可见结果；不只是各画一个对象。','第8题：排水构件与水流；第9题：捕食关系。当前过程覆盖仍弱。'],
      ['compositional｜多知识组合','同一画面同时选择、应用多条约束，检查它们能否共同成立。不是物体多就自动归此类。','第17题：成幼体形态＋两者的食物关系。']])
    s+='\n## 29知识领域\n\n来自当前权威taxonomy的29个顶层领域；不声称互斥，也不要求本轮18题均覆盖：\n\n'+'；'.join(domains)+'。\n'
    s+='\n## 6类知识内容\n\n'+md_table(['类别','含义'],[
      ['特征与结构','形状、部件、内部构造及其布局。'],['属性与状态','颜色、材质等属性以及给定状态下的外观。'],['关系与组织','对象或部分之间的位置、依赖、成员和生态关系。'],['过程与变化','随时间／阶段发生的变化；单个终态不自动算充分考查过程。'],['功能与机制','东西起什么作用、通过什么机制产生结果；只画相关对象不代表考了机制。'],['规则与约定','标准、制度、文化符号等人为约定；不把所有自然定律也放进此类。']])
    for title,section in [('场景复杂度来源','场景复杂度来源'),('组合类型','组合类型'),('前提类别','前提类别')]:
      rows=original_enum(section)
      if title=='前提类别':rows += [['数值/阈值（本次补充）','锁定折射率、角度、浓度、半径等数值；与个数／编组不同。'],['规约版本（本次补充）','锁定标准的版本与表项，不把版本误标为画面历史时刻。']]
      s+='\n## '+title+'\n\n'+md_table(['类别','含义'],rows)
    s+='\n## 参考知识与图片角色\n\n'+md_table(['条件／角色','含义'],[
      ['baseline｜无额外资料','仍有任务文本；edit仍有编辑原图。不是模型没有预训练知识。'],['text｜文本','只加文字知识，不加知识图片。'],['image｜图片','只加参考图片及必要角色说明，不加知识正文；仍保留任务文本。'],['multimodal｜文本＋图片','同时加文字和参考图；有图不代表图已支持考点，更不代表图文互补已成立。'],
      ['edit_source｜编辑原图','待修改场景，两组共同输入；不是额外检索知识。'],['retrieval_reference｜知识参考图','用于提供形态等证据；不是本题目标图，也不要求照抄构图。']])
    s+='\n第1—12题主对比为无资料／图文；第13—18题为无资料／文本。文字主导、图像主导、图文互补属于**证据支持作用**，需逐判据审核，不能只按文件格式自动贴标签；本轮总表不把图文输入一律称为图文互补。\n\n原prompt中的跳类型、弱项等尚未逐题核验，本页不补造标签，也不恢复旧L1/L2/L3门槛。当前知识应用分类不提供多跳难度分数。知识正确、执行正确与画质分别判断。\n\n## 18题分类总表\n\n表格可横向滚动。下方每个case第一行复用这张表对应的数据；本次补全了前提标注，并把单次接触与真正的连续交互链区分开。原标签及差异仍可在详情中追溯。\n'
    return s

def overview_table():return table([row_values(c,i,b) for i,(b,c) in enumerate(cases_all(),1)])
