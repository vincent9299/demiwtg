"""Summarize complete reviewed third-batch outputs without post-hoc filtering."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from version3_20 import RUN, render
from pipeline import read
from bagel_runner import publish,encoded,sha


def rates(rows):
    return dict(total=len(rows),knowledge_all_pass=sum(r['knowledge_pass'] for r in rows),
        execution_all_pass=sum(r['execution_pass'] for r in rows),joint_pass=sum(r['joint_pass'] for r in rows),
        knowledge_items=dict(Counter(v for r in rows for v in r['knowledge'].values())),
        quality_mean=round(sum(r['quality'] for r in rows if r['quality'] is not None)/max(1,sum(r['quality'] is not None for r in rows)),3))


def main():
    cases=read(RUN/'cases.json')['cases'];summary=read(RUN/'scores_summary.json');protocol=read(RUN/'protocol.json')
    assert summary['expected']==summary['reviewed']==protocol['jobs_per_model']*2
    assert len(cases)==20
    rows=summary['per_output'];bykey={(r['model'],r['question_id'],r['condition']):r for r in rows}
    reference_cases={c['question_id'] for c in cases if c['reference_images']}
    main_groups={};matched={};diagnostics={};paired=[]
    for model in ['bagel','gemini']:
        baseline=[bykey[model,c['question_id'],'baseline'] for c in cases]
        supplied=[bykey[model,c['question_id'],'multimodal' if c['reference_images'] else 'text'] for c in cases]
        main_groups[model]={'baseline':rates(baseline),'preselected_supplied':rates(supplied),
            'supplied_definition':'16 image-supported cases use multimodal; 4 text-only cases use text. Preselected before outputs.'}
        matched[model]={condition:rates([r for r in rows if r['model']==model and r['condition']==condition and r['question_id'] in reference_cases])
            for condition in ['baseline','text','image','multimodal']}
        diagnostics[model]={condition:rates([bykey[model,c['question_id'],condition] for c in cases if c.get('diagnostic_selected')])
            for condition in ['baseline','text','image','multimodal','explicit_target']}
        for b,s in zip(baseline,supplied):
            paired.append(dict(model=model,question_id=b['question_id'],baseline_knowledge=b['knowledge_pass'],supplied_knowledge=s['knowledge_pass'],
                baseline_joint=b['joint_pass'],supplied_joint=s['joint_pass']))
    session=read(RUN/'bagel/session/session.json')
    assert session['stage']=='completed' and session['preannotation_restored'] is True
    events=[json.loads(l) for l in (RUN/'bagel/session/events.jsonl').read_text().splitlines()]
    restore=next(e for e in events if e['stage']=='restoration_verified')
    results=[]
    for model in ['bagel','gemini']:
        a=[read(p) for p in (RUN/model/'jobs').glob('*/result.json')];assert len(a)==78
        for r in a:
            if r['ok']:assert sha(Path(r['image']).read_bytes())==r['output_sha256']
        results.append(dict(model=model,attempts=len(a),ok=sum(r['ok'] for r in a),failed=sum(not r['ok'] for r in a)))
    detailed=read(RUN/'reviews.json')['reviews']
    findings=dict(status='generation_and_detailed_assistant_review_completed',cases=20,outputs=156,
        detailed_rating_counts={'knowledge':sum(len(r['knowledge']) for r in detailed),
            'execution':sum(len(r['execution_items']) for r in detailed),'quality':sum(len(r['quality_items']) for r in detailed)},
        supplemental_instruction_audit=read(RUN/'supplemental_instruction_reviews.json'),
        generation=results,main_all20=main_groups,matched_image16=matched,preselected_diagnostic6=diagnostics,
        paired_all20=paired,gemini_cost_usd=read(RUN/'gemini_run_result.json')['reported_cost_usd'],
        background_restoration=restore,
        limits=['Single output per condition and assistant review, not human gold or formal test accuracy.',
            'This is source-selected material use, not automatic retrieval, and no model was finetuned.',
            'Only 11 of 29 primary domains; family-level train/test isolation remains required.',
            'Image-only contains original source pixels including any embedded source labels; it is not OCR-free.',
            'Reference images vary in completeness. Image-only need not support every criterion; per-image limitations are shown.',
            'Explicit visual requirements diagnose behavior, not a proven internal cause.',
            'Some frozen knowledge criteria include identity and condition binding; execution failures can affect these rates. Reasons must distinguish this from a demonstrated factual error.',
            'Frozen joint rates assess listed criteria, not guaranteed complete prompt compliance. Tide and horseshoe-crab instruction coverage gaps have separate post-hoc audits of all 12 corresponding outputs, without changing frozen scores.',
            'The lens magnification case has a recorded near-threshold output; strict frozen tolerance is retained.',
            'Six synthetic initial scenes used built-in imagegen, whose tool did not return billed cost. They are not factual evidence or scored outputs.'])
    publish(RUN/'findings.json',encoded(findings))
    render()
    print(json.dumps({k:findings[k] for k in ['status','outputs','detailed_rating_counts','main_all20','gemini_cost_usd']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
