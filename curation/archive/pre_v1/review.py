"""Notebook review helpers. Only save() creates human decisions; displaying never approves."""
from __future__ import annotations

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import html
import copy
import json
from pathlib import Path
from curation.core import (DEFAULT_RUN, read, facts, accepted_results, reviewed, review, safe_file, require_frozen, image_origin, digest, require)

class ReviewQueue:
    def __init__(self, run=DEFAULT_RUN, reviewer='', split='calibration'):
        self.run = Path(run)
        self.reviewer = reviewer
        if split not in ('calibration', 'holdout'):
            raise ValueError('split must be calibration or holdout')
        self.split = split
        if split == 'holdout':
            require_frozen(self.run)
        self.current = None

    def pending(self):
        queue = []
        for f in facts(self.run):
            if f['split'] == self.split and reviewed(self.run, 'fact', f['fact_id'], f) is None:
                queue.append(('fact', f['fact_id'], f))
        for t, r in accepted_results(self.run, 'evidence'):
            target = {'task': t, 'result': r['result']}
            if t['input']['split'] == self.split and reviewed(self.run, 'evidence', t['task_id'], target) is None:
                queue.append(('evidence', t['task_id'], target))
        hints = self.hints()
        queue.sort(key=lambda item: (item[0] != 'fact', item[1] not in hints))
        return queue

    def hints(self):
        path = self.run / 'review_hints.json'
        return read(path) if path.exists() else {}

    def summary(self):
        pending = self.pending()
        fact_count = sum(kind == 'fact' for kind, _, _ in pending)
        print(f'待审核：知识卡 {fact_count} 条，图片卡 {len(pending)-fact_count} 条。')
        print('先核对知识，再看图片。今天先做 3 条即可；关闭后可继续，已保存的条目会自动跳过。')
        print('接下来运行「② 看一条」，只读展示，不会调用模型或写入审核。')

    def show(self):
        from IPython.display import display, HTML, Image, Markdown
        def field(label, value):
            if isinstance(value, list):
                value = '；'.join(str(v) for v in value) or '未列出'
            display(HTML('<p><b>' + html.escape(label) + '：</b>' + html.escape(str(value or '未填写')) + '</p>'))
        queue = self.pending()
        if not queue:
            self.current = None
            print('当前分组已无待审核条目。审核结束；这不代表所有条目都通过或已导出。')
            return
        kind, rid, item = queue[0]
        self.current = (kind, rid)
        fact = item['fact'] if kind == 'fact' else item['task']['input']['fact']
        concept = item['concept'] if kind == 'fact' else item['task']['input'].get('concept', '')
        display(Markdown(f"### {'知识卡：核对一句知识' if kind == 'fact' else '图片卡：核对图片能否承载知识'} · {concept}"))
        field('这次要核对的知识（模型草稿）', fact['statement'])
        field('适用条件', fact.get('conditions', []))
        field('知识内容类型（描述哪一类知识）', fact.get('content_types', []))
        field('知识领域（知识属于什么学科或主题）', fact.get('knowledge_domains', []))
        field('模型认为值得收录的原因', fact.get('core_reason'))
        field('模型设想的画面表达，不代表已有图片能做到', fact.get('visual_consequence'))
        if kind == 'fact':
            linked = [(t, r) for t, r in accepted_results(self.run, 'evidence')
                      if t['input']['fact_id'] == rid]
            if not linked:
                print('图片进度：这条知识尚无已完成的图片标注。这不是无图知识的最终判定；当前只审核文字依据。')
            else:
                print(f'图片进度：这条知识有 {len(linked)} 张已完成标注的候选图，下面先供对照；图片结论稍后在图片卡单独审核。')
                manifest = read(self.run/'manifest.json')
                for linked_task, _ in linked:
                    display(Image(filename=str(safe_file(manifest['dataset'], linked_task['input']['image']['path'])), width=650))
            display(Markdown('**出题适用性也要检查：** “通常、传统上、可能”不能直接变成所有画面必须满足的规则。若题目未限定情境，合理例外不能判错；若限定后仍无法从画面区分对错，请在依据里注明“知识可能成立，但不适合作为当前核心考点”。'))
        if kind == 'fact':
            display(Markdown('#### 对照下面的原文\n先看来源是否讲同一个概念，再看原文是否支持上面整句话，包括范围、条件和例外。引文匹配通过只说明引用存在，不保证知识正确。'))
            for citation in fact['citations']:
                source = next(s for s in item['sources'] if s['source_id'] == citation['source_id'])
                at = source['text'].find(citation['quote'])
                field('来源标题', source['title'])
                field('原文中的引用', citation['quote'])
                excerpt = source['text'][max(0, at-350):at+len(citation['quote'])+350]
                display(HTML('<details><summary>展开引用前后的上下文</summary><pre style="white-space:pre-wrap">' + html.escape(excerpt) + '</pre></details>'))
                field('来源网址（必要时核对完整页面）', source['url'])
                if source['truncated']:
                    print('本地只保存了部分页面；当前上下文不够判断时，选「拿不准」。')
            display(Markdown('**现在判断：** 来源对得上吗？整句知识有依据吗？条件有没有遗漏？内容类型、领域和画面设想是否合理？\n\n都认可才选「认同」；发现问题选「有问题」并说明哪句话；不熟悉或原文看不懂，选「拿不准」。不用为了通过而猜。'))
        else:
            t = item['task']['input']
            result = item['result']
            display(Markdown('#### 先亲自看图，再读模型判断\n本卡评估的是这张图与上述知识的关系；知识本身还需要知识卡通过。'))
            field('图片来源性质', '已声明为模型生成图，不能当作独立事实证据' if image_origin(t['image']) == 'declared_generated' else '来源真实性尚未核实')
            manifest = read(self.run/'manifest.json')
            display(Image(filename=str(safe_file(manifest['dataset'], t['image']['path'])), width=1000))
            for observation in result.get('observations', []):
                field('模型称在' + observation.get('anchor', '') + '看见', observation.get('visible'))
            field('模型对图片与知识关系的解释', result.get('reason'))
            field('图片尚未呈现的知识部分', result.get('unsupported_aspects', []))
            statuses = {'usable': '可用', 'unusable': '不可用', 'needs_more': '需要补充材料', 'unreviewed': '未判断'}
            for branch, title in [('t2i', '文生图：能否支撑围绕这条知识出题'), ('edit', '图像编辑：能否在这张原图上设计依赖知识的修改')]:
                task = result[branch]
                display(Markdown('#### ' + title))
                field('模型建议（需要你核对）', statuses.get(task['status'], task['status']))
                labels = {'target':'拟考察的画面', 'checks':'看图检查点', 'initial_state':'原图的初始状态', 'instruction':'拟议编辑指令', 'expected_change':'预期变化', 'knowledge_dependency':'为什么需要知识', 'preserve':'应保留的内容', 'reason':'判断理由', 'needs_more':'还缺什么'}
                for key, label in labels.items():
                    if key in task:
                        field(label, task[key])
            if rid in self.hints():
                field('助手发现的疑点，供参考，仍可能有误', self.hints()[rid]['note'])
            display(Markdown('**现在判断：** 模型有没有看错图？有没有把局部当成完整知识？文生图和编辑两项结论及理由是否都合理？\n\n「认同」表示认可整张卡的标注，包括模型说“不可用”的结论，**不等于让图片入选**。任意一项不同意就选「有问题」，用中文说明；不需要你修改 JSON。'))
        display(HTML('<details><summary>技术明细（通常不用看）</summary><pre style="white-space:pre-wrap">' + html.escape(json.dumps(item, ensure_ascii=False, indent=2)) + '</pre></details>'))
        print('看完后去「③ 保存判断」。当前仅展示，尚未保存。')

    def save_judgment(self, judgment, notes, reviewer=None):
        """Chinese notebook entry point; agreement endorses the displayed branch decisions."""
        if reviewer is not None and reviewer.strip():
            self.reviewer = reviewer.strip()
        if self.current is None:
            print('尚未保存：请先运行「② 看一条」，读完卡片后再运行③。')
            return
        if not self.reviewer.strip():
            print('尚未保存：请在③的「审核人」引号里填写姓名或固定昵称，再运行③。当前卡片保留，无需重新运行①②。')
            return
        decisions = {'认同': 'accept', '有问题': 'reject', '拿不准': 'uncertain'}
        if not judgment.strip() or not notes.strip():
            print('尚未保存：请填写「判断」和「依据」，再运行本格。')
            return
        if judgment not in decisions:
            raise ValueError('判断请填写：认同 / 有问题 / 拿不准')
        t2i = edit = 'unreviewed'
        if judgment == '认同' and self.current and self.current[0] == 'evidence':
            result = self.correction()['result']
            t2i, edit = result['t2i']['status'], result['edit']['status']
        self.save(decisions[judgment], notes, t2i=t2i, edit=edit)
        print('这条已记录。继续时回到「② 看一条」；也可以直接关闭，下次接着做。')

    def correction(self):
        if self.current is None or self.current[0] != 'evidence':
            raise ValueError('先展示一条图片证据；知识陈述修改需新批次重新核验')
        task,envelope=next((t,r) for t,r in accepted_results(self.run,'evidence') if t['task_id']==self.current[1])
        return {'target_id':self.current[1],'target_sha256':digest({'task':task,'result':envelope['result']}),'result':copy.deepcopy(envelope['result'])}

    def save(self, decision, notes, t2i='unreviewed', edit='unreviewed', corrected_result=None):
        if self.current is None:
            raise ValueError('先运行 queue.show()')
        if not self.reviewer.strip():
            raise ValueError('先填写真实 reviewer 名称')
        kind, rid = self.current
        if corrected_result is not None:
            require(kind=='evidence' and corrected_result.get('target_id')==rid,'correction belongs to a different record')
            expected=self.correction()
            require(corrected_result.get('target_sha256')==expected['target_sha256'],'stale correction')
            corrected_result=corrected_result['result']
        review(self.run, kind, rid, decision, self.reviewer, notes, t2i, edit, corrected_result)
        self.current = None
        print('已保存', rid, decision)

    def materials(self, index=0):
        """Browse frozen candidates before inference; does not approve facts or pages."""
        from IPython.display import display, HTML, Image
        m = read(self.run/'manifest.json')
        candidates = [c for c in m['concepts'] if c['split'] == self.split]
        c = candidates[index]
        print(index, c['name'], c['domains'], 'benchmark overlap:', c['benchmark_overlap'])
        for s in c['sources']:
            print(s['title'], s['url'], 'truncated:', s['truncated'])
            display(HTML('<pre>'+html.escape(s['text'][:1800])+'</pre>'))
        for im in c['images']:
            print(im['sha256'], im['old_stratum'])
            path = safe_file(m['dataset'], im['path'])
            if path.exists():
                display(Image(filename=str(path), width=650))
            else:
                print('文件缺失，不能用文本替代视觉证据')
