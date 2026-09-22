"""The visible step cells execute the same graph and work from a fresh kernel."""
import ast
import asyncio
import copy
import inspect
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest

from curation.training.tests.test_authoring import inputs, ingest, write_rows, autonomous_inputs, design_result
from curation.training.tests.pipeline_runtime import config, load_pipeline, notebook_path
from curation.preparation.records import read, saved_stage, run_state
from curation.training.operators import TASK_CHECKS, TARGET_CHECKS
from curation.preparation.inspection import check_step_order


def steps(branch):
    return nbformat.read(notebook_path(branch).with_name('stepbystep.ipynb'), as_version=4)


@pytest.mark.parametrize('branch', ['benchmark', 'training'])
def test_bootstrap_imports_project_outside_repo_in_fresh_python(branch, tmp_path):
    book = steps(branch)
    first = next(c for c in book.cells if c.cell_type == 'code')
    assert 'bootstrap' in first.metadata['tags']
    result = subprocess.run([sys.executable, '-c', first.source + '\nassert MODE == "view_saved"\n'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


class SyncCheckpoint(ast.NodeTransformer):
    def visit_Await(self, node):
        return self.visit(node.value)

    def visit_Attribute(self, node):
        node = self.generic_visit(node)
        if node.attr in {'checkpoint_async', 'lance_checkpoint'}:
            # 节点级 Lance checkpoint 与 JSONL checkpoint 的语句形状统一比较
            node.attr = 'checkpoint'
        return node


def is_checkpoint_statement(node):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in {'checkpoint', 'checkpoint_async', 'lance_checkpoint'}
               for n in ast.walk(node))


@pytest.mark.parametrize('branch', ['benchmark', 'training'])
def test_visible_step_operators_match_canonical_chain(branch):
    whole = nbformat.read(notebook_path(branch), as_version=4)
    function = next(c.source for c in whole.cells if 'def run_pipeline' in c.source)
    body = ast.parse(function).body[0].body[1].body  # function docstring, then with run_lock
    if branch == 'training':
        attempt = next(c.source for c in whole.cells if 'def run_training_attempt' in c.source)
        body = ast.parse(attempt).body[0].body
    canonical = [SyncCheckpoint().visit(node) for node in body if is_checkpoint_statement(node)]
    actual = []
    for cell in steps(branch).cells:
        if 'step-operator' in cell.metadata.get('tags', []):
            body = ast.parse(cell.source).body[0].body[0].body  # if execute -> with run_lock
            actual.extend(SyncCheckpoint().visit(node) for node in body if is_checkpoint_statement(node))
    if branch == 'training':
        names = {n.targets[0].id for n in canonical if isinstance(n, ast.Assign)}
        actual = [n for n in actual if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in names]
    assert [ast.dump(n) for n in actual] == [ast.dump(n) for n in canonical]


@pytest.mark.parametrize('branch', ['benchmark', 'training'])
def test_step_execution_then_whole_pipeline_resume(inputs, branch):
    base = inputs[0]
    knowledge_runs, cfg, discovery, focus, draft = autonomous_inputs(inputs, branch)
    run = base / (branch + '_steps')
    book = steps(branch)
    scope = {'MODE': 'execute', 'run': run, 'knowledge_runs': knowledge_runs,
             'config': cfg, 'visual_runs': [], 'check_step_order': check_step_order}
    imports = next(c.source for c in book.cells if 'step-imports' in c.metadata.get('tags', []))
    exec(imports, scope)

    async def run_cells():
        for cell in book.cells:
            if any(t in cell.metadata.get('tags', []) for t in ('step-init', 'step-operator')):
                pending = eval(compile(cell.source, '<step notebook>', 'exec', ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), scope)
                if inspect.isawaitable(pending):
                    await pending
        return scope['state']

    asyncio.run(run_cells())
    assert saved_stage(run, 'design')[0]['status'] == 'pending_design_candidates'
    ingest(run, 'design_candidates', design_result(draft))
    asyncio.run(run_cells())
    ingest(run, 'review_task', {'checks': {k: True for k in TASK_CHECKS}, 'reason': 'fixture task review'})
    asyncio.run(run_cells())
    if branch == 'training':
        ingest(run, 'review_target', {'checks': {k: True for k in TARGET_CHECKS}, 'reason': 'fixture target review'})
        asyncio.run(run_cells())
    assert len(saved_stage(run, 'ready')) == 1
    before = run_state(run)
    whole = load_pipeline(branch)(run, knowledge_runs, cfg)
    assert whole['new_stages'] == []
    assert whole['stages'] == before['stages']
    again = asyncio.run(run_cells())
    assert again['new_stages'] == []
    assert again['stages'] == whole['stages']
    with pytest.raises(RuntimeError, match='重新运行初始化'):
        check_step_order(scope['files'], 'select')
