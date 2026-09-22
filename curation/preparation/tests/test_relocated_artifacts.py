import hashlib
import json

from curation.preparation.contracts import read
from curation.preparation.records import rows
from curation.preparation.config import check_run_location




def test_durable_runs_are_allowed_outside_state(tmp_path):
    check_run_location(tmp_path / 'curation/preparation/runs/new', tmp_path, tmp_path / 'datasets')
