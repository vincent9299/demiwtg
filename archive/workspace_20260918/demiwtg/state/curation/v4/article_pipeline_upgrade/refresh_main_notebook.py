"""Execute the main notebook in view_saved mode and verify full rendering."""
from pathlib import Path
import hashlib
import html
import json
import re
import shutil
import sys
import nbformat
from nbclient import NotebookClient

root = Path('/yzp/zhaozy/yangzepeng/0905/demiwtg')
notebook = root / 'curation/v4/knowledge_debug.ipynb'
runs = [root / 'state/curation/v4' / name for name in sys.argv[1:]]
assert runs and all((r / 'knowledge_base.jsonl').is_file() for r in runs)
hashes = {str(r): hashlib.sha256((r / 'knowledge_base.jsonl').read_bytes()).hexdigest() for r in runs}
calls = {str(r): len(list(r.glob('**/calls/*.request.json'))) for r in runs}
backup = runs[0] / 'main_notebook_before_display.ipynb'
if not backup.exists():
    shutil.copy2(notebook, backup)
nb = nbformat.read(notebook, as_version=4)
reviews = {r.name: json.loads((r / 'quality_review.json').read_text()) if (r / 'quality_review.json').exists() else {} for r in runs}
assessment = []
for name, review in reviews.items():
    state = '主要图文回归通过' if review.get('accepted') else '主要图文回归尚未通过'
    assessment.append('`' + name + '`：' + state)
    if review.get('limitations'):
        assessment.append('已知限制：' + '；'.join(review['limitations']))
nb.cells[1].source = ('**执行前请重启内核。** 代码、提示词或配置改变后使用新 RUN，同一版本才直接续跑。\n\n'
    '下面完整展示这些 run 实际保存的最终输出：' + '、'.join('`' + r.name + '`' for r in runs) + '。无概念、段落或图片数量限制。\n\n'
    + '；'.join(assessment) + '。程序状态 reviewed 只表示模型最终 review 和程序校验完成，不等于语义正确或事实已独立核验。'
    '“瓶式台球”仍明确为材料不足；“人口分布图”已用于调参，属于回归例。全部来源事实未经独立认证。'
    + (' 本版本正文知识来自原文，图片由模型选择编号、程序原样附回；不生成图注，空图注是pipeline输出本身，并非展示隐藏。'
       if all(json.loads((r / 'dataset_manifest.json').read_text()).get('config', {}).get('model_config', {}).get('final_image_selection_only') for r in runs) else '')
    + (' 最后一步的输入为提取主题清单、全部已采用原文及真实图片；完整提取稿保存在checkpoint中，未把生成断言传入最后一步。'
       if all(json.loads((r / 'dataset_manifest.json').read_text()).get('config', {}).get('model_config', {}).get('final_draft_outline_only') for r in runs) else ''))

source = nb.cells[5].source
source = re.sub(r"^MODE = .*$", "MODE = 'view_saved'  # 只查看明确指定的最终结果，不新增模型调用", source, flags=re.M)
source = re.sub(r"^SAVED_RUN = .*$", "SAVED_RUN = ROOT / '" + str(runs[0].relative_to(root)) + "'", source, flags=re.M)
source = re.sub(r'^(SAVED_HOLDOUT_RUN|SAVED_REVIEW_RUNS) = .*\n?', '', source, flags=re.M)
if len(runs) > 1:
    source += '\nSAVED_REVIEW_RUNS = [' + ', '.join("ROOT / '" + str(r.relative_to(root)) + "'" for r in runs[1:]) + ']\n'
match = re.fullmatch(r'(.+)_v(\d+)', runs[0].name)
future = runs[0].parent / (match[1] + '_v' + str(int(match[2]) + 1))
while future.exists():
    number = int(future.name.rsplit('_v', 1)[1]) + 1
    future = future.parent / (match[1] + '_v' + str(number))
source = re.sub(r'^RUN = .*$', "RUN = ROOT / '" + str(future.relative_to(root)) + "'", source, flags=re.M)
nb.cells[5].source = source
nb.cells[-1].source = ("import importlib\nfrom curation.v4 import current_results\n"
    "importlib.reload(current_results)  # 仅刷新展示模块，不热加载业务算子\n"
    "from curation.v4.current_results import show_current_results\n"
    "show_current_results(RUN if MODE == 'execute' else SAVED_RUN, **VIEW)\n")
if len(runs) > 1:
    nb.cells[-1].source += "if MODE == 'view_saved':\n    for saved_review in SAVED_REVIEW_RUNS:\n        show_current_results(saved_review, **VIEW)\n"
for c in nb.cells:
    if c.cell_type == 'code':
        c.outputs = []
        c.execution_count = None
client = NotebookClient(nb, timeout=300, kernel_name='demiwtg', resources={'metadata': {'path': str(root)}})
client.execute()
errors = [o for c in nb.cells if c.cell_type == 'code' for o in c.outputs if o.output_type == 'error']
assert not errors
display = '\n'.join(o.get('data', {}).get('text/html', '') for c in nb.cells if c.cell_type == 'code' for o in c.outputs)
expected_images = expected_concepts = 0
for r in runs:
    assert hashlib.sha256((r / 'knowledge_base.jsonl').read_bytes()).hexdigest() == hashes[str(r)]
    assert len(list(r.glob('**/calls/*.request.json'))) == calls[str(r)]
    for line in (r / 'knowledge_base.jsonl').read_text().splitlines():
        row = json.loads(line)
        expected_concepts += 1
        assert '<h2>' + html.escape(row['concept']) + '</h2>' in display
        for topic in row['knowledge']:
            for para in topic['content']['paragraphs']:
                assert html.escape(para) in display
            for image in topic['content']['images']:
                assert image['image_id'] in display
                expected_images += 1
assert display.count('<figure>') == expected_images
assert '图片预览不可用' not in display
nbformat.write(nb, notebook)
report = {'notebook': str(notebook), 'runs': [str(r) for r in runs], 'concepts': expected_concepts,
    'images': expected_images, 'all_paragraphs_present': True, 'cell_errors': 0, 'new_model_calls': 0,
    'knowledge_hash_unchanged': True, 'mode': 'view_saved', 'kernel': 'demiwtg'}
for r in runs:
    (r / 'main_notebook_display_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
