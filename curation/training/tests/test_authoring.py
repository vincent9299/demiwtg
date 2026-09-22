"""Behavioral regressions for the actual notebook graphs and role/split boundaries."""
import copy
import json
import random
from pathlib import Path

import nbformat
import pytest
from PIL import Image

from curation.preparation import records as storage
from curation.preparation.delivery import apply_reviews, eligible
from curation.preparation.materials import SplitGuard, duplicate, model_content, pixels, retrieve
from curation.training.operators import (ValidateTask, TASK_CHECKS,
                                   TARGET_CHECKS, export_record)
from curation.training.tests.pipeline_runtime import config, load_pipeline
from curation.preparation.records import digest, read, saved_stage


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
    base = tmp_path / "curation/training/runs/test"
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


def ingest(run, stage, result):
    from curation.preparation.records import run_records, read_record
    from demiflow.operator_llm.lance_journal import submit_response
    from project import resolve_root
    requests = [r for k,r in run_records(run).items().items() if k.startswith('request/'+stage+'/')]
    assert len(requests) == 1
    native = requests[0]['native_offline']
    submit_response(resolve_root(), native['request_ref'], {'result':result},
                    model=read_record(native['request_ref'])['model'],
                    metadata={'reviewer':'fixture_author','reviewer_kind':'assistant'})


def publish_fixture(run, name, values):
    from curation.preparation.stages import EncodeStage, stage_uri, stage_ref, PIPELINE_STAGE_ROWS
    from demiflow.standalone import local_data
    local_data().from_iter(lambda: iter(values)).map(EncodeStage(name)).checkpoint_lance(
        stage_uri(run, name), schema=PIPELINE_STAGE_ROWS, fingerprint=digest(values))
    return stage_ref(run,name).to_dict()


def autonomous_inputs(inputs, branch="training", missing_target=False):
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
    write_rows(upstream / "datasets/related_materials.jsonl", [{"identity": {"target_label": "流程图"},
        "material_pack": {"passages": [{"source_id": "S1", "text": "Test source with full context", "url": "test://source"}], "images": [ref]}}])
    discovery = {"status": "ok", "opportunities": [{"claim": "判断用菱形，箭头表示分支", "condition": "流程图条件判断",
        "visible_result": "菱形判断节点及分支", "knowledge_gap": "节点形状未告知", "challenge": "符号绑定",
        "image_contribution": "图中可见符号", "limitations": "fixture only", "evidence": [1, 2]}], "excluded": []}
    focus = {"status": "ok", "selected": [{"opportunity": 1, "task_type": "t2i", "reason": "fixture evidence comparison",
        "application": "判断条件映射到节点形状", "knowledge_missing_without_reference": "菱形符号",
        "edit_source": None, "source_review": None}], "rejected": []}
    return [upstream_ref], config("offline", registry, training_design="knowledge_first", max_units=None, scene_search={"image_ref": None, "external_providers": []}), discovery, focus, draft


def design_result(draft):
    return {'status': 'ok', 'candidates': [{'task_type': 't2i', 'evidence': [1,2],
        'knowledge_gap': '节点形状未告知', 'knowledge_application': draft['knowledge_application'], 'draft': draft}]}


def start(inputs, branch="training", missing_target=False, run_name=None):
    knowledge_runs, cfg, _, _, draft = autonomous_inputs(inputs, branch, missing_target)
    run = inputs[0] / (run_name or branch)
    pipeline = load_pipeline(branch)
    pipeline(run, knowledge_runs, cfg)
    assert saved_stage(run, "design")[0]["status"] == "pending_design_candidates"
    return run, pipeline, knowledge_runs, None, cfg


def complete(inputs, branch="training", missing_target=False):
    run, pipeline, knowledge_runs, _, cfg = start(inputs, branch, missing_target)
    ingest(run, "design_candidates", design_result(inputs[-1]))
    pipeline(run, knowledge_runs, cfg)
    ingest(run, "review_task", {"checks": {k: True for k in TASK_CHECKS}, "reason": "fixture checks"})
    pipeline(run, knowledge_runs, cfg)
    if branch == "training" and not missing_target:
        ingest(run, "review_target", {"checks": {k: True for k in TARGET_CHECKS}, "reason": "fixture target checks"})
        pipeline(run, knowledge_runs, cfg)
    return run, pipeline, knowledge_runs, None, cfg


