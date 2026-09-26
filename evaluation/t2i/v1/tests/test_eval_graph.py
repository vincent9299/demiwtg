"""V1 T2I evaluation graph: answer jobs, judge binding and historical equivalence."""
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
    from PIL import Image
    import io
    questions = tmp_path / "questions.jsonl"
    question = {"qid": "60001", "task": "t2i", "status": "constructed", "level": "L2",
                "gen_prompt": "画一台软银NAO机器人站在桌旁，桌面倒影映出关节结构"}
    questions.write_text(json.dumps(question, ensure_ascii=False) + "\n", encoding="utf-8")
    image = tmp_path / "answer.png"
    Image.new("RGB", (64, 48), (200, 30, 30)).save(image)
    responses = tmp_path / "responses_gemini.jsonl"
    responses.write_text(json.dumps({"qid": "60001", "task": "t2i", "image": str(image),
                                     "ok": True, "seconds": 1.2, "mode": "chat"}) + "\n", encoding="utf-8")
    return tmp_path, questions, responses, question, image


def build(lake, responses=None):
    from evaluation.t2i.v1.t2i_v1_eval_pipeline import config, run_pipeline
    project = lake[0]
    run = project / "evaluation/t2i/v1/datasets/offline"
    cfg = config("offline", [], judge_model="gpt-5.6-sol")
    return run, run_pipeline(run, lake[1], cfg, through="summary", responses=responses), cfg


def test_offline_graph_imports_answers_and_pends_judge(lake):
    run, result, _ = build(lake, lake[2])
    assert result
    manifest = run_manifest(run)
    assert manifest["pipeline_module"] == "evaluation.t2i.v1"
    assert manifest["protocol"]["judge"] == "v6.0-V2"
    questions = saved_stage(run, "questions")
    assert questions[0]["status"] == "question_ready"
    answers = saved_stage(run, "answers")
    assert answers[0]["status"] == "generated"
    assert answers[0]["image"]["sha256"]
    requests = saved_stage(run, "judge_requests")
    active = next(r for r in requests if r["status"] == "generated")
    assert active["prompt_images"] and active["prompt_images"][0].startswith("data:image/jpeg;base64,")
    from preparation.operaters.runfiles import read_record
    assert "prompt_instructions" not in active
    messages = read_record(active['judge_binding']['request_ref'])['messages']
    text = '\n'.join(p.get('text', '') for m in messages if m['role'] == 'user' for p in m['content'])
    assert text.startswith('你是') and active['gen_prompt'] in text
    assert "软银NAO" in active["prompt_gen_prompt"]
    assert active["image_roles"] == [{"role": "generated_image", "sha256": answers[0]["image"]["sha256"]}]
    assert saved_stage(run, "scores")[0]["status"] == "pending_judge"


def test_binding_judge_response_scores_three_lines(lake):
    from demiflow.operator_llm.lance_journal import submit_response
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    from evaluation.t2i.v1.operaters.judging import V60_DIMS
    run, _, cfg = build(lake, lake[2])
    active = next(r for r in saved_stage(run, "judge_requests") if r["status"] == "generated")
    native = read_record(active["judge_binding"]["request_ref"])["native_offline"]
    parsed = {"alignment": {k: 2 for k in V60_DIMS["alignment"]},
              "quality": {k: 1 for k in V60_DIMS["quality"]},
              "aesthetics": {k: 0 for k in V60_DIMS["aesthetics"]}}
    for dim, keys in V60_DIMS.items():
        parsed[dim + "_reasons"] = {k: "r" for k in keys}
    parsed["alignment"]["subject_presence"] = "N/A"
    submit_response(resolve_root(), native["request_ref"], {"result": parsed}, model=cfg["judge_model"])
    from evaluation.t2i.v1.t2i_v1_eval_pipeline import run_pipeline
    rerun = lake[0] / "evaluation/t2i/v1/datasets/offline"
    run_pipeline(rerun, lake[1], cfg, through="summary", responses=lake[2])
    scores = saved_stage(rerun, "scores")
    scored = next(r for r in scores if r["status"] == "scored")
    assert scored["score"]["alignment_score"] == 100.0  # 9×100/9，N/A 剔除
    assert scored["score"]["quality_score"] == 60.0
    assert scored["score"]["aesthetic_score"] == 0.0
    assert scored["score"]["na_keys"]["alignment"] == ["subject_presence"]
    summary = saved_stage(rerun, "summary")[0]
    assert summary["n"] == 1 and summary["alignment"] == 100.0


def test_online_mode_required_for_gateway_and_jobs_pending_offline(lake):
    from evaluation.t2i.v1.t2i_v1_eval_pipeline import config, run_pipeline
    from evaluation.t2i.v1.operaters.answers import BuildAnswerJobs
    cfg = config("offline", ["openrouter/google/gemini-3.1-flash-image"])
    run = lake[0] / "evaluation/t2i/v1/datasets/jobs"
    run_pipeline(run, lake[1], cfg, through="answers")
    jobs = saved_stage(run, "answers")
    assert jobs[0]["status"] == "pending_generation"
    assert jobs[0]["request"]["mode"] == "auto"
    assert jobs[0]["model"] == "openrouter/google/gemini-3.1-flash-image"
    assert "pending_generation" in jobs[0].get("reason", "") or jobs[0]["status"] == "pending_generation"


def test_finalize_matches_historical_scores():
    """Re-finalize historical judge responses with the port and compare line scores."""
    from evaluation.t2i.v1.operaters.judging import finalize_v60, validate_v60
    from evaluation.t2i.v1.tests.fixtures.eval_score import finalize_v60 as old_finalize
    scores_dir = storage.ROOT / "benchmark/t2i/v1/bench200/scores"
    files = [name for name in evidence().paths(evidence_key(scores_dir)+"/scores_v60_V2_") if name.endswith(".jsonl")]
    if not files:
        pytest.fail("retained benchmark evidence missing")
    checked = 0
    for path in files:
        rows = [json.loads(line) for line in evidence().read_text(path).splitlines() if line.strip()]
        for row in rows[:20]:
            parsed = json.loads(row["raw"])
            assert not validate_v60(parsed)
            fresh = finalize_v60({"qid": row["qid"], "level": row.get("level")}, parsed, row["raw"],
                                 "V2", row["judge_model"], row["image_model"])
            for key in ("alignment_score", "quality_score", "aesthetic_score", "na_keys"):
                assert fresh[key] == row[key], (row["qid"], key, fresh[key], row[key])
            checked += 1
    assert checked >= 60


def test_score_table_append_overwrite_and_resume(lake):
    """离线评分结果也按配置写目标表，同一快照重放不重复追加。"""
    import lance
    from project import resolve_root
    from evaluation.t2i.v1.t2i_v1_eval_pipeline import run_pipeline
    base, _, cfg = build(lake, lake[2])
    target = resolve_root() / 'demiwtg/evaluation/t2i/v1/datasets/shared_scores.lance'
    for name, mode, count in [('first', 'append', 1), ('next', 'append', 2), ('replace', 'overwrite', 1)]:
        run = base.with_name(name)
        options = dict(responses=lake[2], target_uri=str(target), write_mode=mode, through='scores')
        state = run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).count_rows() == count
        before = lance.dataset(str(target)).version
        run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).version == before
        assert state['stages']['scores']['dataset_ref']['lance_version'] == before
