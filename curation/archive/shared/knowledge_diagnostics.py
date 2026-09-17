"""Independent, provenance-bound knowledge diagnostics; never computes primary scores.

CLI: import-legacy --batch pilot|expansion20_v1; validate --batch ...; render.
Each evidence document is immutable. New audits are separate revisions referencing
an imported record; legacy judgments are not silently promoted to fresh audits.
"""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
import nbformat
from pipeline import ROOT, STATE, read
from review import esc, picture

OUT=STATE/'knowledge_diagnostics_v1'
PROTOCOL_FILES=[ROOT/'benchmark/t2i/prompts/judge_prompt_gen_v6.0_V2.md',ROOT/'benchmark/t2i/eval_score.py',ROOT/'benchmark/edit/prompts/judge_prompt_edit_qib_v2.2.md',ROOT/'benchmark/edit/prompts/codex_score_prompt_edit_v2.md',ROOT/'benchmark/edit/eval_codex_score.py']
STATUS={'met','unmet','unverified','not_applicable'}
REASONS={'legacy_reason_not_audited','output_visibility_failure','prerequisite_not_realized','question_observability_gap','reviewer_uncertainty','clear_visible_evidence','generation_contract_failure','conditional_not_applicable'}
MAP={'pass':'met','conflict':'unmet','unobservable':'unverified'}
LABEL={'met':'知识要求满足','unmet':'知识要求未满足','unverified':'未能验证','not_applicable':'不适用'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def objhash(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def ref(path):return {'path':str(Path(path).resolve()),'sha256':digest(path)}

def immutable(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(obj,ensure_ascii=False,indent=2)+'\n'
    if path.exists():
        if path.read_text()!=data:raise ValueError(f'Refuse overwrite; use a new revision: {path}')
    else:
        with path.open('x') as f:f.write(data)


def validate_record(r,case,verify_files=True):
    assert r['schema']=='knowledge-diagnostic-v1' and r['primary_score_role']=='excluded'
    assert r['question_id']==case['question_id'] and r['task']==case['task']
    assert r['case_sha256']==objhash(case)
    assert r['review_basis'] in {'legacy_import_not_reaudited','source_bound_visual_reaudit'}
    # Disallow promoting old booleans or diagnostic counts to official metrics.
    assert not ({'official_total','joint_pass','total_score','execution_pass'} & set(r))
    checks={k['id']:k for k in case['knowledge_checks']}
    assert len(r['items'])==len(checks) and {x['id'] for x in r['items']}==set(checks)
    source_ids={s['source_id'] for s in case['sources']}
    for x in r['items']:
        assert x['criterion']==checks[x['id']]['criterion']
        assert x['source_ids']==checks[x['id']]['source_ids'] and set(x['source_ids'])<=source_ids
        assert x['status'] in STATUS and x['evidence'].strip()
        assert x['reasons'] and set(x['reasons'])<=REASONS
        if x['status']=='met':assert not set(x['reasons']) & {'prerequisite_not_realized','output_visibility_failure','generation_contract_failure'}
        if x['status']=='not_applicable':assert x.get('applicability_evidence')
        assert x['not_an_internal_cause_claim'] is True
    if r['review_basis']=='source_bound_visual_reaudit':
        assert r['output_viewed'] and r.get('supersedes') and r['visibility_audited']
        assert all('legacy_reason_not_audited' not in x['reasons'] for x in r['items'])
    else:assert not r['output_viewed'] and not r['visibility_audited']
    if verify_files:
        for x in [r['result_ref'],r['legacy_review_ref']]+([r['output_ref']] if r['output_ref'] else []):assert digest(x['path'])==x['sha256']
        assert r['job_sha256']==objhash(next(j for j in read_jobs(STATE/r['batch']) if j['job_id']==r['job_id']))
    return True


def read_jobs(run):return [json.loads(l) for l in (run/'jobs.jsonl').read_text().splitlines() if l.strip()]


def import_legacy(batch):
    if batch not in {'pilot','expansion20_v1'}:raise ValueError('This importer only supports the two frozen legacy schemas')
    run=STATE/batch;cases=read(run/'cases.json')['cases'];by={c['question_id']:c for c in cases}
    rp=run/('reviews_combined.json' if batch=='pilot' else 'reviews.json');reviews=read(rp)['reviews'];jobs={j['job_id']:j for j in read_jobs(run)}
    records=[]
    for old in reviews:
        job=jobs[old['job_id']];c=by[job['question_id']];result_path=run/old['model']/'jobs'/job['job_id']/'result.json';result=read(result_path)
        output=ref(result['image']) if result['ok'] else None
        if output:assert output['sha256']==result['output_sha256']==old['output_sha256']
        r=dict(schema='knowledge-diagnostic-v1',batch=batch,question_id=c['question_id'],task=c['task'],model=old['model'],condition=job['condition'],job_id=job['job_id'],job_sha256=objhash(job),case_sha256=objhash(c),diagnostic_revision='legacy_import_v1',primary_score_role='excluded',review_basis='legacy_import_not_reaudited',reviewer=old.get('reviewer','legacy_unspecified'),output_viewed=False,visibility_audited=False,result_ref=ref(result_path),output_ref=output,legacy_review_ref=ref(rp),legacy_record_sha256=objhash(old),legacy_review=copy.deepcopy(old),generation_status='ok' if result['ok'] else 'contract_failure',question_issue={'status':'not_assessed','evidence':'旧输出判分不自动构成题目有效性审核。'},primary_score={'status':'not_attached','note':'旧case执行布尔与1–5画质不是bench200正式主分；本模块不生成或修改主分。'},observations=[],items=[])
        for k in c['knowledge_checks']:
            r['items'].append(dict(id=k['id'],criterion=k['criterion'],source_ids=k['source_ids'],observable_region=k.get('observable_region'),exceptions=k.get('exceptions',[]),status=MAP[old['knowledge'][k['id']]],evidence=old.get('observations') or '旧记录未提供逐项证据；待复核。',evidence_scope='shared_legacy_observation_not_individual_reassessment',reasons=['legacy_reason_not_audited'] if result['ok'] else ['generation_contract_failure'],not_an_internal_cause_claim=True))
        validate_record(r,c);records.append(r)
    assert len({(r['model'],r['job_id']) for r in records})==len(records)
    expected={(m,j) for m in ['bagel','gemini'] for j in jobs}
    assert {(r['model'],r['job_id']) for r in records}==expected
    immutable(OUT/batch/'imports.json',{'records':records})
    immutable(OUT/batch/'manifest.json',{'batch':batch,'cases_ref':ref(run/'cases.json'),'jobs_ref':ref(run/'jobs.jsonl'),'review_ref':ref(rp),'protocol_refs':[ref(p) for p in PROTOCOL_FILES],'contract':'primary protocols unchanged; independent diagnostics only','imported_outputs':len(records),'import_does_not_mean_reaudit':True})
    return records


def add_audit(batch,model,job_id,annotations,observations,question_issue=None):
    records=read(OUT/batch/'imports.json')['records'];old=next(r for r in records if (r['model'],r['job_id'])==(model,job_id));r=copy.deepcopy(old)
    r.update(diagnostic_revision='visual_reaudit_v1',supersedes=objhash(old),review_basis='source_bound_visual_reaudit',reviewer='assistant/root',review_mode='model and condition visible; old review read; not blind and not independent adjudication',output_viewed=True,visibility_audited=True,observations=observations)
    if question_issue:r['question_issue']=question_issue
    for x in r['items']:
        x.update(annotations[x['id']]);x['evidence_scope']='fresh_source_bound_visual_observation'
    c=next(c for c in read(STATE/batch/'cases.json')['cases'] if c['question_id']==r['question_id'])
    validate_record(r,c)
    immutable(OUT/batch/'reaudits'/f'{model}__{job_id}__v1.json',r)
    return r


def load_effective(batch):
    originals=read(OUT/batch/'imports.json')['records'];lookup={(r['model'],r['job_id']):r for r in originals}
    for p in sorted((OUT/batch/'reaudits').glob('*.json')):
        r=read(p);key=(r['model'],r['job_id']);assert r['supersedes']==objhash(lookup[key]);lookup[key]=r
    return list(lookup.values())


def validate_batch(batch):
    manifest=read(OUT/batch/'manifest.json')
    for x in [manifest['cases_ref'],manifest['jobs_ref'],manifest['review_ref']]+manifest['protocol_refs']:assert digest(x['path'])==x['sha256']
    cs={c['question_id']:c for c in read(STATE/batch/'cases.json')['cases']}
    rows=load_effective(batch)
    for r in rows:validate_record(r,cs[r['question_id']])
    return {'outputs':len(rows),'fresh_visual_audits':sum(r['output_viewed'] for r in rows),'original_inputs_and_primary_protocols_unchanged':True}


def render():
    batches=[b for b in ['pilot','expansion20_v1'] if (OUT/b/'imports.json').exists()]
    stats={b:validate_batch(b) for b in batches}
    intro='# 独立知识诊断 · 保留旧任务评分\n\n此处不产出任务主分、不修改旧判官输入／公式。知识项用于解释能力表现，不能计入旧总分。主评分若尚未附加就显示未关联，不能把旧case的execution_pass或quality冒称bench200分数。\n\n'
    intro+='## 实际完成范围\n\n'+ '\n'.join(f'- {b}：整理旧记录{s["outputs"]}个输出；其中{s["fresh_visual_audits"]}个输出本次实际复看。' for b,s in stats.items())
    intro+='\n\n机械整理与重新看图分开标识。未复看的输出保持旧判断，不自动补造不可观察原因，也不算新审核。以下3题全部模型／主对照均复看，共12图；只验证诊断记录方法，不代表随机抽样或总体模型准确率。模型、条件及旧记录可见，不称盲审或独立裁决。\n\n诊断区分：知识要求未满足、非知识执行观察、题目缺口、审核不确定。观察标签可并存，不断言模型内部原因。完整来源、实际输入和原图仍在原case notebook；下方同时展示本次复看的原图及知识依据。\n\n'
    intro+='对应主协议：[T2I]('+str(PROTOCOL_FILES[0])+')、[编辑]('+str(PROTOCOL_FILES[2])+')。固定协议哈希在各批manifest；本次已验证未改变。新出题会话可以沿用这些诊断字段，但无须采用废弃的统一主分草案。\n'
    cells=[nbformat.v4.new_markdown_cell(intro)]
    for batch in batches:
        allrows=load_effective(batch);cs={c['question_id']:c for c in read(STATE/batch/'cases.json')['cases']}
        for qid in dict.fromkeys(r['question_id'] for r in allrows if r['output_viewed']):
            c=cs[qid];rr=[r for r in allrows if r['question_id']==qid]
            text='<style>table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}img{max-width:100%;max-height:650px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            text+='<h2>'+esc(c['concept'])+' · '+esc(c['task'])+'</h2><p>'+esc(c['taxonomy_record'])+'</p><h3>中文题面</h3><p>'+esc(c['prompt_zh'])+'</p><details><summary>实际英文原题</summary><pre>'+esc(c['prompt'])+'</pre></details>'
            if c.get('edit_source'):text+=picture(c['edit_source']['path'],'编辑原图；仅作场景，不作知识事实证据')
            text+='<details><summary>来源、判据与参考图片支持范围</summary><pre>'+esc(c['sources'])+'</pre><pre>'+esc(c['knowledge_checks'])+'</pre>'
            for im in c['reference_images']:text+=picture(im['path'],im.get('support_scope','知识参考图'))
            text+='</details><p>任务主分：未关联。下方均为知识能力诊断及非计分观察。</p>'
            for r in rr:
                text+='<h3>'+esc(r['model']+' / '+r['condition'])+'</h3>'+picture(r['output_ref']['path'],'本次实际复看的输出')
                text+='<table><tr><th>判据</th><th>知识诊断</th><th>可见证据</th><th>未确认原因／依据</th></tr>'
                for x in r['items']:text+='<tr><td>'+esc(x['id']+' '+x['criterion'])+'</td><td>'+esc(LABEL[x['status']])+'</td><td>'+esc(x['evidence'])+'</td><td>'+esc(x['reasons'])+'</td></tr>'
                text+='</table><p>附加观察（不产生主分）：</p><pre>'+esc(r['observations'])+'</pre><p>题目／材料复审：</p><pre>'+esc(r['question_issue'])+'</pre><details><summary>历史判断与审计指纹</summary><pre>'+esc({'old_knowledge':r['legacy_review']['knowledge'],'old_note':r['legacy_review'].get('observations'),'basis':r['review_mode'],'image':r['output_ref'],'supersedes':r['supersedes']})+'</pre></details>'
            cells.append(nbformat.v4.new_code_cell('# 已保存的只读诊断展示；生成器：curation/knowledge_application_v1/knowledge_diagnostics.py',execution_count=len(cells),outputs=[nbformat.v4.new_output('display_data',data={'text/html':text,'text/plain':c['concept']+'诊断记录'})]))
    nb=nbformat.v4.new_notebook(cells=cells);nbformat.validate(nb)
    path=OUT/'diagnostic_cases_zh.ipynb';nbformat.write(nb,path)
    immutable(OUT/'validation_v1.json',stats)
    print(json.dumps({'notebook':str(path),'validation':stats},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['import-legacy','validate','render']);ap.add_argument('--batch',choices=['pilot','expansion20_v1']);a=ap.parse_args()
    if a.action=='render':render()
    else:
        if not a.batch:ap.error('--batch required')
        print(len(import_legacy(a.batch)) if a.action=='import-legacy' else validate_batch(a.batch))
