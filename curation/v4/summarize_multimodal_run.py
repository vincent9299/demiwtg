"""Read-only stage accounting for a multimodal run; never invokes models."""
import argparse
import collections
import json
from pathlib import Path


def rows(path):
    if not path.exists():return []
    with path.open() as f:return [json.loads(line) for line in f if line.strip()]


def summarize(run):
    run=Path(run);tables=run/'datasets';cases={};summary={}
    for r in rows(tables/'knowledge_identity.jsonl'):
        name=r['concept_ref'];cases[r['case_id']]=name
        summary[name]={'identity_status':r.get('identity',{}).get('status'),'blocked':r.get('blocked'),
            'identity_unexamined':len(r.get('identity_unexamined',[])), 'case_ids':[r['case_id']]}
    for filename,field,label in [('documents_processed','clean_status','documents'),('images_processed','byte_status','images')]:
        count=collections.defaultdict(collections.Counter)
        for r in rows(tables/(filename+'.jsonl')):
            for name in r['concept_refs']:count[name][r[field]]+=1
        for name,c in count.items():summary.setdefault(name,{})[label]=dict(c)
    for filename,field,decision,label in [('text_relevance','block_decisions','decision','text_filter'),('image_relevance','image_decisions','decision','image_filter')]:
        count=collections.defaultdict(collections.Counter)
        for r in rows(tables/(filename+'.jsonl')):
            for d in r[field]:count[cases[r['case_id']]][d.get(decision,'missing')]+=1
        for name,c in count.items():summary.setdefault(name,{})[label]=dict(c)
    for r in rows(tables/'related_materials.jsonl'):
        summary[cases[r['case_id']]]['selected_text_chars']=sum(len(p['text']) for p in r['material_pack']['passages'])
    stage_checks={}
    for name in ['joint_extract','joint_verify']:
        counts=collections.defaultdict(collections.Counter);reasons=collections.defaultdict(collections.Counter)
        for r in rows(tables/(name+'.jsonl')):
            concept=cases[r['case_id']];counts[concept]['groups']+=1
            counts[concept]['retained_before_merge']+=len(r.get('joint_facts',[]))
            counts[concept]['deferred_before_merge']+=len(r.get('joint_deferred',[]))
            for item in r.get('joint_deferred',[]):
                reasons[concept].update(set(str(x) for x in item.get('reasons',[])))
        stage_checks[name]={concept:{**dict(count),'defer_reasons':dict(reasons[concept])} for concept,count in counts.items()}
    final=rows(run/'knowledge_base.jsonl')
    for r in final:
        k=r['knowledge'];entry=summary.setdefault(r['concept']['concept_ref'],{})
        entry['output']={'retained_candidates':len(k.get('facts',[])),'deferred':len(k.get('deferred_facts',[])),
            'conflicts':len(k.get('unresolved_conflicts',[])),'basis':dict(collections.Counter(f.get('basis') for f in k.get('facts',[]))),
            'joint_batch_scope':r['audit'].get('multimodal_review',{}).get('joint_batch_scope'),
            'positive_images':len(r.get('image_selection',{}).get('supporting_images',[]))}
    requests=list((run/'knowledge/calls').glob('*.request.json'));responses=list((run/'knowledge/calls').glob('*.response.json'))
    finishes=collections.Counter();usage=collections.Counter();elapsed=0.;stages=collections.Counter();parse_errors=[];structure_issues=[]
    for p in responses:
        r=json.loads(p.read_text());body=r.get('body',{});elapsed+=r.get('elapsed_s',0)
        for key in ['prompt_tokens','completion_tokens','total_tokens']:usage[key]+=body.get('usage',{}).get(key,0)
        choices=body.get('choices',[])
        if not choices:finishes['no_choices']+=1;continue
        finishes[choices[0].get('finish_reason','missing')]+=1
        try:d=json.loads(choices[0]['message']['content']).get('result',{})
        except (ValueError,KeyError,TypeError):parse_errors.append(p.name);continue
        stage='extract' if 'facts' in d else 'verify' if 'reviews' in d else 'merge' if 'duplicate_groups' in d else 'image_filter' if 'images' in d else 'text_filter' if 'decisions' in d else 'identity'
        stages[stage]+=1
        if stage=='verify':
            reviews=d.get('reviews');extra=set(d)-{'reviews'}
            if not isinstance(reviews,list) or any(not isinstance(x,dict) for x in reviews) or extra:
                structure_issues.append({'response':p.name,'issue':'Nonstandard review structure; inspect per-fact application',
                    'unexpected_keys':sorted(extra)})
    incomplete=[p.name for p in requests if not p.with_name(p.name.replace('.request.json','.response.json')).exists()]
    return {'run':str(run),'scope':'Directed engineering trial; machine candidates, not independent factual certification',
            'concepts':summary,'stage_checks':stage_checks,'final_file_complete':bool(final),'calls':{'requests':len(requests),'responses':len(responses),'incomplete_requests':incomplete,
            'complete_response_stages':dict(stages),'finish_reasons':dict(finishes),'usage':dict(usage),'sum_request_elapsed_s':round(elapsed,3),'parse_errors':parse_errors,'review_structure_issues':structure_issues}}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();text=json.dumps(summarize(a.run),ensure_ascii=False,indent=2)+'\n'
    if a.output:a.output.write_text(text)
    else:print(text)

if __name__=='__main__':main()
