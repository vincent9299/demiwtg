"""Render reviewed-source trial drafts; no model calls or human-review mutations."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import base64
import json
from pathlib import Path
from curation.core import DEFAULT_RUN, ROOT, read, safe_file, digest


def build(run=DEFAULT_RUN, output=None):
    run=Path(run)
    data=read(run/'trial_questions_v1.json')
    cells=[]
    def md(s, attachments=None):
        c={'cell_type':'markdown','metadata':{},'id':f'trial-{len(cells)}','source':s.splitlines(True)}
        if attachments: c['attachments']=attachments
        cells.append(c)
    md('''# 三道试验题：先看题目是否值得试

**直接阅读，不需要运行 cell。** 本页是助手准备的题目，没有替你打标，也没有调用生成模型。三题涉及两个知识点；螺栓的文生图与编辑是同一考点在两种任务下的比较，不是独立覆盖量。

这轮只验证“前提能否形成可观察、不会误伤合理画法的约束”，不声称满足旧出题 prompt 的完整难度、组合和跨域门槛。知识归因仍需后续对照实验；一张图画错，不能直接证明模型不知道知识。

每题先读“给模型的题面”，再看“审核者的判据”。正式生成只能发送题面（编辑题另附原图），不能把答案与判据一并发送。

## 统一怎么记录结果

每条知识判据分别记：**符合／明确冲突／无法观察**，附画面位置和一句依据。另记指令执行问题，例如缺了要求的对象、遮挡了指定部位。

- 关键部位看不清：不猜测，记无法观察；若题面要求清楚入画，同时记指令问题。
- 画得清楚且关系与知识冲突：记明确冲突，暂不推断模型内部原因。
- 只满足部分判据：逐项保留，不能只用“整体看起来对”通过。
- 符合全部知识判据但违反数量或保留要求：知识表现与指令执行分开记录。

以下“正确／错误例子”是文字边界测试，不是已经生成或经过实验验证的图片。
''')
    for q in data['questions']:
        md(f"## {q['id']}．{q['title']}\n\n**用途：{q['mode']}；状态：待你审阅题意，尚未试生成。**\n\n**给模型的题面：**\n\n> {q['prompt']}")
        if q.get('source_image'):
            p=safe_file(read(run/'manifest.json')['dataset'],q['source_image'])
            assert digest(p.read_bytes())==q['source_image_sha256']
            md('**编辑题的原图：** 左起第一组是目标，另外四组为保留对象。图片作为编辑输入，不作为装配答案。\n\n![编辑原图](attachment:source)',{'source':{'image/jpeg':base64.b64encode(p.read_bytes()).decode()}})
        md('**已给定的前提：** '+q['premise']+'\n\n**仍需要模型自己知道：** '+q['knowledge']+'\n\n**为什么没有直接给答案：** '+q['leakage_check'])
        md('### 审核者的判据（不发给生成模型）\n\n'+'\n'.join(f"- **{c['id']}**：{c['criterion']}" for c in q['checks'])+'\n\n**另外检查指令：** '+q['instruction_checks'])
        md('**允许的其他画法：** '+q['allowed']+'\n\n**边界测试：**\n\n'+'\n'.join('- '+s for s in q['boundary_cases'])+'\n\n**不据此判分：** '+q['excluded'])
        md('**依据：** '+q['source_summary']+'\n\n'+ '\n'.join(f"- [{s['title']}]({s['url']})，定位：{s['locator']}。" for s in q['sources'])+'\n\n**局限：** '+q['limitations'])
    md('''## 接下来由你看什么

只需要看三段题面，反馈“可以试”或者指出具体哪题不符合目标。无需重新审核此前全部知识卡。

题意确定后，建议首轮每题生成 2 张，共 6 张（同一题保留全部结果，不挑最好的一张）。两张只用于暴露判据问题，不足以估计模型准确率。编辑题使用同一张原图。应先固定题面和判据版本，再看生成结果；如需改题，记录新版本，不倒改标准让旧结果通过。

本轮没有选择生成服务、没有使用 4001 端口、没有提交生成请求。若采用付费接口，先提供模型、张数与预算供确认。生成模型及预算确定后再执行。
''')
    out=Path(output or ROOT/'curation/archive/pre_v1/trial_questions.ipynb')
    out.write_text(json.dumps({'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':cells},ensure_ascii=False,indent=1)+'\n')
    return out

if __name__=='__main__':print(build())
