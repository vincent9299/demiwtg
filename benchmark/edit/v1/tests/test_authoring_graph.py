"""V1 Edit authoring graph: plan import, dispatch, offline construct and audits."""
import json
from pathlib import Path

import pytest
from project import historical_evidence, default_root, evidence_key

def evidence():
    return historical_evidence(default_root())

from preparation.operaters import runfiles as storage
from preparation.operaters.runfiles import saved_stage, run_manifest
from demiflow.execution.artifacts import digest


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(storage, "code_version", lambda: {"fixture_code": 1})
    from benchmark.edit.v1.operaters import runfiles
    monkeypatch.setattr(runfiles, "BENCH_DIR", tmp_path / "benchmark/edit/v1")
    from PIL import Image
    bench = tmp_path / "benchmark/edit/v1"
    images = bench / "focus200/images"
    images.mkdir(parents=True)
    sources = []
    for n, color in enumerate([(120, 30, 30), (30, 120, 30)], 1):
        path = images / f"0000{n}_实体_v0.png"
        Image.new("RGB", (48, 64), color).save(path)
        sources.append((path, digest(path.read_bytes())))
    plan = tmp_path / "plan.jsonl"
    row = {"qid": "e001", "seq": 0, "instance": "软银 NAO", "level": "L3", "suite": "basic",
           "protocol_version": "edit-v6.1-image-first", "edit_type": "replace", "alt_type": "adjust",
           "main_domain": "数字与互联网文化",
           "images": [{"file": f"images/0000{n}_实体_v0.png", "sha256": sha,
                       "batch": "quality_regen_v1" if n == 1 else "main",
                       "generator": "codex-imagegen" if n == 1 else "qwen-image-3.0-pro"}
                      for n, (_, sha) in enumerate(sources, 1)],
           "texts": [{"image_index": 0, "edit_type": "replace",
                      "text": "样本编号：e001\n【实体名】软银 NAO\n【先看图】…"}]}
    plan.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return tmp_path, plan, sources


def build(lake):
    from benchmark.edit.v1.edit_v1_benchmark_pipeline import config, run_pipeline
    project = lake[0]
    run = project / "benchmark/edit/v1/datasets/offline"
    cfg = config("offline", author_model="gpt-5.6-sol")
    return run, run_pipeline(run, lake[1], cfg), cfg


CONSTRUCTED = {"task": "edit", "qid": "e001", "status": "constructed",
               "edit_instruction": "把最左侧机器人换成另一型号", "edit_type": "replace",
               "suite": "basic", "level": "L3", "level_reason": {},
               "targeting_types": ["唯一属性直指"], "consequence_types": ["倒影同步"],
               "special_obligation_types": [], "preservation_types": ["计数布局保持"],
               "premise_types": ["定位前提"], "hop_types": ["过程-因果"], "scene_types": ["实体密度"],
               "knowledge_categories": ["物理规律"], "knowledge_domains": ["人造物体"],
               "weak_points": ["倒影失同步"], "product_checks": [{"obligation_id": "R1",
               "factors": ["倒影失同步", "接触悬浮"]}],
               "evidence_audit": {"visible_facts": [{"id": "V1", "fact": "四台机器人", "anchor": "中部"}],
                                  "edit_targets": ["V1"], "distractors": [], "uncertain_or_rejected": []},
               "evidence_receipt": [{"element": "V1: 机器人", "anchor": "中部", "used_in": ["edit_instruction"],
                                     "source": "image", "confidence": "high"}],
               "reasoning": "V1 →[结论] R1 …", "notes": "", "cannot_reason_code": ""}


def test_offline_graph_dispatches_and_pends(lake):
    run, result, _ = build(lake)
    assert result
    manifest = run_manifest(run)
    assert manifest["pipeline_module"] == "benchmark.edit.v1"
    assert manifest["protocol"]["authoring"] == "v6.1-image-first"
    plan_rows = saved_stage(run, "plan")
    assert plan_rows[0]["status"] == "plan_ready"
    dispatch = saved_stage(run, "dispatch")
    assert dispatch[0]["job_id"] == "e001_a0_0_replace"
    requests = saved_stage(run, "requests")
    active = next(r for r in requests if r["status"] == "ready_to_author")
    assert active["prompt_images"] and active["prompt_images"][0].startswith("data:image/png;base64,")
    assert active["image_roles"] == [{"role": "edit_source_before", "sha256": lake[2][0][1]}]
    assert saved_stage(run, "construct")[0]["status"] == "pending_construct"
    assert not saved_stage(run, "questions")


