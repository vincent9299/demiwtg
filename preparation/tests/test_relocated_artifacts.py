import hashlib
import json

from demiflow.execution.artifacts import read
from preparation.operaters.runfiles import rows
from preparation.operaters.runfiles import check_run_location




def test_durable_runs_are_allowed_outside_state(tmp_path):
    check_run_location(tmp_path / 'preparation/runs/new', tmp_path, tmp_path / 'datasets')