def test_both_notebook_graphs_independent_shared_version_and_loss_roles(inputs):
    train, *_ = complete(inputs)
    bench, *_ = complete(inputs, "benchmark")
    t = saved_stage(train, "ready")[0]
    b = saved_stage(bench, "ready")[0]
    assert t["knowledge_version"] and b["knowledge_version"]
    seq = t["training_sample"]["sequence"]
    assert [x["role"] for x in seq if x["loss"]] == ["target"]
    assert all(not x["loss"] for x in seq[:-1])
    assert t["training_input_binding"]["actual_retrieval"] is False
    assert t["answer_materials"] == t["materials"]
    assert "answer_retrieval" not in t
    assert all(i["role"] != "target" for i in t["answer_input"]["image_roles"])
    for stage in ("design_candidates", "review_task"):
        request = storage.read_record(t[stage + "_binding"]["request_ref"])
        assert {i["role"] for i in request["image_roles"]} == {"reference"}
        assert t["target"]["sha256"] not in json.dumps(request)
        assert "data:image/png;base64," in json.dumps(request)


def test_resume_reuses_all_stages_and_keeps_previous_pending_revisions(inputs):
    run, pipeline, catalog, plans, kwargs = complete(inputs)
    previous = storage.run_state(run)
    snapshots = [r['stages'].get('construct') for k,r in storage.run_records(run).items().items() if k.startswith('revision/')]
    revisions = [list(storage.rows(r['dataset_ref']))[0]['status'] for r in snapshots if r]
    assert set(revisions) == {'pending_design_candidates', 'constructed'}
    state = pipeline(run, catalog, kwargs)
    assert state["new_stages"] == []
    assert state["stages"] == previous["stages"]
    assert saved_stage(run, "incomplete") == []


def test_modified_checkpoint_is_not_silently_reused(inputs):
    run, pipeline, catalog, plans, kwargs = complete(inputs)
    from project import resolve_root
    from demiflow.lance.checkpoint import checkpoint_sidecar_path
    ref = storage.run_state(run)["stages"]["construct"]["dataset_ref"]
    path = Path(checkpoint_sidecar_path(str(resolve_root() / ref['relative_uri'])))
    receipt = json.loads(path.read_text())
    receipt['row_count'] += 1
    path.write_text(json.dumps(receipt))
    with pytest.raises(Exception, match="row count|row_count|receipt"):
        pipeline(run, catalog, kwargs)


def test_missing_target_never_exports_training(inputs):
    run, *_ = complete(inputs, missing_target=True)
    assert saved_stage(run, "ready") == []
    assert saved_stage(run, "incomplete")[0]["status"] == "needs_target"


def test_full_notebook_contains_pixels_requests_and_failures(inputs):
    from curation.preparation.review_notebooks import write_review
    run, *_ = complete(inputs)
    note = nbformat.read(write_review(run), as_version=4)
    nbformat.validate(note)
    assert sum(bool(c.get("outputs")) for c in note.cells) >= 3
    text = "\n".join(c.source for c in note.cells)
    assert "完整实际请求：design_candidates" in text and "监督目标" in text
    assert "Test source with full context" in text


@pytest.mark.parametrize("tamper", ["pixels", "knowledge", "config"])
def test_changed_frozen_inputs_rejected_on_resume(inputs, tamper):
    run, pipeline, catalog, plans, kwargs = complete(inputs)
    if tamper == "pixels":
        # Break the authoritative lake shard, not a non-authoritative old path.
        import shutil
        from project import resolve_root
        sha = inputs[4][0]['sha256']
        shutil.rmtree(resolve_root()/'raw/images.lance')
    elif tamper == "knowledge":
        catalog = [{**catalog[0], 'row_count':catalog[0]['row_count']+1}]
    else:
        kwargs = {**kwargs, "mode": "local"}
    with pytest.raises(ValueError): pipeline(run, catalog, kwargs)


