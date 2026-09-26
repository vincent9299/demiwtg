"""Frozen manifests retain old locators; viewers resolve the relocated bytes."""
from evaluation.edit.v1.operaters import frozen_scores


def test_frozen_locator_resolves_without_rewriting_manifest(tmp_path, monkeypatch):
    current = tmp_path / 'benchmark/edit/v1'
    monkeypatch.setattr(frozen_scores, 'EDIT_DIR', current)
    asset = current / 'bench200/run/a/inputs/before/c001.png'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'frozen-image-bytes')
    old = tmp_path / 'benchmark/edit/bench200/run/a/inputs/before/c001.png'
    manifest = {'before': str(old)}
    assert frozen_scores.artifact_path(manifest['before']).read_bytes() == b'frozen-image-bytes'
    assert manifest['before'] == str(old)
    for unchanged in (asset, tmp_path / 'benchmark/edit/v2/example.png', tmp_path / 'elsewhere.png'):
        assert frozen_scores.artifact_path(unchanged) == unchanged


def test_frozen_judge_parsing_matches_the_retained_contract():
    from argparse import Namespace
    from evaluation.edit.v1.tests.fixtures import eval_codex_score as original
    template = frozen_scores.EVAL_DIR / 'prompts/judge_prompt_edit_qib_v2.2.md'
    args = Namespace(template=template)
    assert frozen_scores.load_template(args) == original.load_template(args)
    assert frozen_scores.EDIT_DIMS == original.EDIT_DIMS
    assert frozen_scores.PHI == original.PHI
    for text in ('{"score": 2}', '```json\n{"score": 1}\n```', 'prefix {invalid} {"score": 0} suffix'):
        assert frozen_scores._extract_json(text) == original._extract_json(text)
