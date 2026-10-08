import pytest


@pytest.fixture(autouse=True)
def isolated_lake(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(tmp_path / 'lake'))
