"""V1 Edit evaluation graph: jobs, judge binding, clamp rule and protocol checks."""
import json
from pathlib import Path

import pytest

from preparation.operaters import runfiles as storage
from preparation.operaters.runfiles import saved_stage, run_manifest
from demiflow.execution.artifacts import digest


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(storage, "code_version", lambda: {"fixture_code": 1})
    from evaluation.edit.v1.operaters import runfiles
    bench = tmp_path / "benchmark/edit/v1"
    monkeypatch.setattr(runfiles, "BENCH_DIR", bench)
    monkeypatch.setattr(runfiles, "ALLOWED_SOURCE_ROOT", (bench / "focus200").resolve())
    from evaluation.edit.v1.operaters import answers
    monkeypatch.setattr(answers, "resolve_source",
                        lambda q: runfiles.resolve_source(q))
    from PIL import Image
    source = bench / "focus200/images/00001_实体_v0.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (64, 48), (10, 90, 140)).save(source)
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps({
        "qid": "e001", "task": "edit", "status": "constructed", "edit_type": "replace",
        "suite": "basic", "edit_instruction": "把最左侧机器人换成另一型号",
        "_sample_image": "focus200/images/00001_实体_v0.png",
        "_file": "images/00001_实体_v0.png"}, ensure_ascii=False) + "\n", encoding="utf-8")
    after = tmp_path / "after.png"
    Image.new("RGB", (64, 48), (140, 90, 10)).save(after)
    responses = tmp_path / "responses.jsonl"
    responses.write_text(json.dumps({"qid": "e001", "ok": True, "image": str(after)}) + "\n",
                         encoding="utf-8")
    return tmp_path, questions, responses, source


def build(lake, responses=None):
    from evaluation.edit.v1.edit_v1_eval_pipeline import config, run_pipeline
    project = lake[0]
    run = project / "evaluation/edit/v1/datasets/offline"
    cfg = config("offline", "openrouter/google/gemini-3.1-flash-image", judge_model="qwen3.8-27b")
    return run, run_pipeline(run, lake[1], cfg, through="summary", responses=responses), cfg


def test_offline_graph_builds_jobs_and_pends_judge(lake):
    run, result, _ = build(lake, lake[2])
    assert result
    manifest = run_manifest(run)
    assert manifest["pipeline_module"] == "evaluation.edit.v1"
    assert manifest["protocol"]["judge"] == "edit-imgedit-three-perspective"
    answers = saved_stage(run, "answers")
    assert answers[0]["status"] == "generated"
    assert answers[0]["source_image"]["sha256"] == digest(lake[3].read_bytes())
    requests = saved_stage(run, "judge_requests")
    active = next(r for r in requests if r["status"] == "generated")
    assert len(active["prompt_images"]) == 2
    assert [r["role"] for r in active["image_roles"]] == ["edit_source_before", "edit_result_after"]
    assert "把最左侧机器人" in active["prompt_payload"]["判分指令全文"]
    assert saved_stage(run, "scores")[0]["status"] == "pending_judge"


def test_binding_judge_text_applies_default_and_clamp(lake):
    from demiflow.operator_llm.lance_journal import submit_response
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    run, _, cfg = build(lake, lake[2])
    active = next(r for r in saved_stage(run, "judge_requests") if r["status"] == "generated")
    native = read_record(active["judge_binding"]["request_ref"])["native_offline"]
    reply = ("Analysis: the replacement is faithful.\n"
             "Prompt Compliance: 4\nVisual Naturalness: 5\n"
             "Physical & Detail Integrity: the reflection is wrong")
    submit_response(resolve_root(), native["request_ref"], reply, model=cfg["judge_model"])
    from evaluation.edit.v1.edit_v1_eval_pipeline import run_pipeline
    rerun = lake[0] / "evaluation/edit/v1/datasets/offline"
    run_pipeline(rerun, lake[1], cfg, through="summary", responses=lake[2])
    scores = saved_stage(rerun, "scores")
    scored = next(r for r in scores if r["status"] == "scored")
    dims = scored["score"]["dims"]
    assert dims["Prompt Compliance"] == 4.0
    assert dims["Visual Naturalness"] == 4.0  # 5 被钳制到第一维
    assert dims["Physical & Detail Integrity"] == 1.0  # 缺项默认 1.0
    assert scored["score"]["total"] == 3.0
    summary = saved_stage(rerun, "summary")[0]
    assert summary["n"] == 1 and summary["by_type"]["replace"]["n"] == 1


def test_jobs_carry_historical_request_shape_without_calls(lake):
    """Request shape is verified in offline mode; no gateway or model is ever called."""
    from evaluation.edit.v1.edit_v1_eval_pipeline import config, run_pipeline
    cfg = config("offline", "openrouter/google/gemini-3.1-flash-image")
    run = lake[0] / "evaluation/edit/v1/datasets/jobs"
    run_pipeline(run, lake[1], cfg, through="answers")
    jobs = saved_stage(run, "answers")
    job = next(r for r in jobs if r["status"] == "pending_generation")
    assert "request" in job and job["request"]["payload"]  # offline: 请求已备好，未调端点
    payload = job["request"]["payload"]
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image_url" and content[1]["type"] == "text"
    assert content[1]["text"] == "把最左侧机器人换成另一型号"
    assert payload["modalities"] == ["image", "text"]
    assert payload["image_config"]["aspect_ratio"] in {"4:3", "3:2", "1:1"}
    # 构题内部判据不进入作答输入
    assert "reasoning" not in json.dumps(payload) and "evidence" not in json.dumps(payload)


def test_parse_scores_matches_legacy_semantics():
    from evaluation.edit.v1.operaters.judging import parse_scores
    from evaluation.edit.v1.tests.fixtures.eval_score import EDIT_DIMS
    raw = "Prompt Compliance:3.5\nVisual Naturalness: 5\n"
    dims = EDIT_DIMS["replace"]
    got = parse_scores(raw, "replace")
    assert got == [3.5, 3.5, 1.0]
    assert dict(zip(dims, got))["Visual Naturalness"] == 3.5
    assert parse_scores("Prompt Compliance：5\nVisual Naturalness:2\nPhysical & Detail Integrity:4",
                        "replace") == [5.0, 2.0, 4.0]


def test_score_table_append_overwrite_and_resume(lake):
    """离线评分结果也按配置写目标表，同一快照重放不重复追加。"""
    import lance
    from project import resolve_root
    from evaluation.edit.v1.edit_v1_eval_pipeline import run_pipeline
    base, _, cfg = build(lake, lake[2])
    target = resolve_root() / 'demiwtg/evaluation/edit/v1/datasets/shared_scores.lance'
    for name, mode, count in [('first', 'append', 1), ('next', 'append', 2), ('replace', 'overwrite', 1)]:
        run = base.with_name(name)
        options = dict(responses=lake[2], target_uri=str(target), write_mode=mode, through='scores')
        state = run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).count_rows() == count
        before = lance.dataset(str(target)).version
        run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).version == before
        assert state['stages']['scores']['dataset_ref']['lance_version'] == before
