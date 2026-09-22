"""节点级 Lance checkpoint 续跑语义（P4）测试：DatasetRef、阶段指纹、revision、补登记、搬迁。"""
import json
import shutil

import pytest

from curation.training.tests.test_authoring import autonomous_inputs, inputs  # noqa: F401
from curation.training.tests.pipeline_runtime import load_pipeline
from curation.preparation.records import read, read_stage_ref, saved_stage, run_state

pytest.importorskip("lance")


def test_stage_commits_dataset_ref_and_resume_reuses_without_reexecution(inputs):  # noqa: F801
    runs, cfg, *_ = autonomous_inputs(inputs)
    run = inputs[0] / "stage_lance"
    first = load_pipeline("training")(run, runs, cfg, through="training_materials")
    assert first["new_stages"] == ["training_materials"] and first["reused_stages"] == []
    ref = read_stage_ref(run, "training_materials")
    assert ref and ref["relative_uri"].startswith("runs/pipeline/")
    assert ref["lance_version"] >= 1
    latest = run_state(run)
    assert "dataset_ref" in latest["stages"]["training_materials"]
    # 同输入续跑：完成阶段按回执重放，不重执行上游，新增执行为零
    second = load_pipeline("training")(run, runs, cfg, through="design")
    assert "training_materials" in second["reused_stages"] and "training_materials" not in second["new_stages"]
    assert second["new_stages"] == ["design"]
    # payload 逐字还原业务行
    rows = saved_stage(run, "training_materials")
    assert rows and rows[0]["concept"] and rows[0].get("unit_id")


def test_config_change_requires_new_run_not_silent_reuse(inputs):  # noqa: F801
    """配置/模型参数变化 → run manifest 闸门拒绝（use a new run），不得静默复用旧阶段。"""
    runs, cfg, *_ = autonomous_inputs(inputs)
    run = inputs[0] / "stage_config_guard"
    load_pipeline("training")(run, runs, cfg, through="training_materials")
    cfg2 = {**cfg, "seed": cfg["seed"] + 1}
    with pytest.raises(ValueError, match="use a new run"):
        load_pipeline("training")(run, runs, cfg2, through="training_materials")


def test_offline_response_growth_creates_new_revision(inputs):  # noqa: F801
    """extra（离线响应/阶段附加输入）增长 → 同 run 新阶段 revision，旧 revision 保留。"""
    runs, cfg, *_ = autonomous_inputs(inputs)
    from demiflow.standalone import local_data
    from curation.training.authoring import AuthoringRunFiles
    files = AuthoringRunFiles(inputs[0] / 'stage_resp_unit', runs, 'training', cfg, {'graph':'unit'})
    data = local_data()
    rows_a = [{"task_id": "t1", "concept": "c", "status": "s", "x": 1}]
    ds_a = data.from_iter(lambda: iter([dict(r) for r in rows_a]))
    out_a = files.lance_checkpoint(ds_a, "unit_stage", extra={"responses": ["r1"]})
    assert list(out_a.take_all())[0]["task_id"] == "t1"
    files.finish()
    ref1 = read_stage_ref(files.run, "unit_stage")
    assert ref1 is not None
    # 离线响应增加（extra 变化）→ 新 revision 位置；旧表固定版本保留
    out_b = files.lance_checkpoint(ds_a, "unit_stage", extra={"responses": ["r1", "r2"]})
    files.finish()
    ref2 = read_stage_ref(files.run, "unit_stage")
    assert ref2["relative_uri"] != ref1["relative_uri"]
    import lance

    assert lance.dataset(str(_root() / ref1["relative_uri"]),
                         version=ref1["lance_version"]).count_rows() == 1
    from curation.preparation.records import saved_stage
    assert [r["task_id"] for r in saved_stage(files.run, "unit_stage")] == ["t1"]


def test_empty_stage_commits_valid_empty_table(inputs):  # noqa: F801
    runs, cfg, *_ = autonomous_inputs(inputs, missing_target=True)
    run = inputs[0] / "stage_empty_export"
    state = load_pipeline("training")(run, runs, cfg, through="export")
    ref = read_stage_ref(run, "ready")
    assert ref is not None  # 空表也是有效提交
    assert saved_stage(run, "ready") == []


def test_register_failure_after_commit_can_re_register_without_rerun(inputs, monkeypatch):  # noqa: F801
    runs, cfg, *_ = autonomous_inputs(inputs)
    run = inputs[0] / "stage_register_retry"
    from demiflow.lance.registry import Catalog

    real_register = Catalog.register
    boom = {"armed": True}

    def flaky(self, ref):
        if boom["armed"]:
            boom["armed"] = False
            raise RuntimeError("simulated registry outage")
        return real_register(self, ref)

    monkeypatch.setattr(Catalog, "register", flaky)
    with pytest.raises(RuntimeError, match="simulated registry outage"):
        load_pipeline("training")(run, runs, cfg, through="training_materials")
    # 表已提交但登记失败：重跑走回执重放，补登记，不重执行
    monkeypatch.setattr(Catalog, "register", real_register)
    state = load_pipeline("training")(run, runs, cfg, through="training_materials")
    assert state["new_stages"] == [] and "training_materials" in state["reused_stages"]
    assert read_stage_ref(run, "training_materials") is not None


def test_dataset_root_relocation_keeps_pinned_reads_and_identity(inputs, tmp_path, monkeypatch):  # noqa: F801
    runs, cfg, *_ = autonomous_inputs(inputs)
    run = inputs[0] / "stage_relocate"
    load_pipeline("training")(run, runs, cfg, through="training_materials")
    ref = read_stage_ref(run, "training_materials")
    rows_before = saved_stage(run, "training_materials")
    # 数据根整体搬迁（含 runs 层 + registry），业务读取不变
    new_root = tmp_path / "relocated_datasets"
    shutil.copytree(_root(), new_root, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("blobs", "*.partial"))
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(new_root))
    rows_after = saved_stage(run, "training_materials")
    assert rows_after == rows_before, "搬迁数据根后固定版本读取结果必须一致"


def _root():
    from project import resolve_root

    return resolve_root()
