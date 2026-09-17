"""Independent third development batch: freeze, run Gemini, render case notebook."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
import base64
from collections import Counter
import io
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ROOT, STATE, read
from bagel_runner import publish, encoded, sha, validate_jobs
from review import esc
from case_input_details import actual_input_details

RUN = STATE / 'version3_20'
LABELS = {'baseline':'无资料', 'text':'仅文字', 'image':'仅图片',
          'multimodal':'图文', 'explicit_target':'显式视觉要求（诊断）'}
STATUS = {'pass':'通过', 'conflict':'冲突', 'unobservable':'不可观察'}
TYPES = {'特征与结构','属性与状态','功能与机制','过程与变化','关系与组织','规则与约定'}


def picture(path,label):
    """Embed a compact viewing copy; preserve and link original source/output bytes."""
    from PIL import Image, ImageOps
    p=Path(path)
    with Image.open(p) as original:
        im=ImageOps.exif_transpose(original).convert('RGB')
        im.thumbnail((1254,1254))
        buf=io.BytesIO();im.save(buf,format='JPEG',quality=92,optimize=True,subsampling=0)
    return ('<figure><a href="'+esc(str(p))+'"><img loading="lazy" src="data:image/jpeg;base64,'+
        base64.b64encode(buf.getvalue()).decode()+'"></a><figcaption>'+esc(label)+
        ' · <a href="'+esc(str(p))+'">打开原始图</a>（内嵌JPEG浏览副本；原图及评分哈希不变）</figcaption></figure>')


def table(head, rows):
    return '<table><tr>'+''.join('<th>'+esc(h)+'</th>' for h in head)+'</tr>'+''.join(
        '<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</table>'


def freeze():
    cases = read(RUN/'prepared_cases.json')['cases']
    assert len(cases)==20 and Counter(c['task'] for c in cases)=={'edit':10,'t2i':10}
    assert len({c['question_id'] for c in cases})==20
    jobs=[]
    for c in cases:
        assert c['knowledge_text_zh'] and c['knowledge_text'] and c['prompt_zh'] and c['prompt']
        assert set(c['knowledge_types']) <= TYPES
        assert c['application_level'] in {'direct','conditional','relational','compositional'}
        assert c['sources'] and c['knowledge_checks'] and c['execution_checks']
        assert c['preflight_review']['accepted'] is True
        source_ids={s['source_id'] for s in c['sources']}
        assert len({k['id'] for k in c['knowledge_checks']})==len(c['knowledge_checks'])
        for k in c['knowledge_checks']:
            assert k['criterion'] and k['source_ids'] and set(k['source_ids']) <= source_ids
        for s in c['sources']:
            assert s['url'] and s['quote'] and s['support_scope']
            assert s.get('snapshot_path'), 'A locally inspectable source snapshot is required'
            raw=Path(s['snapshot_path']).read_bytes()
            assert len(raw)>40, 'An empty/placeholder source snapshot is not evidence'
            s['snapshot_sha256']=sha(raw)
        for im in c['reference_images']+([c['edit_source']] if c['task']=='edit' else []):
            p=Path(im['path']); assert p.is_absolute() and p.exists()
            im['sha256']=sha(p.read_bytes())
        for im in c['reference_images']:
            assert im['support_scope'] and im['region'] and im['limitations']
            assert im['source_id'] in source_ids and im['visual_review']['reviewed'] is True
        if c['task']=='edit':
            assert c['edit_source']['visual_review']['reviewed'] is True
            assert all(im['sha256']!=c['edit_source']['sha256'] for im in c['reference_images'])
        c['conditions']=['baseline','text','image','multimodal'] if c['reference_images'] else ['baseline','text']
        if c.get('diagnostic_selected'):
            assert c['explicit_target'] and c['explicit_target_zh']
            c['conditions'].append('explicit_target')
        for condition in c['conditions']:
            images=[]
            if c['task']=='edit':
                images.append({k:c['edit_source'][k] for k in ('path','sha256')}|{'role':'edit_source'})
            if condition in {'image','multimodal'}:
                images.extend({k:im[k] for k in ('path','sha256')}|{'role':'retrieval_reference'} for im in c['reference_images'])
            prompt=('TASK\n'+c['prompt']+'\n\nProduce one image fulfilling the task. '
                    'An edit_source image is the scene to edit; retrieval_reference images are knowledge materials, '
                    'not the target composition. Use relevant supplied knowledge without copying source-page layouts. '
                    'Preserve non-target content for editing. Do not add explanatory text unless the task asks for it.')
            if condition in {'text','multimodal'}:
                prompt+='\n\nSOURCE MATERIALS\n'+c['knowledge_text']
            if condition=='explicit_target':
                prompt+='\n\nEXPLICIT VISUAL REQUIREMENTS (DIAGNOSTIC)\n'+c['explicit_target']
            jobs.append(dict(job_id=c['question_id']+'__'+condition+'__r1',question_id=c['question_id'],
                             task=c['task'],condition=condition,images=images,prompt=prompt,seed=20260914))
    publish(RUN/'cases.json',encoded({'cases':cases}))
    publish(RUN/'jobs.jsonl',''.join(json.dumps(j,ensure_ascii=False)+'\n' for j in jobs).encode())
    validate_jobs(RUN/'jobs.jsonl')
    protocol=dict(version='third-development-20',cases=20,tasks=dict(Counter(c['task'] for c in cases)),
        domains=dict(Counter(c['domain'] for c in cases)),application_levels=dict(Counter(c['application_level'] for c in cases)),
        reference_image_cases=sum(bool(c['reference_images']) for c in cases),
        diagnostic_cases=[c['question_id'] for c in cases if c.get('diagnostic_selected')],
        jobs_per_model=len(jobs),gemini_request_cap=len(jobs),models=['BAGEL-7B-MoT','openrouter/google/gemini-3.1-flash-image'],
        repeats=1,automatic_paid_retries=0,seed_note='BAGEL paired seed; Gemini provider seed not supported',
        edit_source_preparation={'imagegen_scenes':6,'code_native_diagrams':4,
            'purpose':'Only initial scenes, never factual reference evidence or scored model outputs.',
            'imagegen_cost':'Built-in tool does not return a billed-cost field; excluded from Gemini gateway cost.'},
        materials='Preselected verified source materials; no automatic retrieval, no finetuning.',
        scope='Development only; preserve every result and invalid case; no selection by observed model gain.',
        review='Assistant source-bound visual review, not human gold; knowledge, execution, joint success, quality separate.',
        detailed_scoring={'knowledge':'Every criterion: pass/conflict/unobservable + reason. Descriptive numeric display: pass=1, others=0; unobservable retained separately.',
            'execution':'Every frozen execution item: pass/conflict/unobservable + reason. All must pass for execution_pass.',
            'quality':{'clarity':'1-5: legibility/focus at intended viewing scale',
                       'artifacts':'1-5: visible rendering defects, excluding scientific factual correctness',
                       'coherence':'1-5: internal visual/material consistency appropriate to photo or diagram'},
            'quality_anchors':{'1':'严重缺陷，难以查看','2':'明显缺陷','3':'可用但有局部问题','4':'良好，小缺陷','5':'清楚、完整，无明显缺陷'},
            'aggregation':'Keep knowledge, execution, joint pass and quality separate; no blended grand score.'},
        optional_criteria='A criterion beginning with if/若 is satisfied when the optional content is absent; state that explicitly, without claiming the unshown knowledge was demonstrated.',
        image_ablation='Image-only has source pixels and role labels only, no added captions or source-support prose.',
        split_policy='Concept/rule family and source/near-duplicate isolation required before any formal train/test use.',
        training_record='Original question + source materials + necessary application relation + separately verified target; outputs not automatically training targets.')
    publish(RUN/'protocol.json',encoded(protocol))
    print(json.dumps(protocol,ensure_ascii=False,indent=2))


def generate():
    jobs,_=validate_jobs(RUN/'jobs.jsonl')
    protocol=read(RUN/'protocol.json')
    assert len(jobs)==protocol['gemini_request_cap']
    plans=[jobs[i::4] for i in range(4)]
    assert all(len(p)<=48 for p in plans)
    publish(RUN/'gemini_shard_plan.json',encoded({'total_request_cap':len(jobs),'shards':[[j['job_id'] for j in p] for p in plans]}))
    processes=[]
    for i,plan in enumerate(plans):
        out=RUN/f'gemini_shard_{i}';out.mkdir(parents=True,exist_ok=True)
        with (out/'launch.log').open('ab') as log:
            cmd=[sys.executable,str((_ARCHIVE_ROOT/'curation/archive/shared/gemini_runner.py')),'--jobs',str(RUN/'jobs.jsonl'),
                 '--out',str(out),'--limit',str(len(plan)),'--job-ids',','.join(j['job_id'] for j in plan)]
            processes.append((subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT),out))
    codes=[]
    for process,out in processes:
        codes.append(process.wait())
        for d in (out/'jobs').glob('*'):
            for src in d.rglob('*'):
                dst=RUN/'gemini/jobs'/d.name/src.relative_to(d)
                if src.is_dir():dst.mkdir(parents=True,exist_ok=True);continue
                dst.parent.mkdir(parents=True,exist_ok=True)
                if dst.exists():assert sha(dst.read_bytes())==sha(src.read_bytes())
                else:os.link(src,dst)
    results=[read(p) for p in (RUN/'gemini/jobs').glob('*/result.json')]
    publish(RUN/'gemini_run_result.json',encoded(dict(exit_codes=codes,results=len(results),ok=sum(r['ok'] for r in results),
        reported_cost_usd=sum(r.get('usage',{}).get('cost',0) or 0 for r in results),automatic_retries=0)))
    if any(codes):raise SystemExit(1)


CSS='<style>.v3case{font:16px/1.65 sans-serif;max-width:1400px}.v3case pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f7;padding:12px}.v3case table{border-collapse:collapse;width:100%;margin:12px 0}.v3case td,.v3case th{border:1px solid #ccd;padding:9px;text-align:left;vertical-align:top}.v3case img{max-width:100%;max-height:760px}.v3case figure{margin:12px 0}.v3case details{margin:14px 0}</style>'


def render():
    cases=read(RUN/'cases.json')['cases']
    clarifications=read(RUN/'rubric_clarifications.json') if (RUN/'rubric_clarifications.json').exists() else {}
    reviews=read(RUN/'reviews.json')['reviews'] if (RUN/'reviews.json').exists() else []
    lookup={(r['model'],r['job_id']):r for r in reviews}
    second={r['review_id']:r for r in read(RUN/'secondary_review.json')['reviews']} if (RUN/'secondary_review.json').exists() else {}
    supplemental=read(RUN/'supplemental_instruction_reviews.json') if (RUN/'supplemental_instruction_reviews.json').exists() else {'reviews':[]}
    supplemental_by_id={r['review_id']:r for r in supplemental['reviews']}
    cells=[dict(cell_type='markdown',id='intro',metadata={},source='# 第3版：20道知识应用开发题\n\n10编辑＋10文生图。知识、执行保持、联合通过与画质分开。资料为预选来源，不是自动检索；无微调。所有输出与失败保留，评分由助手完成，非人工金标准。每题下方显示实际完整输入与逐项评分。\n')]
    cells[0]['source']+='\n覆盖11个主域；5直接、6条件、3关系、6组合。16题比较无资料／文字／图片／图文，4题比较无资料／文字；6道预选题另做显式视觉要求诊断。仅图片条件仍保留原图内嵌的文字与标签，不等于无OCR信息。\n\n知识及执行细项通过记1，其余记0，同时保留冲突／不可观察及理由；不可观察不等于知识错误。全部知识项通过不自动代表整题成功，联合通过还要求全部执行保持项通过。可选“若”项未画时记录无可见冲突，不声称已经展示该知识。\n\n画质每项1–5：1严重缺陷，2明显缺陷，3可用但有局部问题，4良好有小缺陷，5无明显缺陷；按照片或示意图各自形式评价，不与知识相加。\n'
    if supplemental['reviews']:
        cells[0]['source']+='\n评分完整性说明：'+supplemental['note_zh']+'\n'
    if (RUN/'findings.json').exists():
        f=read(RUN/'findings.json');counts=f['detailed_rating_counts']
        cells[0]['source']+=f'\n已完成{f["outputs"]}张输出的助手审核，共{counts["knowledge"]}项知识判定、{counts["execution"]}项执行判定、{counts["quality"]}项画质评分。Gemini实际为openrouter/google/gemini-3.1-flash-image，网关回报费用${f["gemini_cost_usd"]:.4f}；6张imagegen原图未返回计费字段，未计入该费用。原Qwen服务及预标注已恢复并核验进度继续增长。\n'
    for n,c in enumerate(cases,1):
        taxonomy=c['taxonomy_record']
        if c.get('library_locator'):
            taxonomy=dict(c['library_locator'])
            taxonomy['taxonomy_paths']=taxonomy.get('actual_taxonomy_paths',[])
            taxonomy['display_note']='从冻结记录内的library_locator展示真实挂载；归一化taxonomy_record漏读了该字段，仅纠正展示，题目及请求不变。'
        paths=taxonomy.get('taxonomy_paths') or taxonomy.get('taxonomy') or []
        path='；'.join(paths) if paths else '库内未挂载；语义定位：'+str(taxonomy.get('suggested_semantic_path',c['domain']))
        out=[CSS,'<article class="v3case"><h2>'+esc(f'{n:02d} · {path} · {c["concept"]}')+'</h2>']
        dims=c.get('scene_dimensions',{})
        out.append(table(['主域','知识内容','题型','应用层次','资料形态','场景复杂来源','组合类型','前提类别'],[[
            c['domain'],'、'.join(c['knowledge_types']),c['task'],c['application_level'],
            '图文' if c['reference_images'] else '文字',dims.get('scene_types',[]),dims.get('combo_type'),dims.get('premise_types',[])]]))
        for title,key in [('中文题面','prompt_zh'),('实际英文题面','prompt')]:
            out.append('<h3>'+title+'</h3><pre>'+esc(c[key])+'</pre>')
        if c['task']=='edit':
            out.extend(['<h3>编辑原图</h3>',picture(c['edit_source']['path'],'edit_source：'+str(c['edit_source'].get('observations',''))),
                        '<p>原图来源与生成属性：</p><pre>'+esc(c['edit_source'])+'</pre>'])
        out.append('<h3>完整中文知识译文</h3><pre>'+esc(c['knowledge_text_zh'])+'</pre><h3>实际完整英文知识输入</h3><pre>'+esc(c['knowledge_text'])+'</pre>')
        out.append('<p>文字为依据原文整理的资料，不是逐字引用。仅图片条件不附加这些文字；仅文字条件不输入以下参考图。</p>')
        for im in c['reference_images']:
            out.append(picture(im['path'],'retrieval_reference：'+im['support_scope']))
            out.append(table(['可见支持区域','支持范围','不能证明什么','与文字互补'],[[im['region'],im['support_scope'],im['limitations'],im.get('complementarity','见完整文字适用条件')]]))
            out.append('<details><summary>参考图来源及审核记录</summary><pre>'+esc(im)+'</pre></details>')
        if not c['reference_images']:out.append('<p>本题无知识参考图，不强行配图。</p>')
        out.append('<h3>知识来源与库内定位</h3><pre>'+esc(taxonomy)+'</pre>')
        for s in c['sources']:
            out.append('<p><a href="'+esc(s['url'])+'">'+esc(s.get('title',s['source_id']))+'</a></p><blockquote>'+esc(s['quote'])+'</blockquote><p>'+esc(s['support_scope'])+'</p><details><summary>来源版本、定位与快照</summary><pre>'+esc(s)+'</pre></details>')
        for title,key in [('条件→知识→可见结果','application_chain'),('合理例外','exceptions'),('证据缺口与局限','gaps')]:
            out.append('<h3>'+title+'</h3><pre>'+esc(c.get(key,c.get('application_links',[]) if key=='application_chain' else []))+'</pre>')
        out.append('<h3>冻结的知识判据</h3>'+table(['判据全文','证据来源'],[[k['criterion'],'、'.join(k['source_ids'])] for k in c['knowledge_checks']]))
        out.append('<h3>执行与编辑保持判据</h3><pre>'+esc(c['execution_checks'])+'</pre>')
        if c['question_id'] in clarifications:
            out.append('<p><b>判据口径说明（冻结阈值不变）：</b>'+esc(clarifications[c['question_id']]['note_zh'])+'</p>')
        for model in ['bagel','gemini']:
            for cond in c['conditions']:
                jid=c['question_id']+'__'+cond+'__r1'
                out.append('<h3>'+esc(model.upper()+' · '+LABELS[cond])+'</h3>')
                p=RUN/model/'jobs'/jid/'result.json'
                if p.exists():
                    r=read(p)
                    if r.get('ok'):
                        assert sha(Path(r['image']).read_bytes())==r['output_sha256']
                        out.append(picture(r['image'],model+' · '+LABELS[cond]))
                    else:out.append('<p>生成失败，保留原记录，无付费重试。</p><pre>'+esc(r.get('error'))+'</pre>')
                else:out.append('<p>尚未生成。</p>')
                rv=lookup.get((model,jid))
                rows=[]
                for k in c['knowledge_checks']:
                    status=rv['knowledge'][k['id']] if rv else None
                    reason=(rv.get('knowledge_reasons',{}).get(k['id']) or rv.get('observations')) if rv else '尚未审核'
                    rows.append([k['criterion'],(1 if status=='pass' else 0) if rv else '—',STATUS.get(status,'待审核'),reason])
                out.append(table(['判据全文','通过值','通过／冲突／不可观察','理由'],rows))
                if rv:
                    out.append('<p>知识细项：'+str(sum(v=='pass' for v in rv['knowledge'].values()))+'/'+str(len(rv['knowledge']))+'通过；冲突 '+str(sum(v=='conflict' for v in rv['knowledge'].values()))+'，不可观察 '+str(sum(v=='unobservable' for v in rv['knowledge'].values()))+'。通过记1，其余记0仅用于通过数，不可观察不等于知识错误。</p>')
                    erows=[]
                    for i,criterion in enumerate(c['execution_checks'],1):
                        item=rv['execution_items'][f'E{i}']
                        erows.append([criterion['criterion'] if isinstance(criterion,dict) else criterion,1 if item['status']=='pass' else 0,STATUS[item['status']],item['reason']])
                    out.append('<h4>执行与保持细项</h4>'+table(['判据全文','通过值','判定','理由'],erows))
                    out.append('<p>执行／保持细项：'+str(sum(e['status']=='pass' for e in rv['execution_items'].values()))+'/'+str(len(rv['execution_items']))+'通过。</p>')
                    qnames={'clarity':'清晰度／可读性','artifacts':'伪影控制','coherence':'视觉连贯性'}
                    out.append('<h4>独立画质细项</h4>'+table(['维度','分数（1–5）','理由'],[[qnames[k],v['score'],v['reason']] for k,v in rv['quality_items'].items()]))
                    out.append(table(['独立维度','结果','理由'],[
                        ['执行／编辑保持','通过' if rv['execution_pass'] else '未通过',rv.get('execution_reason',rv.get('observations'))],
                        ['按冻结判据：知识＋执行联合','通过' if rv['execution_pass'] and all(x=='pass' for x in rv['knowledge'].values()) else '未通过','联合要求全部知识项及执行项通过'],
                        ['画质',str(rv.get('quality'))+'/5',rv.get('quality_reason','')]]))
                    out.append('<p>审核身份：'+esc(rv['reviewer'])+'</p>')
                    if rv.get('review_id') in supplemental_by_id:
                        sr=supplemental_by_id[rv['review_id']]
                        out.append('<h4>题面完整性补充审核（出图后独立口径）</h4><p>'+esc(supplemental['note_zh'])+'</p>'+table(['完整要求','判定','理由'],[[sr['criterion_zh'],STATUS[sr['status']],sr['reason']]]))
                    if rv.get('review_id') in second:
                        out.append('<p><b>助手二次复核：</b>'+esc(second[rv['review_id']]['finding'])+'</p>')
        out.append(actual_input_details(c,'version3_20'))
        out.append('<details><summary>出图前审核与显式诊断要求</summary><pre>'+esc({k:c.get(k) for k in ['preflight_review','diagnostic_selected','explicit_target_zh','explicit_target']})+'</pre></details></article>')
        html=''.join(out)
        card=RUN/'notebook_cards'/f'case-{n:02}.html'
        card.parent.mkdir(parents=True,exist_ok=True);card.write_text(html)
        code='from pathlib import Path\nfrom IPython.display import display, HTML\ndisplay(HTML(Path('+repr(str(card))+').read_text()))'
        cells.append(dict(cell_type='code',id=f'case-{n:02}',metadata={'jupyter':{'source_hidden':True}},execution_count=n,
            source=code,
            outputs=[dict(output_type='display_data',metadata={},data={'text/html':html,'text/plain':c['concept']})]))
    nb=dict(cells=cells,metadata={'kernelspec':{'display_name':'demiwtg','language':'python','name':'demiwtg'},'language_info':{'name':'python'}},nbformat=4,nbformat_minor=5)
    from notebook_frontmatter import apply as apply_frontmatter
    apply_frontmatter(nb, 'version3_20')
    (RUN/'cases_review_zh.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=1))
    print(f'Rendered {len(cases)} cases, {len(reviews)} output reviews')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','generate','render'])
    action=parser.parse_args().action
    {'freeze':freeze,'generate':generate,'render':render}[action]()