def test_binding_construct_response_joins_expected_meta(lake):
    from demiflow.operator_llm.lance_journal import submit_response
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    run, _, cfg = build(lake)
    active = next(r for r in saved_stage(run, "requests") if r["status"] == "ready_to_author")
    native = read_record(active["construct_binding"]["request_ref"])["native_offline"]
    submit_response(resolve_root(), native["request_ref"], {"result": CONSTRUCTED},
                    model=cfg["author"]["model"])
    from benchmark.edit.v1.edit_v1_benchmark_pipeline import run_pipeline
    rerun = lake[0] / "benchmark/edit/v1/datasets/offline"
    run_pipeline(rerun, lake[1], cfg)
    exported = [r for r in saved_stage(rerun, "export") if r["status"] == "audited"]
    assert len(exported) == 1
    question = exported[0]["question"]
    assert question["_protocol"] == "edit-v6.1-image-first"
    assert question["_job_qid"] == "e001"
    assert question["_edit_type"] == "replace"
    assert question["_target_level"] == "L3"
    assert question["difficulty"] == "L3"
    assert question["_sample_image"].startswith("focus200/images/")
    assert exported[0]["audit_warnings"] == []


def test_cannot_construct_and_echo_violation_are_explicit(lake):
    from benchmark.edit.v1.operaters.prompting import apply_construct
    from benchmark.edit.v1.operaters.audit import AuditConstruct
    base = {"status": "ready_to_author", "task_id": "e001_a0_0_replace", "qid": "e001",
            "instance": "实体", "target_level": "L3", "suite": "basic", "target_edit_type": "replace",
            "source_image": {"path": "/tmp/x.png", "sha256": "a" * 64, "batch": "main",
                             "generator": "g"},
            "construct_binding": {"request_ref": {}}}
    run = lake[0] / "benchmark/edit/v1/datasets/apply"
    cannot = apply_construct({**base, "construct_result": {"status": "cannot_construct",
        "cannot_reason_code": "target_not_visible", "edit_instruction": "", "level": ""}}, run=run)
    assert cannot["status"] == "cannot_construct"
    assert AuditConstruct()(cannot)["status"] == "cannot_construct_audited"
    bad_type = {**CONSTRUCTED, "edit_type": "adjust"}
    authored = apply_construct({**base, "construct_result": bad_type}, run=run)
    assert authored["status"] == "authored"
    audited = AuditConstruct()(authored)
    assert audited["status"] == "invalid_construct"
    assert any("edit_type" in w for w in audited["audit_warnings"])


def test_historical_bench200_meta_matches():
    """The expected_meta contract must reproduce the frozen bench200 join."""
    from benchmark.edit.v1.operaters.plan import expected_meta
    questions_path = storage.ROOT / "benchmark/edit/v1/bench200/questions.jsonl"
    rows = [json.loads(line) for line in evidence().read_text(evidence_key(questions_path)).splitlines()
            if line.strip()][:20]
    for question in rows:
        row = {"qid": question["_job_qid"], "instance": question["_instance"],
               "target_level": question["_target_level"], "suite": question["_suite"],
               "target_edit_type": question["_edit_type"],
               "source_image": {"file": question["_file"],
                                "path": str(storage.ROOT / "benchmark/edit/v1" / question["_sample_image"]),
                                "sha256": question["_sha256"], "batch": question["_batch"],
                                "generator": question["_generator"]}}
        meta = expected_meta(row)
        for key, value in meta.items():
            assert question.get(key) == value, (question["qid"], key, value, question.get(key))


def test_protocol_enums_match_the_v6_1_yaml():
    """The audit's closed enumerations must stay identical to the protocol text."""
    from benchmark.edit.v1.operaters.audit import ENUM_FIELDS, CANNOT_REASON_CODES
    import yaml
    text = yaml.safe_load((storage.ROOT / "benchmark/edit/v1/prompts/tasks.yaml").read_text())["prompts"]["construct"]["template"]
    if not text:
        pytest.skip("protocol file not present")
    for field, values in ENUM_FIELDS.items():
        for value in values:
            assert value in text, (field, value)
    for code in CANNOT_REASON_CODES:
        assert code in text


def test_final_question_table_append_overwrite_and_resume(lake):
    """V1 新输出支持追加和覆盖；同一完成阶段续跑保持固定版本。"""
    import lance
    from demiflow.operator_llm.lance_journal import submit_response
    from preparation.operaters.runfiles import read_record
    from project import resolve_root
    from benchmark.edit.v1.edit_v1_benchmark_pipeline import run_pipeline
    base, _, cfg = build(lake)
    target = resolve_root() / 'demiwtg/benchmark/edit/v1/datasets/shared_questions.lance'
    for name, mode, count in [('first', 'append', 1), ('next', 'append', 2), ('replace', 'overwrite', 1)]:
        run = base.with_name(name)
        options = dict(target_uri=str(target), write_mode=mode)
        run_pipeline(run, lake[1], cfg, through='requests', **options)
        active = next(r for r in saved_stage(run, 'requests') if r['status'] == 'ready_to_author')
        native = read_record(active['construct_binding']['request_ref'])['native_offline']
        submit_response(resolve_root(), native['request_ref'], {'result': CONSTRUCTED}, model=cfg['author']['model'])
        state = run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).count_rows() == count
        before = lance.dataset(str(target)).version
        run_pipeline(run, lake[1], cfg, **options)
        assert lance.dataset(str(target)).version == before
        assert state['stages']['questions']['dataset_ref']['lance_version'] == before
