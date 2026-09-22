"""Notebook entry points must reject stale kernels before constructing datasets."""
import ast
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest


def cells(name):
    return [''.join(c['source']) for c in json.loads((Path(__file__).resolve().parents[2] / (name + '.ipynb') if (Path(__file__).resolve().parents[2] / (name + '.ipynb')).exists() else Path(__file__).resolve().parents[2] / 'legacy/notebooks' / (name + '.ipynb')).read_text())['cells']
            if c['cell_type'] == 'code']


@pytest.mark.parametrize('name', ['preparation/debug'])
def test_operator_signatures_and_stale_kernel_guard(name, tmp_path):
    sources = cells(name)
    setup = next(s for s in sources if 'def _assert_current_kernel' in s)
    scope = {}
    exec(setup, scope)
    for source in sources:
        compile(source, name, 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            target = scope.get(node.func.id)
            if not inspect.isclass(target) or not target.__module__.startswith('curation.'):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                continue
            inspect.signature(target).bind(*[None] * len(node.args), **{k.arg: None for k in node.keywords})
    guard = next(n for n in ast.parse(setup).body if isinstance(n, ast.FunctionDef) and n.name == '_assert_current_kernel')
    path = tmp_path / 'operator.py'
    path.write_text('# updated after kernel startup')
    namespace = {'Path': Path, 'get_ipython': lambda: SimpleNamespace(kernel=True),
                 'sys': SimpleNamespace(modules={'curation.test': SimpleNamespace(__file__=str(path))})}
    exec(compile(ast.Module(body=[guard], type_ignores=[]), '<guard>', 'exec'), namespace)
    with pytest.raises(RuntimeError, match='重启内核'):
        namespace['_assert_current_kernel']()
    os.utime(path, (1, 1))
    namespace['_assert_current_kernel']()
