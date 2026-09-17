"""Render assistant calibration proposals as a read-only notebook; never writes reviews."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
from pathlib import Path
import base64
import json
from curation.core import DEFAULT_RUN, ROOT, read, facts, accepted_results, reviewed, digest, safe_file, image_origin


def build(run=DEFAULT_RUN, output=None):
    run = Path(run)
    output = Path(output or ROOT/'curation/archive/pre_v1/calibration.ipynb')
    bundle = read(run/'assistant_calibration.json')
    assert bundle['author_type'] == 'assistant' and bundle['status'] == 'proposal'
    fs = {f['fact_id']: f for f in facts(run)}
    es = {t['task_id']: {'task':t,'result':r['result']} for t,r in accepted_results(run,'evidence')}
    cells = []
    def md(text, attachments=None):
        cell = dict(cell_type='markdown', id=f'calibration-{len(cells)}', metadata={}, source=text.splitlines(True))
        if attachments: cell['attachments'] = attachments
        cells.append(cell)
    md('''# 先看这 4 个例子：助手复核与出题草案

**直接阅读，不需要运行任何 cell，也不用填写代码。** 这页全部是助手建议，没有修改你的人工标签，没有条目因此进入核心集。

你之前标注的问题很大一部分来自：页面只给了知识，却让你决定能否出题。这次每个例子都给出实际题面和判分边界。题面是给画图模型看的；判据只供审核，不能一起发给它。

沿用你原来的 T2I 定义：前提是题面在概念名之外锁定的条件。前提已告诉模型的内容不再计作知识推理。这些只是验证单个考点的最小草案，还不是满足原 prompt 难度、跨域和组合门槛的正式题目。

**阅读顺序：先看第 1 个鸡腿菇例子，再看第 2 个反例。** 不要求一次把四个都看完。后两个说明哪些知识需要补材料，哪些需要换一个更具体的考点。

本页不把“来源说过”当作事实终审，也不把“图片没画出”当作知识错误。引用和图片来源均在各例下面列出。
''')
    for i, c in enumerate(bundle['cases'],1):
        f=fs[c['fact_id']]
        assert digest(f)==c['fact_sha256'], 'stale fact proposal'
        h=reviewed(run,'fact',f['fact_id'],f)
        md(f"## {i}．{c['title']}\n\n**助手建议：{c['recommendation']}**\n\n原知识：{f['fact']['statement']}\n\n你的原记录：{h['decision'] if h else '未审核'}；{h['notes'] if h else ''}\n\n{c['human_feedback']}")
        if c.get('evidence_id'):
            e=es[c['evidence_id']]
            assert digest(e)==c['evidence_sha256'], 'stale evidence proposal'
            im=e['task']['input']['image']; path=safe_file(read(run/'manifest.json')['dataset'],im['path'])
            mime='image/png' if path.suffix.lower()=='.png' else 'image/jpeg'
            md('### 当前材料图（不是生成答案）\n\n![当前候选图片](attachment:material)\n\n'+c['image_review']+'\n\n来源标记：'+image_origin(im)+'；图像身份及出处未由本页独立认证。', {'material':{mime:base64.b64encode(path.read_bytes()).decode()}})
            eh=reviewed(run,'evidence',c['evidence_id'],e)
            if eh: md(f"图片卡原记录：{eh['decision']}；{eh['notes']}。这是原记录展示，不是重新保存。")
        else: md('图片状态：'+c['image_review'])
        for key,title in [('premise','题目限定了什么'),('prompt','给模型的题面草案'),('inference','模型还需要自己知道什么'),('checks','审核时看什么'),('alternatives','这些画法也可以，不应误判'),('failure','怎样才能说明画错了'),('leakage','有没有把答案告诉模型'),('next','现在还缺什么')]:
            md(f"### {title}\n\n{c[key]}")
        citations=[]
        for q in f['fact']['citations']:
            source=next(s for s in f['sources'] if s['source_id']==q['source_id'])
            citations.append(f"- [{source['title']}]({source['url']})：本地引用见原审核卡。")
        md('### 依据与边界\n\n'+c['source_note']+'\n\n'+ '\n'.join(citations)+f"\n\n追溯编号：`{c['fact_id']}`。复核性质：助手提案，尚未人工通过，尚未试生成。")
    md('''## 看完后，你只需要反馈一件事

你可以直接在聊天里说“第 1 个这样问我觉得可以”或者“第 3 个仍然像照指令画，因为……”。不必先选 accept/reject。我们先确认这些具体题目是否符合你的目标，再决定如何改正式审核入口。

本页只处理四个校准例子；剩余图片卡没有被批量代标。你之前的 29 条人工记录完整保留。后续若知识需要改写，将创建新候选，不能直接修改已经关联图片和人工记录的原知识。
''')
    nb=dict(nbformat=4,nbformat_minor=5,metadata={},cells=cells)
    output.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n')
    return output

if __name__=='__main__': print(build())
