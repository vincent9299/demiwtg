"""Frozen small knowledge-dependence probes and assistant review consumer.

Data lives in state/curation/knowledge_probe_v1. No canonical datasets are written.
"""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import argparse
import base64
import hashlib
import html
import io
import json
import random
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw
from curation.rag_diagnostic import REPO, sha, write_json, read_rows

DEFAULT = REPO / 'state/curation/knowledge_probe_v1'
ARMS = ['baseline', 'knowledge', 'irrelevant', 'explicit']


def prepare(run=DEFAULT):
    run = Path(run)
    if (run/'plan.json').exists():
        raise ValueError('Frozen plan exists; revise in a new run')
    spec = json.loads((run/'cases.json').read_text())
    arms = spec.get('arms', ARMS)
    frozen = {str(run/'cases.json'): sha(run/'cases.json')}
    jobs = []
    all_rows = []
    for arm in arms:
        directory = run/'conditions'/arm
        directory.mkdir(parents=True, exist_ok=True)
        rows = []
        for c in spec['cases']:
            if arm not in c.get('active_conditions', arms):
                continue
            for repeat in range(1, spec['repeats'] + 1):
                prompt = c['prompt']
                overrides = c.get('condition_prompts', {})
                if arm in overrides:
                    prompt = overrides[arm]
                elif arm in ['knowledge', 'irrelevant', 'wrong']:
                    prompt = 'Reference information for understanding the task; do not render this text: ' + c[arm] + '\n\nTask: ' + prompt
                elif arm == 'explicit':
                    prompt += '\n' + c['explicit']
                seed_key = c.get('seed_family', c['id']) + '_r' + str(repeat)
                qid = c['id'] + ('_'+arm if spec.get('combined') else '') + '_r' + str(repeat)
                rows.append(dict(qid=qid,task='t2i',gen_prompt=prompt,parent_id=c['id'],condition=arm,repeat=repeat,_seed_key=seed_key))
                jobs.append(dict(qid=qid,parent_id=c['id'],condition=arm,repeat=repeat,
                                 output_group='combined' if spec.get('combined') else arm,
                                 seed=42+int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:4],'big'),
                                 prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()))
        qpath = directory/'questions.jsonl'
        qpath.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        frozen[str(qpath)] = sha(qpath)
        all_rows += rows
    if spec.get('combined'):
        directory=run/'conditions/combined';directory.mkdir(exist_ok=True)
        random.Random(91371).shuffle(all_rows)
        qpath=directory/'questions.jsonl';qpath.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in all_rows))
        frozen[str(qpath)]=sha(qpath)
    for name in ['scripts/run_wkbench.py','inferencer.py','data/transforms.py']:
        p = REPO/'bagel/Bagel'/name
        frozen[str(p)] = sha(p)
    runner=Path(spec.get('runner', str(REPO/'bagel/Bagel/scripts/run_wkbench.py')))
    frozen[str(runner)]=sha(runner)
    plan = dict(schema='knowledge-probe-v1',role='development_only',conditions=['combined'] if spec.get('combined') else arms,
                cases=[c['id'] for c in spec['cases']],jobs=jobs,n_images=len(jobs),seed_base=42,
                runner=str(runner),
                python=str(REPO.parent/'env-bagel/bin/python'),model_path=str(REPO/'bagel/models/BAGEL-7B-MoT'),
                image_size=spec.get('image_size',512),num_timesteps=50,frozen_files=frozen,inference_settings=spec.get('inference_settings',{}),
                human_review='assistant_delegated_not_human_gold',protocol=spec['protocol'])
    write_json(run/'plan.json',plan)
    # Independent closed-model baseline; repeat ids are bookkeeping, not paired model noise.
    gemini = run/'gemini_questions.jsonl'
    rows = [dict(qid=c['id']+'_g1',task='t2i',gen_prompt=c['prompt'],parent_id=c['id'],condition='baseline') for c in spec['cases']]
    gemini.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    print(f'Frozen {len(jobs)} local images; {len(rows)} Gemini baseline requests prepared')


