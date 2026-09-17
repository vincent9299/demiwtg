"""Case-only notebook presentation of taxonomy, source locations and new outputs."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from functools import lru_cache
from pathlib import Path
from pipeline import ROOT, STATE, read
from review import esc, picture
from bagel_runner import sha
from case_input_details import classification_details, actual_input_details
from classification_notebook import case_row

RUN = STATE/'nonobject_v1'


def taxonomy_header(case):
    meta = case.get('taxonomy_record')
    if meta is None:
        meta = next((x['taxonomy_record'] for x in read(RUN/'original_case_taxonomy.json') if x['question_id']==case['question_id']), {})
    paths = meta.get('taxonomy_paths', [])
    parts=['<div style="background:#eef5fa;padding:12px;border-left:4px solid #468"><p><b>Taxonomy path（库内原挂载）：</b></p>']
    parts += ['<p>'+esc(p)+'</p>' for p in paths] if paths else ['<p>无精确现有挂载；下面是待补充概念，未写回权威树。</p>']
    parts += ['<p><b>概念：</b>'+esc(meta.get('concept_name',case['concept']))+'</p>', '<p><b>本题知识情境：</b>'+esc(case['concept'])+'</p>', '<p><b>本题主考领域：</b>'+esc(case['domain'])+'（按知识语义审核，不直接照搬原挂载）</p>']
    if meta.get('suggested_semantic_path'):parts.append('<p><b>建议补充／调整归属：</b>'+esc(meta['suggested_semantic_path'])+'</p>')
    if meta.get('json_pointer'):
        parts.append('<p><b>概念库定位：</b><a href="'+esc(meta['concepts_path'])+'">concepts.json</a>，JSON Pointer <code>'+esc(meta['json_pointer'])+'</code>。</p>')
    parts.append(classification_details(case))
    parts.append('<p>原挂载可能交叉或存在噪声；本页展示不修改 taxonomy。概念记录与知识正文来源分别列出。</p></div>')
    return case_row(case)+'<details><summary>概念定位与先前分类审核（保留差异说明）</summary>'+''.join(parts)+'</details>'


@lru_cache(maxsize=1)
def page_locations():
    p=ROOT/'state/collect/docs_clean/pages_clean.jsonl'
    return {d['page_sha']:(n,d['url']) for n,line in enumerate(p.open(),1) if (d:=json.loads(line))}


def source_locations(source):
    parts=[]
    origin=source.get('origin')
    if origin=='existing_local_clean_document':
        parts.append('<p><b>来源类型：库内已有文档。</b></p>')
        loc=page_locations().get(source.get('page_sha'))
        line=source.get('library_line') or (loc[0] if loc and loc[1]==source['url'] else None)
        path=source.get('library_path') or str(ROOT/'state/collect/docs_clean/pages_clean.jsonl')
        if line:parts.append('<p><b>库内定位：</b><a href="'+esc(path)+':'+str(line)+'">'+esc(path)+':'+str(line)+'</a>；page_sha=<code>'+esc(source.get('page_sha',''))+'</code>。</p>')
        else:parts.append('<p>原记录标记为库内文档；本次未解析到净版行号，请核对下面保存的来源快照。</p>')
        if source.get('associated_concepts'):parts.append('<p>该正文原关联概念：'+esc('、'.join(source['associated_concepts']))+'；不要求知识只能用于原关联概念。</p>')
        if source.get('text_char_start') is not None:parts.append('<p>正文text字段字符区间（从0起、右端不含）：'+str(source['text_char_start'])+'—'+str(source['text_char_end'])+'。</p>')
    else:parts.append('<p><b>来源类型：外部补充／复核资料，不冒称原知识库已有。</b></p>')
    parts.append('<p><b>原始引用：</b><a href="'+esc(source['url'])+'">'+esc(source.get('title',source['url']))+'</a>'+('；'+esc(source['locator']) if source.get('locator') else '')+'</p>')
    if source.get('snapshot_path'):parts.append('<p><b>本次使用的本地来源快照：</b><a href="'+esc(source['snapshot_path'])+'">'+esc(source['snapshot_path'])+'</a>。保存到本地不改变外部来源属性。</p>')
    parts.append('<p><b>支持范围：</b>'+esc(source['support_scope'])+'</p>')
    return ''.join(parts)


def supplemental_card(c,index,style):
    batch=c.get('runtime_batch','nonobject_v1')
    run=STATE/batch
    out=[style,'<div class="case-zh">',taxonomy_header(c),'<h2>'+str(index)+'．'+esc(c['concept'])+(' · 场景知识联合应用</h2>' if batch=='scene_v1' else ' · 非物体知识应用</h2>'),
         '<h3>中文题面</h3><pre>'+esc(c['prompt_zh'])+'</pre><details><summary>英文实际题面</summary><pre>'+esc(c['prompt'])+'</pre></details>',
         '<h3>输入知识</h3><p>本题比较无额外知识与有文字知识，不输入参考图片。以下是有来源依据的知识转述，未把它冒充逐字原文；左右两组原题相同。</p><pre>'+esc(c['knowledge_zh'])+'</pre>',
         '<details><summary>实际输入模型的英文知识转述</summary><pre>'+esc(c['knowledge_text'])+'</pre></details>',
         '<details><summary>知识来源：库内定位与外部引用</summary>']
    for s in c['sources']:
        out += ['<h4>'+esc(s['source_id'])+'</h4>',source_locations(s),'<blockquote>'+esc(s['quote'])+'</blockquote>']
    if batch=='scene_v1':
        out.append('</details><details open><summary>本题的知识／执行边界与首轮发现</summary>')
        if c['question_id']=='dev_scene_ecology':
            out.append('<p>阶段形态、足的分布与合法食物选择是知识项；题面已要求正在捕食，因此嘴与猎物的接触同时是显式执行项。冻结K3/K4把食物选择和接触合在一起，本页保留原判据但不把四项相加解释为纯知识分。BAGEL两组仍未正确区分成幼体，Gemini两组主要局部项均成立；未证明知识带来可靠增益。</p>')
        else:
            out.append('<p>典型出水口和排离墙面依赖构件功能知识；右侧没有水路是显式前提，因此K3只是较弱的条件一致性检查，不能当独立长尾知识。两模型无知识时已经画出主要出水关系，BAGEL主要不足在完整建筑场景执行。本题可展示功能条件选择，但目前不是知识增益的强证据。</p>')
        out.append('<p>这些场景未新增知识图像资料；不是图像检索验证。先保留成功与失败输出，不按增益挑题。</p>')
    out.append('</details><h3>无知识／有知识生成对比</h3>')
    review_path=run/'reviews.json';reviews=read(review_path)['reviews'] if review_path.exists() else []
    lookup={(r['model'],r['job_id']):r for r in reviews}
    for model in ['bagel','gemini']:
        out.append('<h4>'+model.upper()+'</h4><table><tr><th>无额外知识</th><th>有文字知识</th></tr><tr>')
        for cond in ['baseline','text']:
            job=c['question_id']+'__'+cond+'__r1';p=run/model/'jobs'/job/'result.json';out.append('<td>')
            if p.exists():
                r=read(p)
                if r['ok']:
                    assert sha(Path(r['image']).read_bytes())==r['output_sha256'];out.append(picture(r['image'],model+' · '+cond))
                else:out.append('<p>本次输出失败：'+esc(r.get('error','unknown'))+'</p>')
            else:out.append('<p>尚无生成结果，不以占位图冒充输出。</p>')
            if (model,job) in lookup:
                rv=lookup[(model,job)];out.append('<details><summary>助手逐图观察（可展开）</summary><p>'+esc(rv['observations'])+'</p><p>'+esc(rv['execution_observations'])+'</p><pre>'+esc(rv['knowledge'])+'</pre></details>')
            out.append('</td>')
        out.append('</tr></table>')
    out.append('<details><summary>条件 → 知识 → 可见结果</summary>')
    for a in c['application_links']:out.append('<p><b>条件：</b>'+esc(a['given'])+'</p><p><b>知识依据：</b>'+esc('、'.join(a['facts']))+'</p><p><b>应用后的可见结果：</b>'+esc(a['visual_result'])+'</p>')
    out.append('</details><details><summary>知识判据与观察部位</summary>')
    for k in c['knowledge_checks']:out.append('<p><b>'+esc(k['id'])+'：</b>'+esc(k['criterion'])+'<br>观察：'+esc(k['observable_region'])+'</p>')
    for title,key in [('执行与保持要求','execution_checks'),('例外与容许变化','exceptions'),('证据缺口与范围','gaps')]:
        out.append('</details><details><summary>'+title+'</summary><ul>'+''.join('<li>'+esc(x)+'</li>' for x in c[key])+'</ul>')
    out.append('</details>')
    out.append(actual_input_details(c, batch))
    out.append('<p>本轮每条件只生成一次，全部结果保留；不据单次表现断言长尾、难度或RAG收益。来源审核不等于人工准入。</p></div>')
    return ''.join(out)
