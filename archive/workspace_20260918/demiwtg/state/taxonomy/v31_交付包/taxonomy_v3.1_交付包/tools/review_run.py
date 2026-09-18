# -*- coding: utf-8 -*-
"""Layer-1+2 skeleton review by three models, back-to-back, fixed JSON output.
打包版：自包含（不依赖 ab_test.py），API 密钥从环境变量读取。

用法:
    export OPENROUTER_API_KEY=sk-or-v1-xxxx
    export PROXY=http://127.0.0.1:7891   # 可选，OpenRouter 走本地代理
    python3 review_run.py

说明:
    - 三模型背靠背评审骨架前两层（领域 + 二级分支），温度 0.3。
    - claude 系是 thinking 模型，须 max_tokens 12000 + reasoning:minimal。
    - 记录 finish_reason 防截断。产物写入 review_artifacts/。
    - SKEL/WS 路径请按实际部署位置修改。
"""
import json, os, re, time, threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OR_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OR_URL = 'https://openrouter.ai/api/v1/chat/completions'

WS = os.environ.get('REVIEW_WS', '/Users/meng/qwenwork/review_L1')
SKEL = os.environ.get('REVIEW_SKEL', '/Users/meng/qwenwork/世界目录_骨架全览.json')
MODELS = ['google/gemini-3.7-flash', 'anthropic/claude-fable-5', 'openai/gpt-5.6-sol']

SYSTEM = ('你是一位知识体系架构评审专家。你在评审一个多模态图文标注用的'
          '「世界知识标签体系」的前两层结构（领域层 + 二级分支层）。'
          '请给出严格、具体、可执行的意见，不要泛泛而谈。只输出严格 JSON。')

USER = '''【背景】
「融合世界标签体系」是用于多模态图文数据标注的树状标签体系：领域 / 分支 / … / 叶子，叶子下挂具体、可标注的实例，中英双语，现有约 16,600 个叶子、约 34.6 万实例。
目标：建成完整的世界知识目录，覆盖世界所有地区（欧洲、东亚、南亚与东南亚、中东、非洲、美洲、大洋洲）的知识。

【第一层与第二层结构】
格式：序号 领域名(叶子数)：二级分支列表，(空)=暂无内容的预留分支。

{inject}

【评审任务】只评审领域层与二级分支层，不要深入更细的层级。
1. 缺失：要成为完整世界知识目录，还缺哪些领域或二级分支？每条须说明为什么现有结构容纳不下它。
2. 错挂：哪些二级分支不属于它现在所在的领域？应该挪去哪里？
3. 边界冲突：哪些领域之间、或二级分支之间边界不清、实例会打架？给出裁决规则。
4. 合并/拆分/更名：哪些领域或二级分支需要合并、拆分、改名？
5. 命名与英译：哪些名称不准确，或难以翻译成地道的英文？给出中英双语建议。
6. 世界覆盖：这套划分是否天然覆盖世界各地区？哪里仍会偏向某一地区或某种文化视角？
7. 结构平衡：最大域 3822 叶、多个分支为空，有无结构性风险？

【约束】
- 建议限定在领域与二级分支粒度，不要涉及叶子和实例。
- 新增领域建议不超过 5 个，须确有必要。
- 每类意见最多 5 条，按重要性排序。

【输出】只输出一个 JSON 对象，不要任何其他文字：
{{
  "missing": [{{"level":"domain|l2","under":"所属领域或空","zh":"","en":"","why":""}}],
  "misplaced": [{{"name":"","from":"","to":"","why":""}}],
  "boundary_conflicts": [{{"pair":["",""],"issue":"","fix":""}}],
  "merge_split": [{{"target":"","action":"merge|split|rename","detail":""}}],
  "naming_issues": [{{"name":"","issue":"","suggested_zh":"","suggested_en":""}}],
  "coverage_gaps": [{{"region_or_theme":"","issue":""}}],
  "balance_comments": "",
  "overall": ""
}}'''


def http_json(url, key, payload, timeout=120):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
        'X-OpenRouter-Title': 'taxonomy-review'})
    if 'openrouter.ai' in url:
        proxy = os.environ.get('PROXY', 'http://127.0.0.1:7891')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy, 'http': proxy}))
    else:
        opener = urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def build_inject():
    skel = json.load(open(SKEL, encoding='utf-8'))
    lines = []
    for i, (dom, l2s) in enumerate(skel.items(), 1):
        lv = sum(v.get('leaves', 0) for v in l2s.values())
        names = [n if v.get('leaves', 0) > 0 else n + '(空)' for n, v in l2s.items()]
        lines.append(f'{i:02d} {dom}({lv})：' + '、'.join(names))
    return '\n'.join(lines)


def extract_obj(txt):
    s = txt.find('{'); e = txt.rfind('}')
    if s < 0 or e <= s: return None
    try:
        return json.loads(txt[s:e+1])
    except Exception:
        t = re.sub(r',\s*([}\]])', r'\1', txt[s:e+1])
        try: return json.loads(t)
        except Exception: return None


def call(model, messages):
    payload = {'model': model, 'temperature': 0.3, 'max_tokens': 12000, 'messages': messages}
    if model.startswith(('google/', 'anthropic/')):
        payload['reasoning'] = {'effort': 'minimal'}
    t0 = time.time()
    j = http_json(OR_URL, OR_KEY, payload, timeout=600)
    ch = j['choices'][0]
    txt = ch['message'].get('content')
    if txt is None:
        raise RuntimeError('content=None finish=' + str(ch.get('finish_reason'))
                           + ' resp=' + json.dumps(j, ensure_ascii=False)[:300])
    return txt, time.time() - t0, ch.get('finish_reason')


def main():
    assert OR_KEY, '请设置环境变量 OPENROUTER_API_KEY'
    os.makedirs(WS, exist_ok=True)
    prompt = USER.format(inject=build_inject())
    json.dump({'system': SYSTEM, 'user': prompt}, open(f'{WS}/prompt.json', 'w'), ensure_ascii=False, indent=1)
    print('prompt chars:', len(prompt))
    lock = threading.Lock()

    def run(model):
        tag = model.split('/')[-1]
        try:
            txt, dt, fr = call(model, [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}])
            obj = extract_obj(txt)
            json.dump({'model': model, 'lat': dt, 'finish': fr, 'raw': txt, 'parsed': obj},
                      open(f'{WS}/{tag}.json', 'w'), ensure_ascii=False, indent=1)
            with lock:
                print(f"[{tag}] lat={dt:.0f}s parsed={obj is not None} "
                      f"missing={len(obj.get('missing',[])) if obj else '-'}")
        except Exception as e:
            with lock:
                print(f"[{tag}] ERR {str(e)[:200]}")
            json.dump({'model': model, 'err': str(e)[:500]}, open(f'{WS}/{tag}.json', 'w'), ensure_ascii=False)

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(run, MODELS))


if __name__ == '__main__':
    main()
