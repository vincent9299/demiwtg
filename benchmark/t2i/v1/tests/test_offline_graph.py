"""V1 T2I authoring graph: offline requests, response binding and historical equivalence."""
import json
from pathlib import Path

import pytest
from project import historical_evidence, default_root, evidence_key

def evidence():
    return historical_evidence(default_root())

from preparation.operaters import runfiles as storage
from preparation.operaters.runfiles import saved_stage, run_manifest


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(storage, "code_version", lambda: {"fixture_code": 1})
    samples = tmp_path / "samples.jsonl"
    rows = [
        {"sample_id": "60001", "instance": "软银 NAO", "mount_paths": ["demiwtg / 人造物体"],
         "l1": "人造物体", "image": "samples/60001.png"},
        {"sample_id": "60002", "instance": "鳞毛蕨", "mount_paths": ["demiwtg / 植物"], "l1": "植物"},
        {"sample_id": "60003"},
    ]
    samples.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return tmp_path, samples


def build(lake):
    from benchmark.t2i.v1.t2i_v1_benchmark_pipeline import config, run_pipeline
    project = lake[0]
    run = project / "benchmark/t2i/v1/datasets/offline"
    config = config("offline", ["xiaoyao/gpt-5.6-sol"], author_model="gpt-5.6-sol", limit=3)
    return run, run_pipeline(run, lake[1], config), config


QUESTION = {"task": "t2i", "status": "constructed", "qid": "60001_gpt-5.6-sol",
            "gen_prompt": "画一台软银NAO机器人，桌面倒影中齿轮可见",
            "level": "L2", "level_reason": "前提8 结论6",
            "reasoning": "（前提）样本是机器人（知识·概念自身结构；域·人造物体）→[结论] R1 倒影同步（混淆源：镜面）→[不得画] N1 反转（混淆源：镜像）",
            "combo_type": "光学媒介", "scene_types": ["实体密度"], "hop_types": ["过程-因果", "物理规律"],
            "weak_points": ["镜面倒影", "精确计数"],
            "premise_types": ["定位前提", "改动规定前提", "保持范围前提", "环境场所前提"],
            "knowledge_domains": ["人造物体", "动物"], "notes": ""}


def test_offline_graph_prepares_requests_and_pends_without_responses(lake):
    run, result, _ = build(lake)
    assert result
    manifest = run_manifest(run)
    assert manifest["pipeline_module"] == "benchmark.t2i.v1"
    assert manifest["protocol"]["authoring"] == "v6.0-entry-items"
    samples = saved_stage(run, "samples")
    assert [r["status"] for r in samples] == ["sample_ready", "sample_ready", "invalid_sample"]
    jobs = saved_stage(run, "jobs")
    assert [r["qid"] for r in jobs if r["status"] == "ready_to_author"] == [
        "60001_gpt-5.6-sol", "60002_gpt-5.6-sol"]
    requests = saved_stage(run, "requests")
    active = next(r for r in requests if r["status"] == "ready_to_author")
    assert active["prompt_images"] == []  # v6.0 authors without images
    assert active["prompt_payload"]["概念名"] == "软银 NAO"
    from preparation.operaters.runfiles import read_record
    assert "prompt_instructions" not in active
    messages = read_record(active['synthesize_binding']['request_ref'])['messages']
    text = next(m['content'] for m in messages if m['role'] == 'user')
    assert text.lstrip().startswith('#') and '软银 NAO' in text
    assert saved_stage(run, "synthesize")[0]["status"] == "pending_synth"
    assert not saved_stage(run, "questions")
    export = saved_stage(run, "export")
    assert all(r["status"] == "pending_synth" for r in export if r["task_id"] == "60001_gpt-5.6-sol")


def test_binding_a_response_produces_an_audited_question(lake):
    from demiflow.operator_llm.lance_journal import submit_response
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    run, _, config = build(lake)
    active = next(r for r in saved_stage(run, "requests") if r["status"] == "ready_to_author")
    native = read_record(active["synthesize_binding"]["request_ref"])["native_offline"]
    submit_response(resolve_root(), native["request_ref"], {"result": QUESTION},
                    model=config["author"]["model"])
    from benchmark.t2i.v1.t2i_v1_benchmark_pipeline import run_pipeline
    rerun = lake[0] / "benchmark/t2i/v1/datasets/offline"
    run_pipeline(rerun, lake[1], config)
    exported = [r for r in saved_stage(rerun, "export") if r["status"] == "audited"]
    assert len(exported) == 1
    row = exported[0]
    assert row["question"]["gen_prompt"] == QUESTION["gen_prompt"]
    assert row["question"]["_generator_model"] == "xiaoyao/gpt-5.6-sol"
    assert row["question"]["_job_sample"] == "60001"
    assert row["question"]["task"] == "t2i"
    assert isinstance(row["audit_warnings"], list)


