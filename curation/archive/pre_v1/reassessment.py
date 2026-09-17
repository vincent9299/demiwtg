"""Read-only presentation of assistant curation review; never changes human decisions."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import base64
import json
from pathlib import Path
from collections import Counter
from curation.core import DEFAULT_RUN, ROOT, facts, read, digest, safe_file


def build(run=DEFAULT_RUN, output=None):
    run=Path(run)
    bundle=read(run/'reassessment_v1.json')
    if bundle['author_type']!='assistant' or bundle['status']!='proposal':
        raise ValueError('Only assistant proposals belong in this report')
    current={f['fact_id']:f for f in facts(run)}
    rows=bundle['records']
    if len({r['fact_id'] for r in rows})!=len(rows) or set(current)!=set(r['fact_id'] for r in rows):
        raise ValueError('Review must cover every candidate exactly once')
    for r in rows:
        if digest(current[r['fact_id']])!=r['fact_sha256']:raise ValueError('Stale candidate review')
    cells=[]
    def md(s, attachments=None):
        c=dict(cell_type='markdown',id=f'reassessment-{len(cells)}',metadata={},source=s.splitlines(True))
        if attachments:c['attachments']=attachments
        cells.append(c)
    counts=Counter(r['action'] for r in rows)
    md('''# Curation：27 条知识复核与 5 张完整候选卡

**打开直接读，无需运行 cell；这里没有保存人工判断的按钮。** 全部结论是助手建议，原始模型输出和你的人工标注未被覆盖。本次不出正式题、不调用生成模型、不新增核心集成员。

复核范围：逐条对照 27 条知识的本地引文和视觉设想；对重点卡和明显疑点补查外部资料。**没有把 27 条都当作完成外部事实认证。** 5 张卡是优先讨论的修订候选，不是 5 条已验收知识。

阅读顺序：先看下面 5 张卡。每张分别说明“事实依据、策展价值、图片支持”，不要用一个认同同时批准三件事。末尾是全部 27 条逐条复核及来源链接。

你先看卡片 1 即可。可以直接在聊天里说“知识这样改是否准确”“这个视觉后果有无依据”“这张图能不能支持”，不必填英文标签。

**动作统计（每条只计一个主要动作）：** '''+'；'.join(f'{k} {v} 条' for k,v in counts.items())+'。\n\n另有 5 条被选作展开讨论，属于上述条目的子集，不另外计数。')
    for r in sorted((r for r in rows if r.get('card')),key=lambda r:r['card']['order']):
        c=r['card']; f=current[r['fact_id']]; h=r['human_review']
        md(f"## 卡片 {c['order']}：{f['concept']} — {c['title']}\n\n**助手建议：{r['action']}，尚未人工验收。**\n\n**原陈述：** {f['fact']['statement']}\n\n**修订候选：** {c['statement']}\n\n**为什么修订：** {r['finding']}\n\n**你原来的记录（仅引用）：** {h['decision'] if h else '未审核'}；{h['notes'] if h else ''}")
        md('### A．事实依据与适用条件\n\n'+c['fact_support']+'\n\n**条件：** '+c['conditions']+'\n\n**来源：**\n\n'+'\n'.join(f"- [{s['title']}]({s['url']})：{s['support']}" for s in c['sources']))
        md('### B．为什么值得作为知识候选\n\n'+c['value']+'\n\n**条件 → 知识 → 视觉结果：** '+c['chain']+'\n\n**不能这样推出：** '+c['invalid_inference']+'\n\n**知识内容：** '+c['content_type']+'；**领域：** '+ '、'.join(f['fact']['knowledge_domains'])+'（沿用原领域标签，未做全量分类终审）。')
        im=c['image']; p=safe_file(read(run/'manifest.json')['dataset'],im['path'])
        if digest(p.read_bytes())!=im['sha256']:raise ValueError('Image bytes changed')
        mime='image/png' if p.suffix=='.png' else 'image/jpeg'
        md('### C．当前图片能说明什么\n\n![当前关联图片](attachment:material)\n\n**来源性质：** '+im['origin']+'\n\n**我亲自看到：** '+im['observation']+'\n\n**支持边界：** '+im['support']+'\n\n**还缺什么：** '+c['gap'],{'material':{mime:base64.b64encode(p.read_bytes()).decode()}})
        md('**本卡现在可供你确认：** '+c['ask']+'\n\n只确认某一项不会自动批准其他项。正式保存分维度判断的界面尚未实现，本页先用于讨论。\n\n追溯：`'+r['fact_id']+'`；这是一份新修订建议，没有修改原记录。')
    md('## 全部 27 条的复核\n\n下面按原概念列出；“暂缓”不表示知识错误，“修订”不表示修订版已通过。未展开图片的条目只统计关联模型结果数量，不声称完成看图复核。')
    for r in rows:
        f=current[r['fact_id']]
        md(f"### {f['concept']} · {r['fact_id'].rsplit('_',1)[1]} · {r['action']}\n\n**原知识：** {f['fact']['statement']}\n\n**事实与引文检查：** {r['finding']}\n\n**策展处置：** {r['next']}\n\n**图片范围：** 已有关联模型结果 {r['image_count']} 条；{'本页展示并人工目视复核一张（助手身份）。' if r.get('card') else '本次未做该条的完整图片复核。'}\n\n**外部复核：** {r['external_scope']}\n\n**本地引文出处：**\n\n"+'\n'.join(f"- [{s['title']}]({s['url']})" for s in r['local_sources']))
    md('## 本次交付边界\n\n完成了逐条助手复核和 5 张材料完整、缺口明确的候选卡。没有把缺口补成虚构事实，没有导出新的核心集，没有继续生成先前的三道试验题。\n\n后续先讨论这 5 张卡，再把已校准的规则落实到提取 prompt 与分维度审核入口；不再要求你为同一条同时判断事实真假、知识价值和出题质量。')
    out=Path(output or ROOT/'curation/archive/pre_v1/reassessment.ipynb')
    out.write_text(json.dumps(dict(nbformat=4,nbformat_minor=5,metadata={},cells=cells),ensure_ascii=False,indent=1)+'\n')
    return dict(path=str(out),counts=dict(counts),cards=sum(bool(r.get('card')) for r in rows))

if __name__=='__main__':print(build())
