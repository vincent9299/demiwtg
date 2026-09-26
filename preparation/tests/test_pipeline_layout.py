"""Pipeline ownership, executable entry points, source inventories and run identities."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('module', ['preparation.preparation_pipeline',
                                    'curation.t2i.t2i_train_pipeline', 'curation.edit.edit_train_pipeline',
                                    'benchmark.t2i.v1.t2i_v1_benchmark_pipeline', 'benchmark.edit.v1.edit_v1_benchmark_pipeline',
                                    'benchmark.t2i.v2.t2i_v2_benchmark_pipeline', 'benchmark.edit.v2.edit_v2_benchmark_pipeline',
                                    'evaluation.evaluation_pipeline',
                                    'evaluation.t2i.v1.t2i_v1_eval_pipeline', 'evaluation.t2i.v2.t2i_v2_eval_pipeline', 'evaluation.edit.v1.edit_v1_eval_pipeline'])
def test_pipeline_cli_loads_without_executing_models(module):
    # Every CLI must import even when notebook files cannot be opened.
    program = '''
import builtins, io, os, runpy, sys
real_open = builtins.open
real_io_open = io.open
def guard(opener):
    def open_file(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and str(file).endswith('.ipynb'):
            raise AssertionError('Python execution must not read notebook code')
        return opener(file, *args, **kwargs)
    return open_file
builtins.open = guard(real_open)
io.open = guard(real_io_open)
module = sys.argv[1]
sys.argv = [module, '--help']
runpy.run_module(module, run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', program, module],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '--run' in result.stdout
    assert '--write-mode' in result.stdout


def test_executable_graphs_are_python_functions_independent_of_notebooks(monkeypatch):
    import importlib
    import inspect

    original = Path.open

    def no_notebooks(path, *args, **kwargs):
        if path.suffix == '.ipynb':
            raise AssertionError('Pipeline or fingerprint depends on notebook contents')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', no_notebooks)
    entries = {
        'preparation.preparation_pipeline': ['run_pipeline', 'run_visual_pipeline', 'run_image_review', 'replay_visual_pipeline'],
        'curation.t2i.t2i_train_pipeline': ['run_pipeline', 'run_concept', 'run_attempt'],
        'curation.edit.edit_train_pipeline': ['run_pipeline', 'run_attempt'],
        'evaluation.evaluation_pipeline': ['run_pipeline', 'run_backend', 'run_judging', 'run_partition_judging'],
        'evaluation.t2i.v2.t2i_v2_eval_pipeline': ['run_pipeline', 'run_judging'],
        **{f"{kind}.{track}.{version}.{track}_{version}_{'benchmark' if kind == 'benchmark' else 'eval'}_pipeline": ['run_pipeline']
           for kind in ('benchmark', 'evaluation') for track in ('t2i', 'edit')
           for version in (('v1', 'v2') if kind == 'benchmark' else ('v1',))},
    }
    for module_name, names in entries.items():
        module = importlib.import_module(module_name)
        for name in names:
            function = getattr(module, name)
            assert Path(inspect.getsourcefile(function)) == Path(module.__file__)
            assert 'def ' + name in inspect.getsource(function)
    from benchmark.t2i.v1.operaters.runfiles import graph_digest
    from evaluation.operaters.contracts import graph_version as evaluation_version
    assert graph_digest() and evaluation_version()


def test_real_source_inventories_resolve_after_moves():
    from preparation.operaters.runfiles import code_version
    from preparation.operaters.runfiles import source_code, PIPELINE_DIRS
    from preparation.operaters.runfiles import preparation_source_code
    from evaluation.operaters.contracts import implementation
    source = code_version()['code']
    assert all(any(path.startswith(branch + '/') for path in source) for branch in PIPELINE_DIRS)
    assert not any(path.startswith('curation/benchmark/') for path in source)
    for path in source:
        if '/v1/' in path:
            relative = path.split('/v1/', 1)[1]
            assert relative == '__init__.py' or ('/' not in relative and relative.endswith('_pipeline.py')) or relative.startswith(('operaters/', 'prompts/'))
    assert 'evaluation/bagel/adapter.py' in source
    assert not any(path.startswith(('evaluation/bagel/gen/', 'evaluation/bagel/vlm/',
                                    'evaluation/bagel/data/')) for path in source)
    snapshot = source_code()
    assert 'curation/t2i/operaters/candidates.py' in snapshot
    assert 'curation/edit/operaters/__init__.py' in snapshot
    assert not any(k.startswith(('benchmark/', 'evaluation/', 'curation/t2i/', 'curation/edit/'))
                   for k in preparation_source_code())
    assert implementation()['source']


def test_training_and_benchmark_pipelines_do_not_import_each_others_operators():
    root = Path(__file__).resolve().parents[2]
    modules = ('curation.t2i', 'curation.edit',
               'benchmark.t2i.v2', 'benchmark.edit.v2')
    retired = ('curation.training', 'curation.training_t2i', 'curation.training_edit',
               'curation.benchmark', 'curation.preparation', 'curation.evaluation')
    for module in modules:
        forbidden = [other for other in modules if other != module] + list(retired)
        for path in root.joinpath(*module.split('.')).rglob('*.py'):
            if 'tests' in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                imports = ([node.module] if isinstance(node, ast.ImportFrom)
                           else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                assert not any(m and (m == prefix or m.startswith(prefix + '.'))
                               for m in imports for prefix in forbidden), path


def test_run_namespaces_use_flat_owner_directories(tmp_path):
    from preparation.operaters.runfiles import run_relative, PIPELINE_DIRS
    for branch in PIPELINE_DIRS:
        assert run_relative(tmp_path / branch / 'datasets/case') == f'demiwtg/{branch}/datasets/case'
        assert run_relative(tmp_path / branch / 'datasets/case/backend') == f'demiwtg/{branch}/datasets/case__backend'



def test_preparation_uses_standard_pipeline_layers():
    root = Path(__file__).resolve().parents[1]
    assert not any((root / name).exists() for name in
                   ('data', 'runtime', 'runtime.py', 'inspection', 'configs'))
    assert {p.name for p in root.glob('*.py')} == {
        '__init__.py', 'preparation_pipeline.py'}
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not any((node.module or '').startswith(prefix) for prefix in
                               ('preparation.data', 'preparation.runtime', 'preparation.inspection')), path


# 文件名前缀直接体现任务、版本和用途；与目录规范共同检查。
PIPELINES = {
    'preparation': 'preparation',
    'curation/t2i': 't2i_train', 'curation/edit': 'edit_train',
    'benchmark/t2i/v1': 't2i_v1_benchmark', 'benchmark/t2i/v2': 't2i_v2_benchmark',
    'benchmark/edit/v1': 'edit_v1_benchmark', 'benchmark/edit/v2': 'edit_v2_benchmark',
    'evaluation': 'evaluation',
    'evaluation/t2i/v1': 't2i_v1_eval', 'evaluation/t2i/v2': 't2i_v2_eval',
    'evaluation/edit/v1': 'edit_v1_eval',
}


def test_every_pipeline_is_registered_for_layout_checks():
    root = Path(__file__).resolve().parents[2]
    discovered = {str(path.parent.relative_to(root))
                  for branch in ('preparation', 'curation', 'benchmark', 'evaluation')
                  for path in (root / branch).rglob('*_pipeline.py')
                  if not {'tests', 'runs', 'archive', 'VLMEvalKit'} & set(path.parts)}
    assert discovered == set(PIPELINES)


@pytest.mark.parametrize('relative', PIPELINES)
def test_fixed_pipeline_layout(relative):
    """New entry files, viewer layers and notebook-defined graphs are not allowed."""
    import json
    root = Path(__file__).resolve().parents[2] / relative
    prefix = PIPELINES[relative]
    pipeline_source = (root / (prefix + '_pipeline.py')).read_text()
    assert 'lance.write_dataset(' in pipeline_source or '.write_lance(' in pipeline_source
    assert '.read_lance(' in pipeline_source
    assert not any(name in pipeline_source for name in
                   ('lance_checkpoint', 'checkpoint_lance', 'commit_stage_ref'))
    expected = {'__init__.py', 'README.md', prefix + '_debug.ipynb', prefix + '_pipeline.py'}
    if relative == 'preparation':
        expected.add('requirements.txt')
    assert {p.name for p in root.iterdir() if p.is_file()} == expected
    directories = {'operaters', 'prompts', 'tests', 'runs', 'datasets', '__pycache__'}
    directories.add('reviews' if relative.startswith('curation/') else 'archive')
    if relative == 'evaluation':
        directories |= {'t2i', 'edit', 'bagel'}
    assert {p.name for p in root.iterdir() if p.is_dir()} <= directories
    for name in ('operaters', 'prompts'):
        assert (root / name).is_dir()
    book = json.loads((root / (prefix + '_debug.ipynb')).read_text())
    assert len(book['cells']) == (1 if relative in {'preparation', 'curation/t2i', 'benchmark/t2i/v2'} else 2 if relative in {'curation/edit', 'evaluation/t2i/v2'} else 4)
    for cell in book['cells']:
        if cell['cell_type'] == 'code':
            assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                           for node in ast.walk(ast.parse(''.join(cell['source']))))
    for path in [root / (prefix + '_pipeline.py'), *(root / 'operaters').rglob('*.py')]:
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                assert not any(part in {'ops', 'debug', 'native', 'tests', 'pipeline', 'IPython', 'nbformat'}
                               or part.endswith('_pipeline') for part in module.split('.')), path
                assert module != 'demiflow.execution.notebook', path
                assert module != 'demiflow.lance.run', path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {'exec', 'compile'}, path
