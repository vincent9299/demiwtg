import pyarrow as pa
import pytest
from demiflow import data
from demiflow.execution.artifacts import resolve_local_artifact
from tools.lake_migration.t2i_case_ownership import NEW, OLD, PROBE, relocate


def prepare(root):
    for owner, name in [(OLD, 'case'), (PROBE, 'probe')]:
        directory = root / owner / 'datasets'
        directory.mkdir(parents=True)
        (root / owner / 'README.md').write_text('retained evidence')
        for value in [1, 2]:
            data.from_arrow(pa.table({'value': [value]})).write_lance(
                str(directory / f'{name}.lance'), mode='overwrite')
        (directory / f'{name}.sqlite').write_bytes(b'journal evidence')


def test_relocated_versions_and_journals_remain_readable(tmp_path):
    prepare(tmp_path)
    result = relocate(tmp_path)
    assert result['complete'] and result['tables'] == 2
    for owner, name in [(OLD, 'case'), (PROBE, 'probe')]:
        old = tmp_path / owner / 'datasets' / f'{name}.lance'
        for version in [1, 2]:
            assert data.read_lance(str(old), version=version).take(1) == [{'value': version}]
        journal = tmp_path / owner / 'datasets' / f'{name}.sqlite'
        assert resolve_local_artifact(journal).read_bytes() == b'journal evidence'
        assert (tmp_path / NEW / 'datasets' / f'{name}.lance').is_dir()
        assert not (tmp_path / owner).exists()


def test_collision_is_rejected_before_moving_sources(tmp_path):
    prepare(tmp_path)
    (tmp_path / OLD / 'datasets' / 'probe.sqlite').write_bytes(b'another journal')
    with pytest.raises(ValueError, match='collides'):
        relocate(tmp_path)
    assert (tmp_path / OLD / 'datasets' / 'case.lance').is_dir()
    assert (tmp_path / PROBE / 'datasets' / 'probe.lance').is_dir()
    assert not (tmp_path / NEW).exists()
