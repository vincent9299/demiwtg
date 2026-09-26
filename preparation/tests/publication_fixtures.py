from preparation.operaters import runfiles as storage
"""Synthetic publication fixtures shared by pipeline tests; isolated lake only."""
import json
import random

import pytest
from PIL import Image

from preparation.operaters.results import to_stage_row
from demiflow.execution.artifacts import digest
from preparation.tests.test_confirm_images import decision
from preparation.operaters.images import FinalizeVisualMaterials, VISUAL_PROTOCOL
from preparation.operaters.images import (
    RecordPrimaryImageSelection, PrepareImageReview, ApplyConfirmedImageSelection)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def write_rows(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values))


def review(**extra):
    return {"decision": "accept", "reviewer": "test_fixture", "reviewer_kind": "assistant",
            "reason": "Unit-test fixture only", "evidence": ["fixture"], "scope": "test",
            "non_generated": True, "identity_checked": True, "support_scope": "test diagram", **extra}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIWTG_DATASETS_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    # code_version is real and its paths refer to the repo, not the temporary data root.
    monkeypatch.setattr(storage, "code_version", lambda: {"fixture_code": 1})
    monkeypatch.setattr(storage, "preparation_source_code", lambda: {"fixture_code": "test"})
    base = tmp_path / "preparation/runs/test"
    base.mkdir(parents=True)
    assets = []
    for n in range(3):
        rng = random.Random(n)
        im = Image.frombytes("RGB", (90, 80), bytes(rng.randrange(256) for _ in range(90*80*3)))
        path = base / f"fixture{n}.png"
        im.save(path)
        sha = digest(path.read_bytes())
        assets.append({"path": str(path), "sha256": sha, "source": {"url": "test://fixture"},
                       "review": review(sha256=sha)})
    text = {"item_id": "Ktext", "fingerprint": "fptext", "kind": "text", "concept": "流程图",
            "text": "流程图以菱形表示判断，箭头表示分支走向。", "references": [{"url": "test://source"}],
            "sources": [{"text": "Test source with full context"}], "knowledge_version": "shared-v1", "review": review()}
    visual = {"item_id": "Vimage", "fingerprint": "fpimage", "kind": "image", "concept": "流程图",
              "asset": assets[0], "upstream_status": "keep_candidate", "selection_review": {},
              "knowledge_version": "shared-v1", "review": review(support_scope="流程图菱形判断")}
    catalog = base / "knowledge.jsonl"
    write_rows(catalog, [text, visual])
    registry = base / "splits.json"
    write_json(registry, {"schema": "v4-split-registry/1", "scope": "development_only",
                         "formal_test": {"concepts": [], "rule_families": [], "images": []}})
    plan = {"task_id": "task1", "concept": "流程图", "task_type": "t2i", "split": "train",
            "rule_family": "flow-symbol", "intent": "流程图条件判断", "selection": {
                "method": "manual", "item_ids": ["Ktext", "Vimage"], "reason": "test fixture"}, "target": assets[1]}
    draft = {"status": "ok", "instruction": "绘制流程图：判断条件后分别走两个分支。", "condition": "条件判断",
             "knowledge_application": "依据流程图符号约定选择可见节点形状", "criteria": [{
                 "requirement": "条件采用菱形", "evidence": [1, 2], "observable_region": "判断节点",
                 "allowed_variation": "颜色/布局可以变化"}], "edit_type": None, "anchor": "", "preserve": []}
    return base, catalog, registry, [text, visual], assets, plan, draft


def publish_fixture(run, name, values):
    from preparation.operaters.results import EncodeStage, PIPELINE_STAGE_ROWS
    from preparation.operaters.runfiles import stage_uri, stage_ref
    from demiflow import data
    data.from_iter(lambda: iter(values)).map(EncodeStage(name)).checkpoint_lance(
        stage_uri(run, name), schema=PIPELINE_STAGE_ROWS, fingerprint=digest(values))
    return stage_ref(run,name).to_dict()


def republish_fixture(ref, values):
    from demiflow.lance.registry import write_registered_table
    from preparation.operaters.results import PIPELINE_STAGE_ROWS
    from project import resolve_root
    ref,_,_ = write_registered_table(resolve_root(), 'runs/fixtures/'+digest(values)+'.lance',
        schema_name='pipeline_stage_rows',schema_version='v1',schema=PIPELINE_STAGE_ROWS,
        rows_factory=lambda: (to_stage_row(row,'knowledge_base',None,0) for row in values),
        fingerprint=digest(values))
    return ref.to_dict()


def article_publication(inputs, branch="benchmark", missing_target=False):
    """Synthetic upstream publication, deliberately not a production case plan."""
    base, _, registry, _, assets, _, draft = inputs
    upstream = base / (branch + "_knowledge")
    def image(asset, iid):
        return {"image_id": iid, "bytes": {"path": asset["path"], "sha256": asset["sha256"], "generation_origin": "existing"},
                "record": {"image_id": iid, "url": "test://fixture", "caption": "流程图条件判断两个分支菱形"}}
    ref = image(assets[0], "figure1")
    originals = [ref]
    if not missing_target:
        target = assets[1]
        originals.append(image(target, "scene1"))
    kb = {"concept": "流程图", "case_id": "fixture", "status": "reviewed",
          "knowledge": [{"title": "判断节点", "content": {"paragraphs": ["流程图以菱形表示判断，箭头表示分支走向。"],
              "images": [{"image_id": "figure1", "paragraph_index": 0, "caption": "判断节点", "limitations": "test fixture", "figure_number": 1}]},
              "references": [{"kinds": ["text"], "source_ids": ["S1"], "paragraph_indices": [0], "url": "test://source"}]}],
          "images": originals, "published_images": [ref], "audit": {"selected_image_ids": ["figure1"]}}
    kb['published_passages'] = [{'source_id':'S1','text':'Test source with full context','url':'test://source'}]
    upstream_ref = publish_fixture(upstream, 'knowledge_base', [kb])
    return [upstream_ref]


def description():
    return dict(caption='图示有分支', representation='diagram', view_tags=['front'],
                objects=[], text_regions=[], observability_issues=[], uncertainties=[])


def image_record(asset):
    return {'image_id':'a', 'bytes':{'path':asset['path'], 'sha256':asset['sha256']},
            'record':{'url':'test://fixture'}}


def reviewed_image(asset):
    item = {**decision('a', 'keep'), 'image_metadata':description(),
            'visual_support':{'supports':'判断分支', 'region':'中央', 'limitations':'仅此图'}}
    row = dict(case_id='C', batch_id='B',
        image_prompt={'image_ids':['a'], 'selection_protocol':VISUAL_PROTOCOL,
                      'metadata_required_ids':['a']},
        pixel_images=['fixture'], pixel_roles=[{'image_id':'a','original_sha256':asset['sha256']}],
        prompt_result={'images':[item]})
    second = PrepareImageReview()(RecordPrimaryImageSelection()(row))
    second['prompt_result'] = {'images':[item]}
    result = ApplyConfirmedImageSelection()(second)['image_decisions'][0]
    return {**image_record(asset), 'selection_review':result}
