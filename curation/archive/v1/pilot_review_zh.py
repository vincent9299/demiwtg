"""Build an immediately readable Chinese notebook of the original pilot and supplemental non-object cases."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from pathlib import Path
from pipeline import STATE, read
from review import esc, picture
from bagel_runner import sha
from case_input_details import actual_input_details
from classification_notebook import guide, overview_table
from nonobject_notebook import taxonomy_header, source_locations, supplemental_card

RUN = STATE / 'pilot'
STYLE = '<style>.case-zh{font:16px/1.65 sans-serif;max-width:1200px}.case-zh pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f6;padding:12px}.case-zh figure{margin:8px 0}.case-zh img{max-width:100%;max-height:520px}.case-zh table{width:100%;table-layout:fixed;border-collapse:collapse}.case-zh td,.case-zh th{width:50%;vertical-align:top;border:1px solid #ddd;padding:10px}.case-zh details{margin:12px 0}.case-zh summary{cursor:pointer}</style>'


def items(values, empty='原冻结记录未列出；不表示已经排除所有问题。'):
    return '<ul>' + ''.join('<li>' + esc(v) + '</li>' for v in values) + '</ul>' if values else '<p>' + esc(empty) + '</p>'


def audit_details(case):
    sources = {s['source_id']: s for s in case['sources']}
    parts = ['<h3>可展开的审核细节</h3><p>下列条件、判据、例外及材料充分性来自首轮冻结记录；后续发现的问题另列，不覆盖原标准。</p>',
             '<details><summary>① 条件 → 需要调用的知识 → 可见结果</summary>']
    for i, link in enumerate(case['application_links'], 1):
        parts.append('<h4>应用链 ' + str(i) + '</h4><p><b>已给条件：</b>' + esc(link['given']) + '</p><p><b>调用知识：</b></p>')
        for sid in link['facts']:
            s = sources[sid]
            parts.append('<p><a href="' + esc(s['url']) + '">' + esc(sid) + '</a>：' + esc(s['support_scope']) + '</p><blockquote>' + esc(s['quote']) + '</blockquote>')
        parts.append('<p><b>应出现的可见结果：</b>' + esc(link['visual_result']) + '</p>')
    parts += ['</details><details><summary>② 知识判据：检查什么、看哪里、依据与例外</summary>',
              '<p>通过＝结果可见且符合判据；冲突＝可见结果明确不符；无法观察＝遮挡、尺度或输出问题使判断不足。局部判据通过不自动认证整体物种或型号正确。</p>']
    for k in case['knowledge_checks']:
        parts += ['<h4>' + esc(k['id'] + '：' + k['criterion']) + '</h4>',
                  '<p><b>观察部位：</b>' + esc(k['observable_region']) + '</p>',
                  '<p><b>依据：</b>' + '、'.join('<a href="' + esc(sources[sid]['url']) + '">' + esc(sid) + '</a>' for sid in k['source_ids']) + '</p>',
                  '<p><b>本项例外／不可观察条件：</b></p>' + items(k.get('exceptions', []))]
    parts += ['</details><details><summary>③ 执行、编辑保持与画质：和知识分开看</summary>',
              '<p><b>首轮冻结的执行检查：</b></p>', items(case['execution_checks']),
              '<p>对象数量、题面场景和显式约束依据上方原题判断；编辑题还须检查原图的非目标部分是否保持。画质另看形变、清晰度、材质及整体自然程度，不以好看代替知识正确。</p>',
              '<p>后续审计将身份知识与通用执行分离，并纠正了NASA原题未要求“两端完整”的越界判据。每张结果的初审折叠栏中同时展示原执行判断和补充判断；不会无痕修改旧分。</p>',
              '</details><details><summary>④ 合理例外与适用范围</summary>', items(case.get('exceptions', [])),
              '<p>逐项例外见②；来源只能支持其注明的范围，不能把题目设定的性别、月龄或假想改装当成原照片已证明的事实。</p>',
              '</details><details><summary>⑤ 证据充分性与缺口：文字／图片分别支持什么</summary>']
    ids = [k['id'] for k in case['knowledge_checks']]
    for mode, label in [('text', '文字资料'), ('image', '参考图片'), ('multimodal', '图文合用')]:
        supported = case.get('evidence_sufficiency', {}).get(mode)
        if supported is None:
            parts.append('<p>' + label + '：未记录充分性判断。</p>')
        else:
            missing = [k for k in ids if k not in supported]
            parts.append('<p><b>' + label + '：</b>支持 ' + esc('、'.join(supported) or '无已列知识项') + '；未列为充分支持 ' + esc('、'.join(missing) or '无') + '。</p>')
    parts.append('<p>这是材料审核判断，不是模型得分；仅图片组答对也可能依赖模型原有知识。</p>')
    for i, im in enumerate(case['reference_images'], 1):
        parts.append('<p><b>参考图 ' + str(i) + ' 的实际支持范围：</b>' + esc(im.get('support_scope', '未记录')) + '</p>')
    parts += ['<p><b>出图前冻结的证据缺口：</b></p>', items(case.get('gaps', [])),
              '<p><b>材料审核备注：</b>' + esc(case.get('quality_notes', '未记录')) + '</p>',
              '<p>“尚未生成验证”等措辞保留的是出图前状态：本批已经生成，后续结果及局限见对比与⑥。</p>',
              '</details><details><summary>⑥ 三维判断、后续发现与审核状态</summary>',
              '<p><b>事实依据：</b>来源片段及支持范围经过助手开发审核，尚非人工金标准；①及②可逐项回到来源核对。</p>',
              '<p><b>策展价值：</b>本题已建立题目前提、来源知识和可见结果的应用链；模型难度与收益以实际输出判断，不从“事实正确”直接推出“好题”。</p>',
              '<p><b>图片支持：</b>以⑤逐项支持范围为准；图片关联、文件存在和外形相似均不自动认证拍摄真实性或生物身份。</p>']
    if 'pignose' in case['question_id']:
        parts.append('<p><b>后续复核：</b>鳍状肢与多趾蹼足存在评分边界；局部猪鼻／宽肢命中不等于整体猪鼻龟正确。需要更充分的身份结构判据，原分保留。</p>')
    if case['question_id'] == 'dev_conditional_lady_larva':
        parts.append('<p><b>后续复核：</b>深色、分节、橙斑不足以排除多足毛虫式错误。下一版应结合来源支持的鳄鱼状体形、胸部三对足等检查；当前冻结局部判据没有据此重写。</p>')
    if 'nasa_' in case['question_id']:
        parts.append('<p><b>后续复核：</b>原题只要求一段管道，未要求管道两端完整入镜。执行补充审计移除了这个额外门槛；颜色知识判据不变。表页显眼的表头／图标颜色不能替代按氧含量选择正确行。</p>')
    parts.append('<p>当前材料均为开发题，助手复核不能自动算成人工准入。资料真实来源、局部知识、完整对象、通用执行和画质应分别判断。</p>')
    parts.append('<details><summary>查看该题冻结的完整审核字段（原始记录）</summary><pre>' + esc({k: case.get(k) for k in ['domain', 'knowledge_types', 'application_level', 'knowledge_family_id', 'readiness', 'assistant_assessment', 'application_links', 'knowledge_checks', 'execution_checks', 'exceptions', 'evidence_sufficiency', 'gaps', 'quality_notes']}) + '</pre></details></details>')
    return ''.join(parts)


def result_cell(case, model, condition, reviews):
    job = case['question_id'] + '__' + condition + '__r1'
    d = RUN / model / 'jobs' / job
    result = read(d / 'result.json')
    r = reviews[(model, job)]
    parts = []
    if result['ok']:
        assert sha(Path(result['image']).read_bytes()) == result['output_sha256']
        parts.append(picture(result['image'], '无额外知识' if condition == 'baseline' else '加入图文知识'))
    else:
        parts.append('<p><b>该次返回3张图，未满足单图契约；原评分保留失败，没有挑选最佳图。</b></p>')
        auxiliary = read(d / 'auxiliary_manifest.json')['images']
        for n, im in enumerate(auxiliary, 1):
            assert sha(Path(im['path']).read_bytes()) == im['sha256']
            parts.append(picture(im['path'], '同次响应原图 ' + str(n) + '（仅供查看，不改主分）'))
    labels = {'pass': '通过', 'conflict': '冲突', 'unobservable': '无法观察'}
    parts.append('<details><summary>助手初审（局部判据，不等于整体正确）</summary><p>' + esc('；'.join(k+': '+labels[v] for k,v in r['knowledge'].items())) + '</p><p>' + esc(r.get('observations', '')) + '</p><p>' + esc(r.get('execution_observations', '')) + '</p></details>')
    supplement = r.get('execution_supplement')
    if supplement:
        parts.append('<details><summary>后续执行补充审核（保留原分）</summary><p>原执行：' + ('通过' if supplement['execution_pass_original'] else '未通过') + '；补充通用执行：' + ('通过' if supplement['execution_pass'] else '未通过') + '。</p><p>' + esc(supplement['execution_observations']) + '</p><p>变更理由：' + esc(supplement.get('change_reason') or '未改变。') + '</p><p>身份备注（不是新的身份金标准）：' + esc(supplement.get('identity', {}).get('observations', '未记录')) + '</p></details>')
    return ''.join(parts)


def card(case, translation, index, reviews):
    parts = [STYLE, '<div class="case-zh">', taxonomy_header(case), '<h2>' + str(index) + '．' + esc(case['concept']) + ' · ' + ('图像编辑' if case['task'] == 'edit' else '文生图') + '</h2>',
             '<h3>题面（中文译文）</h3><pre>' + esc(translation['prompt_zh']) + '</pre>',
             '<details><summary>实际使用的英文原题</summary><pre>' + esc(case['prompt']) + '</pre></details>']
    if case.get('edit_source'):
        parts.append('<h3>编辑原图：两组共同输入</h3>' + picture(case['edit_source']['path'], '这张图是待编辑场景，不是额外知识。'))
    parts += ['<h3>有知识组额外输入了什么</h3>', '<p>下列文字资料＋下列参考图片。原题保持相同；没有先把资料改写成答案caption。</p>',
              '<h4>文字知识：中文阅读说明</h4><pre>' + esc(translation['knowledge_zh']) + '</pre>',
              '<details><summary>实际送入模型的完整知识原文</summary><pre>' + esc(case['knowledge_text']) + '</pre></details>',
              '<h4>实际输入的知识参考图</h4>']
    for im in case['reference_images']:
        parts.append(picture(im['path'], im.get('support_scope', '知识参考图')))
    parts.append('<details><summary>原文来源与支持范围</summary>')
    for s in case['sources']:
        parts.append(source_locations(s))
    parts.append('</details><h3>生成对比：左无额外知识，右有图文知识</h3>')
    for model, title in [('bagel', 'BAGEL'), ('gemini', 'Gemini')]:
        parts += ['<h4>' + title + '</h4><table><tr><th>无额外知识</th><th>加入图文知识</th></tr><tr>',
                  '<td>' + result_cell(case, model, 'baseline', reviews) + '</td>',
                  '<td>' + result_cell(case, model, 'multimodal', reviews) + '</td></tr></table>']
    parts.append(audit_details(case))
    parts.append(actual_input_details(case, 'pilot'))
    parts.append('</div>')
    return ''.join(parts)


from batch_review_summary import summary as batch_summary


def main():
    cases = read(RUN / 'cases.json')['cases']
    translations = {x['question_id']: x for x in read(RUN / 'review_translations_zh.json')['cases']}
    reviews = {(x['model'], x['job_id']): x for x in read(RUN / 'reviews_combined.json')['reviews']}
    for r in read(STATE / 'execution_supplement_reviews.json')['reviews']:
        reviews[(r['model'], r['job_id'])]['execution_supplement'] = r
    assert len(cases) == len(translations) == 12
    supplemental = read(STATE / 'nonobject_v1' / 'cases.json')['cases']
    scenes_path=STATE/'scene_v1/cases.json'
    if scenes_path.exists(): supplemental += read(scenes_path)['cases']
    original_count = len(cases)
    cases = cases + supplemental
    intro = '# 18题（首轮12题＋4题非物体知识＋2题场景知识） · 中文题面与有无知识对比\n\n直接向下浏览即可，图片已嵌入，无需运行生成模型。每题依次展示中文题面、编辑原图（若有）、输入知识文字与参考图、BAGEL和Gemini的左右对比。\n\n第1—12题右侧加入图文知识；新增第13—18题右侧只加入文字知识，不输入参考图。左侧均为无额外知识；这是独立生成对照，不是把左图继续编辑成右图。编辑题两侧都使用同一编辑原图。中文仅供审核，实际生成使用保留的英文原题。这里展示原首轮结果，不混入后续复验。原始评分有已发现的问题，默认折叠，不代替你的判断。\n\n**目录**\n\n' + '\n'.join(f'{i}. {c["concept"]}' for i,c in enumerate(cases,1))
    intro += '\n\n**按知识类型挑几个代表看：** 第1题猪鼻龟（结构）、第4题幼年马来貘（阶段状态）、第9题瓢虫食物关系（生物关系）、第8题滴水嘴兽（功能）、第11题NASA管道标识（规约）、第13题全反射（物理机制）、第14题彩虹（地理气象）。第17—18题新增生态互动与建筑排水场景，可与第6／9和第8题对照阅读；不是相同输入下的严格难度消融。现有第15—16题虽涉及动态机制，但只考瞬时／平衡结果，不能据此声称完整覆盖过程变化；化学、历史典故、天文对应也未由这些题充分代表。\n\n每题开头新增分类审核；末尾展开“实际给 BAGEL／Gemini 的完整 prompt 与图片输入”，可查看各条件、各模型保存的实际请求。原12题包含仅文字、仅图片等其他已运行条件的输入，但上方生成对比仍保留无知识／图文知识两列。'
    overview=overview_table()
    overview_code = 'import sys\nfrom pathlib import Path\nfrom IPython.display import display, HTML\nmodule_dir = ' + repr(str(_ARCHIVE_ROOT/'curation/archive/compat/knowledge_application_v1')) + '\nif module_dir not in sys.path: sys.path.insert(0, module_dir)\nfrom classification_notebook import overview_table\ndisplay(HTML(overview_table()))'
    cells = [{'cell_type':'markdown','id':'classification-guide','metadata':{},'source':guide().split('## 18题分类总表')[0]},
             {'cell_type':'markdown','id':'batch-review-summary','metadata':{},'source':batch_summary('pilot')+'\n## 18题分类总表\n\n下表包含首轮12题及后续6题；上方效果统计仅限首轮12题。'},
             {'cell_type':'code','id':'classification-table','metadata':{'jupyter':{'source_hidden':True}},'execution_count':1,'source':overview_code,'outputs':[{'output_type':'display_data','metadata':{},'data':{'text/html':overview,'text/plain':'18题分类总表（可横向滚动）'}}]},
             {'cell_type':'markdown', 'id':'intro-zh', 'metadata':{}, 'source':intro}]
    pages = []
    for index, c in enumerate(cases, 1):
        html = card(c, translations[c['question_id']], index, reviews) if index <= original_count else supplemental_card(c, index, STYLE)
        pages.append(html)
        code = 'from pathlib import Path\nfrom IPython.display import display, HTML\npage = Path(' + repr(str(RUN / 'review_zh_cards' / f'{index:02d}.html')) + ')\ndisplay(HTML(page.read_text()))'
        path = RUN / 'review_zh_cards' / f'{index:02d}.html'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        cells.append({'cell_type':'code','id':f'case-{index:02d}','metadata':{'jupyter':{'source_hidden':True}},'execution_count':index,'source':code,'outputs':[{'output_type':'display_data','metadata':{},'data':{'text/html':html,'text/plain':f'第{index}题：{c["concept"]}'}}]})
    nb = {'cells':cells, 'metadata':{'kernelspec':{'display_name':'demiwtg','language':'python','name':'demiwtg'},'language_info':{'name':'python'}},'nbformat':4,'nbformat_minor':5}
    # Embedded image outputs are runtime data, not repository code.
    from notebook_frontmatter import apply as apply_frontmatter
    apply_frontmatter(nb, 'pilot')
    notebook = RUN / 'pilot_review_zh.ipynb'
    notebook.write_text(json.dumps(nb,ensure_ascii=False,indent=1))
    # Keep the lightweight code entry in sync; all rendered outputs stay in state.
    light = json.loads(json.dumps(nb))
    intro_cell = next(c for c in light['cells'] if c.get('id') == 'intro-zh')
    intro_cell['source'] = intro_cell['source'].replace('直接向下浏览即可，图片已嵌入，无需运行生成模型。', '运行全部单元格即可展示18题，仅读取已有结果，不调用模型。每题下有六组可展开审核细节。')
    for cell in light['cells']:
        if cell['cell_type']=='code':
            cell['outputs'] = []
            cell['execution_count'] = None
    Path(__file__).with_suffix('.ipynb').write_text(json.dumps(light, ensure_ascii=False, indent=1))
    print(json.dumps({'notebook':str(notebook),'cases':len(pages),'main_result_slots':4*len(pages)},ensure_ascii=False))


if __name__ == '__main__':
    main()
