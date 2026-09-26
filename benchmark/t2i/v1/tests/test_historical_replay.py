"""End-to-end offline authoring replay on real historical r11 evidence.

Reads the historical provenance samples and raw API responses (read-only),
binds them into an isolated lake and drives the new graph to exported
questions. No model is called; the raws are the frozen r11 authoring outputs.
"""
import json
from pathlib import Path

import pytest
from project import historical_evidence, default_root, evidence_key

def evidence():
    return historical_evidence(default_root())

from preparation.operaters import runfiles as storage
from preparation.operaters.runfiles import saved_stage

REPO = Path(__file__).resolve().parents[4]
SAMPLES = REPO / "benchmark/t2i/v1/bench200/provenance/samples_v60_uniform_r11.jsonl"
RAW_DIR = REPO / "benchmark/t2i/v1/bench200/provenance/r11_synth/raw"
QUESTIONS = REPO / "benchmark/t2i/v1/bench200/questions.jsonl"


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(storage, "code_version", lambda: {"fixture_code": 1})
    return tmp_path


def test_replay_two_historical_authoring_calls_end_to_end(lake):
    pytest.importorskip("demiflow")
    historical = {json.loads(line)["qid"]: json.loads(line)
                  for line in evidence().read_text(evidence_key(QUESTIONS)).splitlines() if line.strip()}
    samples = [json.loads(line) for line in evidence().read_text(evidence_key(SAMPLES)).splitlines()
               if line.strip()][:2]
    # 终版题库 qid 即 sample_id（一题一实例）；raw 响应文件名带模型后缀
    usable = [s for s in samples if s["sample_id"] in historical]
    if len(usable) < 1:
        pytest.skip("first provenance samples have no frozen question")
    samples_path = lake / "samples.jsonl"
    samples_path.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in usable),
                            encoding="utf-8")

    from benchmark.t2i.v1.t2i_v1_benchmark_pipeline import config, run_pipeline
    from benchmark.t2i.v1.operaters.prompting import extract_json_object
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    from demiflow.operator_llm.lance_journal import submit_response

    run = lake / "benchmark/t2i/v1/datasets/replay"
    cfg = config("offline", ["xiaoyao/gpt-5.6-sol"], author_model="gpt-5.6-sol",
                 limit=len(usable))
    run_pipeline(run, samples_path, cfg, through="requests")
    for row in saved_stage(run, "requests"):
        if row["status"] != "ready_to_author":
            continue
        raw_path = RAW_DIR / f"{row['qid']}.json"
        if evidence_key(raw_path) not in evidence().entries:
            continue
        raw = json.loads(evidence().read_text(evidence_key(raw_path)))
        content = raw["choices"][0]["message"]["content"]
        question = extract_json_object(content)
        native = read_record(row["synthesize_binding"]["request_ref"])["native_offline"]
        submit_response(resolve_root(), native["request_ref"], {"result": question},
                        model=cfg["author"]["model"])
    state = run_pipeline(run, samples_path, cfg)
    exported = [r for r in saved_stage(run, "export") if r["status"] == "audited"]
    assert exported, "replay should produce audited questions from bound historical responses"
    for row in exported:
        frozen = historical.get(row["sample_id"])
        if frozen:
            assert row["question"]["gen_prompt"] == frozen["gen_prompt"]
            assert row["question"]["level"] == frozen["level"]
    assert state["stages"]["questions"]["dataset_ref"]["row_count"] == len(exported)