def test_unbound_offline_response_cannot_pass(inputs):
    run, pipeline, knowledge_runs, _, cfg = start(inputs, run_name="badbinding")
    from demiflow.lance.records import LanceRecordStore
    request = next(v for k,v in storage.run_records(run).items().items() if k.startswith('request/design_candidates/'))
    native = storage.read_record(request['native_offline']['request_ref'])
    LanceRecordStore(**storage.prompt_store(run)).put('response/'+native['request_sha256'],
        {'request_sha256':'wrong','model':'external','content':{'result':design_result(inputs[-1])}})
    pipeline(run, knowledge_runs, cfg)
    assert saved_stage(run, "incomplete")[0]["status"] == "failed_design_candidates"






def test_target_exact_and_near_duplicates_excluded_from_retrieval(inputs):
    _, _, registry, items, assets, plan, _ = inputs
    _, _, dhash = pixels(assets[0])
    selected, trace = retrieve(items, "流程图", SplitGuard(registry), excluded=[{**assets[0], "dhash": dhash}])
    assert [i["item_id"] for i in selected] == ["Ktext"]
    assert trace["excluded"][0]["reason"] == "target_or_source_near_duplicate"
    assert duplicate({"sha256": "a", "dhash": dhash}, {"sha256": "b", "dhash": dhash})






@pytest.mark.parametrize("mutation", ["bad_evidence", "removed_quota", "extract"])
def test_v4_validator_rejects_bad_bindings_and_legacy_fields(inputs, mutation):
    _, _, registry, items, assets, plan, draft = inputs
    draft = copy.deepcopy(draft)
    if mutation == "bad_evidence": draft["criteria"][0]["evidence"] = [99]
    if mutation == "removed_quota": draft["combo_type"] = "old"
    if mutation == "extract":
        plan = {**plan, "task_type": "edit"}
        draft["edit_type"] = "extract"
    row = {"draft": draft, "status": "constructed", "issues": [], "materials": items, "plan": plan, "branch": "training"}
    assert ValidateTask(SplitGuard(registry))(row)["status"] == "invalid_task"


def test_review_bound_to_exact_knowledge_item(inputs):
    items = copy.deepcopy(inputs[3])
    with pytest.raises(ValueError): apply_reviews(items, [review(item_id="Ktext", fingerprint="wrong")])


def test_agent_transport_has_identical_text_pixels_order_and_request_hash(inputs):
    from curation.preparation.responses import materialize
    from curation.training.prompting import request_for
    base, _, registry, items, _, plan, _ = inputs
    selected = {"task_id": plan["task_id"], "plan": plan, "branch": "training",
                "status": "selected", "materials": items, "edit_source": None}
    request = request_for(selected, "construct")
    source = storage.run_records(base/'portable').put('request/fixture',request).to_dict()
    prompt = materialize(source, base / "packet")
    same = read(prompt.parent / "request.json")
    assert same == request
    projection = read(prompt.parent / "context.json")["projection"]
    text_parts = [v["text"] for v in projection if v["type"] == "text"]
    assert text_parts[0] == request["messages"][0]["content"]
    assert all(text in prompt.read_text() for text in text_parts)
    image_hashes = [v["sha256"] for v in projection if v["type"] == "image"]
    assert image_hashes == [v["sha256"] for v in request["image_roles"]]
    assert plan["target"]["sha256"] not in prompt.read_text()
    Path(request["image_roles"][0]["path"]).write_bytes(b"changed")
    materialize(source, base / "changed-packet")
    assert read(base / "changed-packet/request.json") == request
    # Old files may change or disappear; frozen Lance pixels and request identity persist.


