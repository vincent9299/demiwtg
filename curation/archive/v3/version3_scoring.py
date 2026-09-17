"""Detailed source-bound output review packets and strictly validated scores."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from version3_20 import RUN
from bagel_runner import publish, encoded, sha
from pipeline import read

QUALITY={'clarity','artifacts','coherence'}
STATUS={'pass','conflict','unobservable'}


def prepare():
    cases={c['question_id']:c for c in read(RUN/'cases.json')['cases']}
    jobs=[json.loads(l) for l in (RUN/'jobs.jsonl').read_text().splitlines()]
    order=[(m,j) for m in ['bagel','gemini'] for j in jobs]
    random.Random(94137).shuffle(order)
    mapping=[];packets=[]
    for i,(model,job) in enumerate(order,1):
        rid=f'output_{i:03d}'
        mapping.append(dict(review_id=rid,model=model,job_id=job['job_id']))
        p=RUN/model/'jobs'/job['job_id']/'result.json'
        if not p.exists() and model=='gemini':
            matches=list(RUN.glob('gemini_shard_*/jobs/'+job['job_id']+'/result.json'))
            assert len(matches)<=1
            if matches:p=matches[0]
        if not p.exists():
            continue
        r=read(p);image_path=None
        if r['ok']:
            src=Path(r['image']);assert sha(src.read_bytes())==r['output_sha256']
            dst=RUN/'blind'/f'{rid}{src.suffix}';publish(dst,src.read_bytes());image_path=str(dst)
        c=cases[job['question_id']]
        packets.append(dict(review_id=rid,review_shard=(i-1)%3,image_path=image_path,output_sha256=r.get('output_sha256'),
            generation_error=r.get('error') if not r['ok'] else None,case=c,
            review_instruction='Inspect the actual output, source/reference images and frozen evidence. For EVERY knowledge criterion give pass/conflict/unobservable and a specific reason in Chinese. For EVERY E item give status and reason. execution_pass is true only if all E pass. Quality independently: clarity, artifacts, coherence scores1..5 + reasons; scientific/factual correctness is not aesthetic quality. Overall quality is nearest integer to mean (half up). If generation failed use all unobservable, execution_pass false, quality null and quality item scores null. Reviewer is assistant, never human gold. Condition/model are concealed, not guaranteed unguessable. Do not infer knowledge error merely from unobservability.'))
    publish(RUN/'blind_mapping.json',encoded(mapping))
    for i in range(3):
        p=RUN/'blind'/f'packets_{i}.json';p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_suffix('.tmp');tmp.write_bytes(encoded({'expected':len(mapping[i::3]),'ready':sum(x['review_shard']==i for x in packets),
            'packets':[x for x in packets if x['review_shard']==i]}));tmp.replace(p)
    print('Prepared',len(packets),'anonymous detailed reviews')


def summarize():
    cases={c['question_id']:c for c in read(RUN/'cases.json')['cases']}
    jobs={j['job_id']:j for j in [json.loads(l) for l in (RUN/'jobs.jsonl').read_text().splitlines()]}
    mapping={r['review_id']:r for r in read(RUN/'blind_mapping.json')}
    reviews=[];seen=set();groups=defaultdict(Counter);strata=defaultdict(Counter);per=[]
    for p in sorted((RUN/'blind').glob('reviews_*.json')):
        for raw in read(p)['reviews']:
            rid=raw['review_id'];assert rid in mapping and rid not in seen
            seen.add(rid);m=mapping[rid];j=jobs[m['job_id']];c=cases[j['question_id']]
            r=dict(raw,model=m['model'],job_id=m['job_id'])
            result=read(RUN/m['model']/'jobs'/m['job_id']/'result.json')
            valid=result['ok']
            if valid:
                assert r['output_sha256']==result['output_sha256']==sha(Path(result['image']).read_bytes())
            kids={k['id'] for k in c['knowledge_checks']}
            assert set(r['knowledge'])==kids==set(r['knowledge_reasons'])
            assert all(s in STATUS for s in r['knowledge'].values())
            assert all(r['knowledge_reasons'].values()) and r['reviewer'] and r['observations']
            eids={f'E{i}' for i in range(1,len(c['execution_checks'])+1)}
            assert set(r['execution_items'])==eids
            assert all(e['status'] in STATUS and e['reason'] for e in r['execution_items'].values())
            ep=all(e['status']=='pass' for e in r['execution_items'].values())
            assert r['execution_pass'] is ep
            assert set(r['quality_items'])==QUALITY
            for item in r['quality_items'].values():
                assert item['reason']
                assert (type(item['score']) is int and 1<=item['score']<=5) if valid else item['score'] is None
            if valid:
                expected_quality=int(sum(x['score'] for x in r['quality_items'].values())/3+0.5)
                assert r['quality']==expected_quality
            else:
                assert all(v=='unobservable' for v in r['knowledge'].values()) and not ep and r['quality'] is None
            kp=all(v=='pass' for v in r['knowledge'].values())
            counts=Counter(total=1,generation_failed=int(not valid),knowledge_pass=int(kp),execution_pass=int(ep),joint_pass=int(kp and ep),
                knowledge_items=len(kids),knowledge_items_pass=sum(v=='pass' for v in r['knowledge'].values()),
                knowledge_items_conflict=sum(v=='conflict' for v in r['knowledge'].values()),
                knowledge_items_unobservable=sum(v=='unobservable' for v in r['knowledge'].values()),
                execution_items=len(eids),execution_items_pass=sum(e['status']=='pass' for e in r['execution_items'].values()),
                all_knowledge_observable=int(all(v!='unobservable' for v in r['knowledge'].values())))
            label=m['model']+'/'+j['condition'];groups[label].update(counts)
            for dim in ['task','domain','application_level']:
                strata[label+'/'+dim+'/'+c[dim]].update(counts)
            if c['reference_images']:strata[label+'/reference_subset/with_image'].update(counts)
            per.append(dict(model=m['model'],job_id=m['job_id'],condition=j['condition'],question_id=c['question_id'],
                knowledge_pass=kp,execution_pass=ep,joint_pass=kp and ep,quality=r['quality'],knowledge=r['knowledge']))
            reviews.append(r)
    assert len(seen)==len(mapping),'Every output, including failures, needs a detailed review'
    publish(RUN/'reviews.json',encoded({'reviewer_type':'assistant_not_human_gold','reviews':reviews}))
    publish(RUN/'scores_summary.json',encoded(dict(expected=len(mapping),reviewed=len(seen),
        by_model_condition={k:dict(v) for k,v in groups.items()},by_stratum={k:dict(v) for k,v in strata.items()},per_output=per,
        caution='Single-sample development judgments. Conditions have different denominators; use reference_subset for four-condition comparisons. No aggregate quality/knowledge blend. No finetuning or automatic retrieval.')))
    print(json.dumps({k:dict(v) for k,v in groups.items()},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','summarize']);a=p.parse_args()
    {'prepare':prepare,'summarize':summarize}[a.action]()
