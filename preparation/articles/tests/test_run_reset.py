from pathlib import Path
import pytest
from demiflow.execution.artifacts import run_lock
from preparation.articles.operators.run_state import MaterialRunState
from preparation.articles.operators import run_reset


def setup(tmp_path,monkeypatch):
    relative=Path('business/datasets/test')
    run=tmp_path/relative
    run.parent.mkdir(parents=True)
    state=MaterialRunState(tmp_path,relative)
    state.save_manifest({'code':'intermediate'})
    state.save_configuration('prompt_config',{'text':'draft'})
    monkeypatch.setattr(run_reset,'run_records',lambda run:state)
    return run,state


def test_empty_run_reset_archives_scaffold_and_releases_manifest(tmp_path,monkeypatch):
    run,state=setup(tmp_path,monkeypatch)
    receipt=run_reset.reset_empty_run(run,actor='tester',reason='finalized source')
    assert (Path(receipt['archive'])/'scaffold/manifest.json').exists()
    assert state.load_manifest() is None
    state.save_manifest({'code':'final'})
    assert state.load_manifest()['code']=='final'


@pytest.mark.parametrize('activity',['calls','stage','request','active_writer'])
def test_reset_rejects_work_and_live_writer(tmp_path,monkeypatch,activity):
    run,state=setup(tmp_path,monkeypatch)
    if activity=='active_writer':
        with run_lock(run.parent/'_demiflow'/run.name),pytest.raises(RuntimeError):
            run_reset.reset_empty_run(run,actor='tester',reason='cannot')
        return
    if activity=='calls':(run.parent/'model_calls__test.sqlite').write_bytes(b'evidence')
    elif activity=='stage':Path(state.stages_uri).mkdir()
    else:state.bind_request('judge','task','sha',{'messages':[]})
    with pytest.raises(ValueError):
        run_reset.reset_empty_run(run,actor='tester',reason='cannot')
    assert state.load_manifest()['code']=='intermediate'