def test_reject_cannot_construct_and_empty_design_keep_explicit_statuses(lake):
    from benchmark.t2i.v1.operaters.prompting import apply_synth
    base = {"status": "ready_to_author", "task_id": "t", "qid": "t",
            "synthesize_binding": {"request_ref": {}}, "instance": "x",
            "sample_id": "s", "author_model": "m"}
    run = lake[0] / "benchmark/t2i/v1/datasets/apply"
    rejected = apply_synth({**base, "synthesize_result": {"status": "reject", "reject_code": "no_evidence"}}, run=run)
    assert rejected["status"] == "rejected" and rejected["reject_code"] == "no_evidence"
    cannot = apply_synth({**base, "synthesize_result": {"status": "cannot_construct", "notes": "证据不足"}}, run=run)
    assert cannot["status"] == "cannot_construct"
    empty = apply_synth({**base, "synthesize_result": {"status": "constructed"}}, run=run)
    assert empty["status"] == "empty_design"
    fenced = apply_synth({**base, "synthesize_result": '```json\n{"status": "constructed", "gen_prompt": "x",}',
                          }, run=run)
    assert fenced["status"] == "authored" and fenced["question"]["gen_prompt"] == "x"


def test_historical_raw_responses_parse_identically():
    """Replay historical r11 raw responses through the new parser and audit."""
    from benchmark.t2i.v1.tests.fixtures.eval_synthesize import audit_v60 as old_audit, extract_json_object as old_extract
    from benchmark.t2i.v1.operaters.prompting import extract_json_object as new_extract
    from benchmark.t2i.v1.operaters.audit import audit_v60 as new_audit
    raw_dir = storage.ROOT / "benchmark/t2i/v1/bench200/provenance/r11_synth/raw"
    raws = [name for name in evidence().paths(evidence_key(raw_dir)+"/") if name.endswith(".json")]
    if not raws:
        pytest.fail("retained r11 evidence missing")
    checked = 0
    for path in raws[:25]:
        raw = json.loads(evidence().read_text(path))
        content = raw["choices"][0]["message"]["content"]
        if not content or not content.strip():
            continue
        question = old_extract(content)
        assert new_extract(content) == question
        assert new_audit(question) == old_audit(question)
        checked += 1
    assert checked >= 5


def test_historical_questions_reaudit_without_new_warnings():
    from benchmark.t2i.v1.operaters.audit import audit_v60
    from benchmark.t2i.v1.tests.fixtures.eval_synthesize import audit_v60 as old_audit
    questions_path = storage.ROOT / "benchmark/t2i/v1/bench200/questions.jsonl"
    rows = [json.loads(line) for line in evidence().read_text(evidence_key(questions_path)).splitlines() if line.strip()]
    for question in rows[:50]:
        assert audit_v60(question) == old_audit(question)


def test_final_question_table_append_overwrite_and_resume(lake):
    """V1 新输出支持追加和覆盖；同一完成阶段续跑保持固定版本。"""
    import lance
    from demiflow.operator_llm.lance_journal import submit_response
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    from benchmark.t2i.v1.t2i_v1_benchmark_pipeline import run_pipeline
    base, _, cfg = build(lake)
    target = resolve_root() / 'demiwtg/benchmark/t2i/v1/datasets/shared_questions.lance'
    for name, mode, count in [('first', 'append', 1), ('next', 'append', 2), ('replace', 'overwrite', 1)]:
        run = base.with_name(name)
        options = dict(target_uri=str(target), write_mode=mode)
        run_pipeline(run, lake[1], cfg, through='requests', **options)
        active = next(r for r in saved_stage(run, 'requests') if r['status'] == 'ready_to_author')
        native = read_record(active['synthesize_binding']['request_ref'])['native_offline']
        submit_response(resolve_root(), native['request_ref'], {'result': QUESTION}, model=cfg['author']['model'])
        state = run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).count_rows() == count
        before = lance.dataset(str(target)).version
        run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).version == before
        assert state['stages']['questions']['dataset_ref']['lance_version'] == before