def collect(run, allow_partial=False):
    run=Path(run).resolve(); plan=json.loads((run/'plan.json').read_text()); records=[]
    for p,h in plan['frozen_files'].items():
        if sha(p)!=h: raise ValueError(f'Frozen input changed: {p}')
    for arm in plan['conditions']:
        d=run/'conditions'/arm/'outputs'
        rows={k:v for p in d.glob('responses*.jsonl') for k,v in read_rows(p).items()}
        expected={j['qid'] for j in plan['jobs'] if j.get('output_group',j['condition'])==arm}
        if not set(rows)<=expected: raise ValueError('Unexpected response outside frozen bank')
        if set(rows)!=expected and not allow_partial:
            raise ValueError(f'Incomplete {arm}')
        if sha(d/'questions.jsonl')!=sha(d.parent/'questions.jsonl'): raise ValueError('Snapshot mismatch')
        for j in [j for j in plan['jobs'] if j.get('output_group',j['condition'])==arm]:
            if j['qid'] not in rows and allow_partial: continue
            r=rows[j['qid']]
            if not r.get('ok') or r['seed']!=j['seed']: raise ValueError(f'Bad generation: {arm}/{j["qid"]}')
            p=d/r['image']
            with Image.open(p) as im: im.verify()
            records.append(dict(**j,model='BAGEL',path=str(p),sha256=sha(p)))
    for gem in sorted(p for p in run.glob('gemini*') if p.is_dir()):
        questions=read_rows(run/(gem.name+'_questions.jsonl'))
        for rp in sorted(gem.glob('*/result.json')):
            r=json.loads(rp.read_text())
            if not r.get('ok'): continue
            q=questions[r['qid']]
            p=Path(r.get('output_path') or r.get('image',''))
            if not p.is_absolute(): p=gem/p
            records.append(dict(qid=r['qid'],parent_id=q['parent_id'],condition=q['condition'],repeat=q.get('repeat',1),
                                model='Gemini',path=str(p),sha256=sha(p),
                                question_bank=str(run/(gem.name+'_questions.jsonl')),
                                question_bank_sha256=sha(run/(gem.name+'_questions.jsonl')),
                                prompt_sha256=hashlib.sha256(q['gen_prompt'].encode()).hexdigest()))
    spec=json.loads((run/'cases.json').read_text())
    if spec.get('closed_reference_run'):
        reference=Path(spec['closed_reference_run'])
        old={c['id']:c for c in json.loads((reference/'cases.json').read_text())['cases']}
        current={c['id']:c for c in spec['cases']}
        for r in json.loads((reference/'image_index.json').read_text()):
            if r['model']!='Gemini' or r['parent_id'] not in current: continue
            assert old[r['parent_id']]['prompt']==current[r['parent_id']]['prompt'], 'Closed baseline prompt differs'
            assert sha(r['path'])==r['sha256'], 'Reused closed image changed'
            records.append({**{k:v for k,v in r.items() if k!='blind_id'}, 'reused_from':str(reference)})
    return records


