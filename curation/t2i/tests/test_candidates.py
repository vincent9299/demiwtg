"""Preserved evidence/dependency checks and actual design request rendering."""
import copy
import json
from pathlib import Path

import pytest

from preparation.operaters import runfiles as storage
from demiflow.execution.artifacts import read
from preparation.operaters.inputs import SplitGuard
from preparation.tests.publication_fixtures import inputs
from curation.t2i.operaters.candidates import ExpandCandidates
from curation.t2i.tests.fixtures import designed_row


def test_required_figure_travels_with_text_even_if_model_cites_only_text(inputs):
    row, cfg = designed_row(inputs)
    seed = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    assert [m['kind'] for m in seed['materials']] == ['text', 'image']
    assert seed['focus']['evidence'] == [1, 2]


def test_formal_family_reservation_uses_bound_visual_dependencies(inputs):
    row, cfg = designed_row(inputs)
    seed = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    registry = read(inputs[2]); registry['formal_test']['rule_families'] = [seed['plan']['rule_family']]
    inputs[2].write_text(json.dumps(registry))
    assert list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]['status'] == 'invalid_candidate'


def test_invalid_candidate_does_not_discard_valid_sibling(inputs):
    row, cfg = designed_row(inputs)
    cfg['tasks_per_unit'] = 2
    row['design_policy']['max_candidates'] = 2
    bad = copy.deepcopy(row['candidate_design']['candidates'][0]); bad['evidence'] = [999]
    row['candidate_design']['candidates'].append(bad)
    assert [r['status'] for r in ExpandCandidates(SplitGuard(inputs[2]), cfg)(row)] == ['constructed', 'invalid_candidate']


def test_agent_transport_has_identical_text_pixels_order_and_request_hash(inputs, monkeypatch):
    from preparation.prompts.responses import materialize
    from curation.t2i.operaters import prompting
    base, _, registry, items, _, plan, _ = inputs
    from curation.t2i.t2i_train_pipeline import config
    selected, _ = designed_row(inputs)
    cfg = config()
    selected['status'] = 'knowledge_available'
    run = base / 'portable'
    storage.run_records(run).put('manifest', {'config': cfg})
    pack, _ = prompting.prompt_config(run, cfg)
    original = prompting.inputs_for
    calls = []
    def counted_inputs(row, stage):
        calls.append(stage)
        return original(row, stage)
    monkeypatch.setattr(prompting, 'inputs_for', counted_inputs)
    prepared = prompting.prepare_design(selected, run=run, pack=pack)
    assert calls == ['design_candidates']  # 同一次准备只读取/组装一遍图文。
    request = storage.read_record(prepared['design_candidates_binding']['request_ref'])
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


def test_t2i_rejects_edit_candidate(inputs):
    row, cfg = designed_row(inputs)
    candidate = row['candidate_design']['candidates'][0]
    candidate.update(task_type='edit', edit_intent='改变当前对象的状态', draft=None)
    result = list(ExpandCandidates(SplitGuard(inputs[2]), cfg)(row))[0]
    assert result['status'] == 'invalid_candidate'
    assert 'only T2I candidates' in str(result['issues'])
    assert 'training_input_binding' not in result
    assert not result.get('export_ready')


@pytest.mark.parametrize('task_types', [['edit'], ['t2i', 'edit']])
def test_t2i_material_input_rejects_mixed_task_configuration(task_types):
    from curation.t2i.t2i_train_pipeline import run_pipeline
    with pytest.raises(ValueError, match='only task_types'):
        run_pipeline('unused', [], {'task_types': task_types})


def test_t2i_design_request_rejects_edit_source(inputs):
    from curation.t2i.operaters.prompting import inputs_for
    row, _ = designed_row(inputs)
    row['edit_source'] = inputs[4][0]
    with pytest.raises(ValueError, match='no edit source'):
        inputs_for(row, 'design_candidates')
