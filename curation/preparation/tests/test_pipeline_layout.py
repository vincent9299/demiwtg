"""Pipeline ownership, executable entry points, and real source inventories."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('module', ['preparation.pipeline', 'preparation.visual_pipeline',
                                    'benchmark.pipeline', 'training.pipeline', 'evaluation.pipeline'])
def test_pipeline_cli_loads_without_executing_models(module):
    result = subprocess.run([sys.executable, '-m', 'curation.' + module, '--help'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '--run' in result.stdout


def test_real_source_inventories_resolve_after_moves():
    from curation.preparation.records import code_version
    from curation.evaluation.native.contracts import implementation
    source = code_version()['code']
    assert all(any('/' + branch + '/' in path for path in source)
               for branch in ('preparation', 'benchmark', 'training', 'evaluation'))
    assert implementation()['source']


def test_training_and_benchmark_do_not_import_each_others_operators():
    root = Path(__file__).resolve().parents[2]
    for branch, other in [('training', 'benchmark'), ('benchmark', 'training')]:
        for path in (root / branch).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = ([node.module] if isinstance(node, ast.ImportFrom)
                           else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                assert not any(m and m.startswith('curation.' + other + '.') for m in modules), path
