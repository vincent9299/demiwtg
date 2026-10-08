"""改题重答采用原正式题作基线；仅答题不产生判官调用。"""
import json
from pathlib import Path
import lance
import pytest
from demiflow import data
from demiflow.image_generation import ImageGenerator
from project import resolve_root
from evaluation.t2i.v2.t2i_v2_eval_pipeline import (
    DATASETS, config, prepare_reanswer_questions, run_paired_arm)
from evaluation.t2i.v2.tests.test_pipeline import picture
from evaluation.t2i.v2.tests.test_paired import question


def store(name, rows):
    uri = str(resolve_root() / (name + '.lance'))
    data.from_items(rows).write_lance(uri)
    return {'uri': uri, 'version': lance.dataset(uri).version}


def reviewed(concept, instruction, status='ready'):
    return {**question(), 'concept': concept, 'task_id': concept+'_new',
        'instruction': instruction, 'source_task_id': concept+'_prior',
        'question_revision': concept+'_revision', 'requirements_json': '[]', 'review_status': status}


def test_source_compares_original_and_latest_not_local_review_flag():
    baseline = store('baseline', [
        {'concept':'changed', 'task_id':'original', 'instruction':'old'},
        {'concept':'same', 'task_id':'same', 'instruction':'unchanged'},
        {'concept':'held', 'task_id':'held', 'instruction':'old'},
    ])
    cohort = store('cohort', [{'concept':c,'selection_rank':i} for i,c in enumerate(['changed','same','held'],1)])
    old = store('old', [reviewed('changed','old'), reviewed('same','unchanged'), reviewed('held','old')])
    latest = store('latest', [reviewed('changed','new'), reviewed('held','old','hold')])
    settings = {'review_sources':[old,latest], 'cohort_source':cohort, 'expected_ready':2,'expected_changed':1}
    target = str(resolve_root()/DATASETS/'changed.lance')
    result = prepare_reanswer_questions(resolve_root()/DATASETS/'prepare',baseline,settings,target_uri=target)
    assert result['counts'] == {'ready':2,'changed':1,'unchanged':1}
    rows=data.read_lance(**result['target']).take(3)
    assert len(rows)==1 and rows[0]['concept']=='changed'
    assert rows[0]['instruction']=='new' and rows[0]['baseline_instruction']=='old'
    assert rows[0]['original_task_id']=='original' and rows[0]['requires_new_answer']
    assert prepare_reanswer_questions(resolve_root()/DATASETS/'prepare',baseline,settings,target_uri=target)==result


def test_answer_only_records_request_without_judge(monkeypatch):
    requested=[]
    def generate(self,request,key):
        requested.append(request['body']['prompt'])
        return picture()
    monkeypatch.setattr(ImageGenerator,'remote',generate)
    src=store('questions',[question()])
    cfg=config(answer_models=[{'backend':'modelhub','model':'fixture'}], parallel_answers=True,
        arm_concurrency=1,max_questions=1)
    manifest={'config':cfg,'source':src,'expected_questions':1}
    run=resolve_root()/DATASETS/'only_answers'
    result=run_paired_arm(run,manifest,0)
    assert result['complete'] and set(result['outputs'])=={'answers'}
    row=data.read_lance(**result['outputs']['answers']).take(1)[0]
    assert row['status']=='generated' and row['instruction']==question()['instruction']
    assert json.loads(row['answer_call_json'])['request_ref']
    assert not list((resolve_root()/DATASETS).glob('scores_*'))
    assert not list((resolve_root()/DATASETS).glob('calls_a__*'))
    again=run_paired_arm(run,manifest,0)
    assert again['complete'] and requested==[question()['instruction']]


def test_answer_only_cannot_enable_stream_judging():
    with pytest.raises(ValueError, match='excludes'):
        config(answer_models=[{'backend':'modelhub','model':'fixture'}],
            judge_model={'model':'fixture'},parallel_answers=True,stream_judging=True)


def test_parallel_answer_delivery_and_readonly_view(monkeypatch):
    from types import SimpleNamespace
    import evaluation.t2i.v2.t2i_v2_eval_pipeline as pipeline
    from evaluation.t2i.v2.operators.run_tables import RunTables
    from evaluation.t2i.v2.operators.case_viewer import build_case_browser
    root=resolve_root(); run=root/DATASETS/'parallel_answers'
    monkeypatch.setattr(ImageGenerator,'remote',lambda *args: picture())
    class Process:
        def __init__(self, command, **kwargs): self.index=int(command[-1])
        def wait(self):
            manifest=RunTables(root,str(DATASETS/'records__parallel_answers.lance')).load_manifest()
            result=run_paired_arm(run,manifest,self.index)
            return 0 if result['complete'] else 1
    monkeypatch.setattr(pipeline,'subprocess',SimpleNamespace(Popen=Process,
        DEVNULL=pipeline.subprocess.DEVNULL,STDOUT=pipeline.subprocess.STDOUT))
    cfg=config(answer_models=[{'backend':'modelhub','model':m} for m in ['one','two']],
        parallel_answers=True,arm_concurrency=2,max_questions=1)
    result=pipeline.run_pipeline(run,store('questions',[question()]),cfg)
    assert result['complete'] and result['counts']=={'generated':2,'total':2}
    rows=data.read_lance(**result['target']).take(3)
    assert {r['answer_model'] for r in rows}=={'one','two'}
    document,meta=build_case_browser(root/'demiwtg',run.name)
    assert meta['expected']==2 and meta['judge']=='本轮仅作答'
    assert all(arm['generation']=={'generated':1} for arm in meta['arms'])
    assert 'fixture' not in document