def panels(run=DEFAULT, allow_partial=False):
    run=Path(run).resolve(); records=collect(run, allow_partial=allow_partial)
    index_path=run/'image_index.json'
    shuffled=json.loads(index_path.read_text()) if index_path.exists() else []
    previous={r['path']:r for r in shuffled}
    for r in records:
        if r['path'] in previous and r['sha256']!=previous[r['path']]['sha256']:
            raise ValueError('Previously masked image changed')
    added=[r for r in records if r['path'] not in previous]
    random.Random(61973).shuffle(added)
    for r in added:
        r['blind_id']=f'V{len(shuffled)+1:03d}'
        shuffled.append(r)
    directory=run/'blind_panels'; directory.mkdir(exist_ok=True)
    full=run/'blind_images'; full.mkdir(exist_ok=True)
    for r in shuffled:
        target=full/(r['blind_id']+'.png')
        if not target.exists():
            with Image.open(r['path']) as im: im.convert('RGB').save(target)
    # Reviewers receive panels and rubric only; mapping retained for later analysis.
    write_json(run/'image_index.json',shuffled)
    for case in json.loads((run/'cases.json').read_text())['cases']:
        subset=[r for r in shuffled if r['parent_id']==case['id']]
        canvas=Image.new('RGB',(1600,((len(subset)+3)//4)*425),'white'); draw=ImageDraw.Draw(canvas)
        for i,r in enumerate(subset):
            with Image.open(r['path']) as im: thumb=ImageOps.contain(im.convert('RGB'),(390,390))
            x=(i%4)*400;y=(i//4)*425
            canvas.paste(thumb,(x+(400-thumb.width)//2,y+30));draw.text((x+8,y+8),r['blind_id'],fill='black')
        canvas.save(directory/(case['id']+'.jpg'),quality=96)
    clarifications=json.loads((run/'rubric_clarifications.json').read_text()) if (run/'rubric_clarifications.json').exists() else {}
    write_json(run/'blind_rubric.json',dict(cases=[{k:c[k] for k in ['id','concept','prompt','criteria','exceptions','sources']} for c in json.loads((run/'cases.json').read_text())['cases']], clarifications=clarifications,
        instructions='Review only anonymous images and criteria. Record conform/conflict/unobservable separately for each criterion; identity/composition separately. Do not open image_index or condition outputs before submission. Model/style may be inferable; this is masked assistant review, not independent human gold.'))
    print(directory)


def summary(run=DEFAULT):
    run=Path(run); index={r['blind_id']:r for r in json.loads((run/'image_index.json').read_text())}
    criteria={c['id']:set(c['criteria']) for c in json.loads((run/'cases.json').read_text())['cases']}
    reviews=[]
    for p in (run/'reviews').glob('*.json'):
        reviews+=json.loads(p.read_text())['records']
    if len({r['blind_id'] for r in reviews})!=len(reviews): raise ValueError('Duplicate reviews')
    out=[]
    for r in reviews:
        src=index[r['blind_id']]
        if set(r['checks'])!=criteria[src['parent_id']] or not set(r['checks'].values()) <= {'conform','conflict','unobservable'}:
            raise ValueError(f'Incomplete or invalid rubric: {r["blind_id"]}')
        if sha(src['path'])!=src['sha256']: raise ValueError('Reviewed image changed')
        out.append(dict(**src,review=r,all_knowledge_conform=all(v=='conform' for v in r['checks'].values())))
    return out


def status(run=DEFAULT):
    run=Path(run)
    groups=json.loads((run/'plan.json').read_text())['conditions']
    counts={a:sum(len(read_rows(p)) for p in (run/'conditions'/a/'outputs').glob('responses*.jsonl')) for a in groups}
    gem=[json.loads(p.read_text()) for p in run.glob('gemini*/*/result.json')]
    result=dict(local_responses=counts,gemini_results=len(gem),gemini_success=sum(r['ok'] for r in gem),
                reported_cost_usd=sum(r.get('usage',{}).get('cost',0) or 0 for r in gem))
    if (run/'session.json').exists(): result['session']=json.loads((run/'session.json').read_text())
    print(json.dumps(result,ensure_ascii=False,indent=2))


def gemini_conditions(records, arms):
    """Use actual closed-model arms in stable order, retaining the old empty view."""
    present={r['condition'] for r in records if r['model']=='Gemini'}
    preferred=list(dict.fromkeys(['baseline']+list(arms)))
    return [a for a in preferred if a in present]+sorted(present-set(preferred)) or ['baseline']


def gemini_prompt(run, record):
    """Find the exact closed-model bank entry, never substitute the base case prompt."""
    if record.get('question_bank'):
        paths=[Path(record['question_bank'])]
    else:
        # Legacy indices lack bank provenance; include the original run for reused images.
        source=Path(record.get('reused_from',run))
        paths=sorted(source.glob('gemini*questions.jsonl'))
    matches=[]
    for path in paths:
        if not path.exists():
            continue
        if record.get('question_bank_sha256') and sha(path)!=record['question_bank_sha256']:
            raise ValueError('Closed-model question bank changed')
        q=read_rows(path).get(record['qid'])
        if q and q.get('parent_id')==record['parent_id'] and q.get('condition')==record['condition']:
            prompt=q['gen_prompt']
            if record.get('prompt_sha256') and hashlib.sha256(prompt.encode()).hexdigest()!=record['prompt_sha256']:
                raise ValueError('Closed-model prompt changed')
            matches.append(prompt)
    unique=set(matches)
    return next(iter(unique)) if len(unique)==1 else None


def metrics(run=DEFAULT):
    run=Path(run);records=summary(run);result={'by_case':{},'paired_conditions':{}}
    spec=json.loads((run/'cases.json').read_text())
    arms=spec.get('arms', ARMS)
    closed_arms=gemini_conditions(records,arms)
    for c in spec['cases']:
        result['by_case'][c['id']]={}
        for model,arm in [('BAGEL',a) for a in arms]+[('Gemini',a) for a in closed_arms]:
            group=[r for r in records if r['parent_id']==c['id'] and r['model']==model and r['condition']==arm]
            if not group: continue
            result['by_case'][c['id']][model+'/'+arm]=dict(n=len(group),
                knowledge_conform=sum(r['all_knowledge_conform'] for r in group),
                any_unobservable=sum('unobservable' in r['review']['checks'].values() for r in group),
                joint_task_conform=sum(r['all_knowledge_conform'] and r['review'].get('identity_composition')=='conform' for r in group),
                execution_assessed=sum(r['review'].get('identity_composition') in {'conform','conflict','unobservable'} for r in group),
                unique_images=len({r['sha256'] for r in group}))
    families={}
    for c in spec['cases']:
        if c.get('seed_family'): families.setdefault(c['seed_family'],[]).append(c['id'])
    for family,cases in families.items():
        if len(cases)!=2: continue
        result['paired_conditions'][family]={}
        for arm in arms:
            pairs=[]
            for repeat in range(1,spec['repeats']+1):
                rs=[r for r in records if r['model']=='BAGEL' and r['condition']==arm and r['parent_id'] in cases and r['repeat']==repeat]
                if len(rs)!=2: continue
                assert rs[0]['seed']==rs[1]['seed'], 'Condition pair seeds differ'
                pairs.append(all(r['all_knowledge_conform'] for r in rs))
            result['paired_conditions'][family][arm]=dict(n_pairs=len(pairs),both_conform=sum(pairs))
    return result


def render(run=DEFAULT):
    run=Path(run); spec=json.loads((run/'cases.json').read_text()); esc=lambda s:html.escape(str(s))
    arms=spec.get('arms', ARMS)
    arm_labels=spec['protocol'].get('arm_labels', {})
    records=summary(run) if (run/'reviews').exists() and (run/'image_index.json').exists() else []
    closed_arms=gemini_conditions(records,arms)
    chunks=['<h2>知识型出题方法：小批开发验证</h2><p>来源核验与匿名助手复核；非人工金标准，非独立正式测试集。正确材料为核验后的 oracle 文字，尚非实际检索或微调。</p>',
            '<pre style="white-space:pre-wrap">'+esc(json.dumps(spec['protocol'],ensure_ascii=False,indent=2))+'</pre>']
    if (run/'findings.json').exists():
        chunks.append('<h3>实验结论</h3><pre style="white-space:pre-wrap">'+esc(json.dumps(json.loads((run/'findings.json').read_text()),ensure_ascii=False,indent=2))+'</pre>')
    if (run/'rubric_clarifications.json').exists():
        chunks.append('<h3>审图前来源复核与释义</h3><pre style="white-space:pre-wrap">'+esc((run/'rubric_clarifications.json').read_text())+'</pre>')
    if (run/'source_evidence.json').exists():
        for evidence in json.loads((run/'source_evidence.json').read_text())['records']:
            p=Path(evidence['path'])
            if sha(p)!=evidence['sha256']: raise ValueError('Source snapshot changed')
            chunks.append('<p>来源快照：<a href="'+esc(str(p))+'">'+esc(evidence['url'])+'</a> — '+esc(evidence['excerpt'])+'</p>')
    if records:
        pairs=metrics(run)['paired_conditions']
        if pairs: chunks.append('<h3>同种子条件对：两题同时符合</h3><pre>'+esc(json.dumps(pairs,ensure_ascii=False,indent=2))+'</pre>')
        chunks.append('<h3>全部知识判据符合（非总质量分）</h3><table><tr><th>案例</th>'+''.join('<th>'+esc(arm_labels.get(a,a))+'</th>' for a in arms)+ ''.join('<th>Gemini '+esc(arm_labels.get(a,a))+'</th>' for a in closed_arms)+'</tr>')
        for c in spec['cases']:
            chunks.append('<tr><td>'+esc(c['concept'])+'</td>')
            for arm,model in [(a,'BAGEL') for a in arms]+[(a,'Gemini') for a in closed_arms]:
                group=[r for r in records if r['parent_id']==c['id'] and r['condition']==arm and r['model']==model]
                cell=str(sum(r['all_knowledge_conform'] for r in group))+'/'+str(len(group))
                if group and all(r['review'].get('identity_composition') in {'conform','conflict','unobservable'} for r in group):
                    joint=sum(r['all_knowledge_conform'] and r['review']['identity_composition']=='conform' for r in group)
                    cell+='<br><small>知识＋构图 '+str(joint)+'/'+str(len(group))+'</small>'
                chunks.append('<td>'+cell+'</td>')
            chunks.append('</tr>')
        chunks.append('</table><p>无法观察不会计作知识符合；逐项冲突/不可观察见下面完整记录。小批重复不构成稳定总体性能估计；跨模型随机噪声不配对。</p>')
    for c in spec['cases']:
        chunks.extend(['<hr><h3>'+esc(c['id']+' · '+c['concept'])+'</h3>', '<p><b>实际原题：</b>'+esc(c['prompt'])+'</p>'])
        for key in ['chain']+[a for a in arms if a!='baseline']+['criteria','exceptions','image_support']:
            chunks.append('<p><b>'+esc(arm_labels.get(key,key))+'</b> '+esc(json.dumps(c.get(key),ensure_ascii=False))+'</p>')
        for s in c['sources']:
            chunks.append('<p><a href="'+esc(s['url'])+'">'+esc(s['title'])+'</a>：'+esc(s['support'])+'<br>短摘：'+esc(s.get('quote',''))+'</p>')
        for arm,model in [(a,'BAGEL') for a in arms]+[(a,'Gemini') for a in closed_arms]:
            subset=[r for r in records if r['parent_id']==c['id'] and r['condition']==arm and r['model']==model]
            chunks.append('<h4>'+esc(model+' / '+arm_labels.get(arm,arm))+'</h4>')
            if model=='BAGEL':
                plan=json.loads((run/'plan.json').read_text())
                jobs=[j for j in plan['jobs'] if j['parent_id']==c['id'] and j['condition']==arm]
                if jobs:
                    j=jobs[0];bank=read_rows(run/'conditions'/j.get('output_group',arm)/'questions.jsonl')
                    chunks.append('<details><summary>实际生成输入（冻结文件）</summary><pre style="white-space:pre-wrap">'+esc(bank[j['qid']]['gen_prompt'])+'</pre></details>')
            elif subset:
                prompts={gemini_prompt(run,r) for r in subset}
                if None in prompts:
                    chunks.append('<p>部分 Gemini 实际题面无法唯一追溯；未以原题替代。</p>')
                for prompt in sorted(p for p in prompts if p is not None):
                    chunks.append('<details><summary>Gemini 实际生成输入（对应题库）</summary><pre style="white-space:pre-wrap">'+esc(prompt)+'</pre></details>')
            chunks.append('<div style="display:flex;gap:12px;flex-wrap:wrap">')
            for r in sorted(subset,key=lambda r:r['repeat']):
                with Image.open(r['path']) as im: thumb=ImageOps.contain(im.convert('RGB'),(650,650))
                b=io.BytesIO();thumb.save(b,format='JPEG',quality=91)
                chunks.append('<figure style="margin:0;max-width:380px"><a href="'+esc(r['path'])+'"><img style="width:100%" src="data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()+'"></a><figcaption>'+esc(json.dumps(r['review'],ensure_ascii=False))+'</figcaption></figure>')
            chunks.append('</div>')
    return ''.join(chunks)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','panels','summary','status','metrics']);parser.add_argument('--run',type=Path,default=DEFAULT);args=parser.parse_args()
    result=globals()[args.command](args.run)
    if args.command in ('summary','metrics'): print(json.dumps(result,ensure_ascii=False,indent=2))