def test_edit_source_evidence_reaches_authors_without_leaking_to_answers(inputs):
    from curation.training.prompting import inputs_for
    _, _, _, items, assets, plan, draft = inputs
    source = {**assets[1], 'origin': 'not_verified', 'review': review(
        sha256=assets[1]['sha256'], evidence=['Named photographer and dated source'],
        scope='Visible original scene anchor')}
    row = {'materials': items, 'plan': {**plan, 'task_type': 'edit'},
           'branch': 'benchmark', 'edit_source': source, 'draft': draft}
    for stage in ('construct', 'review_task'):
        values, roles = inputs_for(row, stage)
        task = json.loads(values['payload'][0]['text'])
        evidence = task['edit_source_provenance']
        assert evidence['source'] == source['source']
        assert evidence['review'] == source['review']
        assert evidence['origin'] == 'not_verified'
        assert evidence['sha256'] == roles[-1]['sha256']
    answer, _ = model_content(draft['instruction'], items, source)
    assert 'Named photographer and dated source' not in json.dumps(answer)
    assert 'Visible original scene anchor' not in json.dumps(answer)


def test_native_local_http_graph_and_offline_render_parity(inputs, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from curation.benchmark import prompting
    from demiflow.operator_llm.recorded_json import RecordedJSONClient as LocalModel
    knowledge_runs, cfg, discovery, focus, draft = autonomous_inputs(inputs, "benchmark")
    calls = []
    results = [design_result(draft), {'checks': {k: True for k in TASK_CHECKS}, 'reason': 'fixture review'}]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({'data': [{'id': 'qwen3.8-27b'}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(payload)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            payload = json.dumps({'model': 'qwen3.8-27b', 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps({'result': results[len(calls)-1]})}}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(payload)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(prompting, 'validate_local_endpoint', lambda *args: None)
    async def forbidden(*args, **kwargs): raise AssertionError('Old LocalModel must never execute')
    monkeypatch.setattr(LocalModel, 'json', forbidden)
    cfg = config('local', inputs[2], max_units=None)
    cfg['model']['base_url'] = f'http://127.0.0.1:{server.server_port}/v1'
    run = inputs[0] / 'native_http'
    try:
        pipeline = load_pipeline('benchmark')
        pipeline(run, knowledge_runs, cfg)
        assert len(saved_stage(run, 'ready')) == 1 and len(calls) == 2
        for stage, checkpoint, index in [('design_candidates','knowledge',0), ('review_task','validate',1)]:
            row = saved_stage(run, checkpoint)[0]
            assert calls[index]['messages'] == prompting.request_for(row, stage)['messages']
        assert saved_stage(run, 'ready')[0]['design_candidates_call']['request_ref']
        assert pipeline(run, knowledge_runs, cfg)['new_stages'] == []
        assert len(calls) == 2
    finally:
        server.shutdown(); server.server_close()


def test_portable_ingest_uses_native_response_binding_and_preserves_raw(inputs):
    from curation.preparation.responses import ingest as submit
    run, pipeline, knowledge_runs, _, cfg = start(inputs, run_name='native_offline')
    key = next(k for k in storage.run_records(run).items() if k.startswith('request/design_candidates/'))
    request = storage.run_records(run).reference(key).to_dict()
    raw = inputs[0] / 'native_raw.json'; write_json(raw, {'result': design_result(inputs[-1])})
    original = raw.read_bytes()
    response = submit(request, raw, run, 'external', 'high')
    assert storage.read_record(response.to_dict())['content'] == {'result': design_result(inputs[-1])}
    pipeline(run, knowledge_runs, cfg, through='construct')
    row = saved_stage(run, 'construct')[0]
    assert row['draft'] == inputs[-1] and raw.read_bytes() == original
    assert row['design_candidates_provenance']['reasoning_effort'] == 'high'


def request_record(run, stage):
    return next(v for k,v in storage.run_records(run).items().items() if k.startswith('request/'+stage+'/'))


def republish_fixture(ref, values):
    from demiflow.lance.registry import write_registered_table
    from curation.preparation.schemas import PIPELINE_STAGE_ROWS
    from project import resolve_root
    ref,_,_ = write_registered_table(resolve_root(), 'runs/fixtures/'+digest(values)+'.lance',
        schema_name='pipeline_stage_rows',schema_version='v1',schema=PIPELINE_STAGE_ROWS,
        rows_factory=lambda: (storage.to_stage_row(row,'knowledge_base',None,0) for row in values),
        fingerprint=digest(values))
    return ref.to_dict()
