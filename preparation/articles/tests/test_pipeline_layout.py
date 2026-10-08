"""Pipeline ownership, executable entry points, source inventories and run identities."""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('module', ['subset.subset_pipeline',
                                    'preparation.concepts.concepts_pipeline',
                                    'preparation.taxonomy.taxonomy_pipeline',
                                    'collect.wiki_documents.wiki_documents_pipeline',
                                    'collect.qid_identity.qid_identity_pipeline',
                                    'preparation.documents.documents_pipeline',
                                    'preparation.document_embeddings.document_embeddings_pipeline',
                                    'preparation.qid_images.qid_images_pipeline',
                                    'preparation.image_embeddings.image_embeddings_pipeline',
                                    'preparation.qid_concepts.qid_concepts_pipeline',
                                    'preparation.qid_review.qid_review_pipeline',
                                    'preparation.visual_concepts.visual_concepts_pipeline',
                                    'preparation.images.catalog.image_catalog_pipeline',
                                    'preparation.images.consolidation.image_consolidation_pipeline',
                                    'preparation.images.dense_caption.dense_caption_pipeline',
                                    'preparation.concept_positive_images.concept_positive_images_pipeline',
                                    'preparation.concept_image_backfill.concept_image_backfill_pipeline',
                                    'curation.edit_scene_images.edit_scene_images_pipeline',
                                    'curation.concept_image_tasks.concept_image_tasks_pipeline',
                                    'curation.t2i_positive_pairs.t2i_positive_pairs_pipeline',
                                    'curation.image_facts.image_facts_pipeline',
                                    'curation.t2i_training_samples.t2i_training_samples_pipeline', 'curation.edit_training_pairs.edit_training_pairs_pipeline',
                                    'benchmark.t2i.v1.t2i_v1_benchmark_pipeline', 'benchmark.edit.v1.edit_v1_benchmark_pipeline',
                                    'benchmark.t2i.v2.t2i_v2_benchmark_pipeline', 'benchmark.edit.v2.edit_v2_benchmark_pipeline',
                                    'benchmark.t2i.coarse_screening.t2i_coarse_screening_pipeline',
                                    'benchmark.t2i.fine_screening.t2i_fine_screening_pipeline',
                                    'evaluation.t2i.case_annotation.case_annotation_pipeline',
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
    if module in {'preparation.images.consolidation.image_consolidation_pipeline',
                  'preparation.concept_positive_images.concept_positive_images_pipeline',
                  'preparation.concept_image_backfill.concept_image_backfill_pipeline'}:
        assert '--config' in result.stdout
        return
    assert '--run' in result.stdout
    assert ('--mode' if module in {'evaluation.t2i.case_annotation.case_annotation_pipeline'}
            else '--write-mode') in result.stdout


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
        'subset.subset_pipeline': ['run_pipeline'],
        'benchmark.t2i.coarse_screening.t2i_coarse_screening_pipeline': ['run_pipeline'],
        'benchmark.t2i.fine_screening.t2i_fine_screening_pipeline': ['run_pipeline'],
        'evaluation.t2i.case_annotation.case_annotation_pipeline': ['run_pipeline', 'save_annotation'],
        'preparation.qid_images.qid_images_pipeline': ['run_pipeline'],
        'preparation.image_embeddings.image_embeddings_pipeline': ['run_pipeline'],
        'preparation.qid_concepts.qid_concepts_pipeline': ['run_pipeline'],
        'preparation.qid_review.qid_review_pipeline': ['run_pipeline'],
        'preparation.visual_concepts.visual_concepts_pipeline': ['run_pipeline'],
        'preparation.concepts.concepts_pipeline': ['run_pipeline'],
        'preparation.taxonomy.taxonomy_pipeline': ['run_pipeline'],
        'collect.wiki_documents.wiki_documents_pipeline': ['run_pipeline'],
        'collect.qid_identity.qid_identity_pipeline': ['run_pipeline'],
        'preparation.documents.documents_pipeline': ['run_pipeline'],
        'preparation.document_embeddings.document_embeddings_pipeline': ['run_pipeline'],
        'preparation.images.catalog.image_catalog_pipeline': ['run_pipeline'],
        'preparation.images.consolidation.image_consolidation_pipeline': ['run_pipeline'],
        'preparation.images.dense_caption.dense_caption_pipeline': ['run_pipeline'],
        'preparation.concept_positive_images.concept_positive_images_pipeline': ['run_pipeline'],
        'preparation.concept_image_backfill.concept_image_backfill_pipeline': ['run_pipeline'],
        'curation.edit_scene_images.edit_scene_images_pipeline': ['run_pipeline'],
        'curation.concept_image_tasks.concept_image_tasks_pipeline': ['run_pipeline'],
        'curation.t2i_positive_pairs.t2i_positive_pairs_pipeline': ['run_pipeline'],
        'curation.image_facts.image_facts_pipeline': ['run_pipeline'],
        'curation.t2i_training_samples.t2i_training_samples_pipeline': ['run_pipeline', 'run_concept', 'run_attempt'],
        'curation.edit_training_pairs.edit_training_pairs_pipeline': ['run_pipeline', 'run_attempt'],
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
    from benchmark.t2i.v1.operators.runfiles import graph_digest
    assert graph_digest()


def test_real_source_inventories_resolve_after_moves():
    from preparation.articles.operators.runfiles import code_version
    from preparation.articles.operators.runfiles import source_code, PIPELINE_DIRS
    from preparation.articles.operators.runfiles import preparation_source_code
    source = code_version()['code']
    assert all(any(path.startswith(branch + '/') for path in source) for branch in PIPELINE_DIRS)
    assert not any(path.startswith('curation/benchmark/') for path in source)
    for path in source:
        if '/v1/' in path:
            relative = path.split('/v1/', 1)[1]
            assert relative == '__init__.py' or ('/' not in relative and relative.endswith('_pipeline.py')) or relative.startswith(('operators/', 'prompts/'))
    assert 'preparation/documents/documents_pipeline.py' in source
    assert 'preparation/images/catalog/image_catalog_pipeline.py' in source
    assert 'preparation/images/catalog/operators/records.py' in source
    assert 'preparation/images/images_pipeline.py' not in source
    assert 'preparation/preparation_pipeline.py' not in source
    assert 'evaluation/bagel/adapter.py' in source
    assert not any(path.startswith(('evaluation/bagel/gen/', 'evaluation/bagel/vlm/',
                                    'evaluation/bagel/data/')) for path in source)
    snapshot = source_code()
    assert 'curation/t2i_training_samples/operators/candidates.py' in snapshot
    assert 'curation/edit_training_pairs/operators/__init__.py' in snapshot
    assert not any(k.startswith(('benchmark/', 'evaluation/', 'curation/t2i_training_samples/', 'curation/edit_training_pairs/'))
                   for k in preparation_source_code())
    from curation.t2i_training_samples.operators.runfiles import source_snapshot
    training = source_snapshot()['business']
    assert 'preparation/images/catalog/operators/records.py' in training
    assert not any('/archive/' in path for path in training)


def test_training_and_benchmark_pipelines_do_not_import_each_others_operators():
    root = Path(__file__).resolve().parents[3]
    modules = ('curation.t2i_training_samples', 'curation.edit_training_pairs',
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
    from preparation.articles.operators.runfiles import run_relative, PIPELINE_DIRS
    for branch in PIPELINE_DIRS:
        assert run_relative(tmp_path / branch / 'datasets/case') == f'demiwtg/{branch}/datasets/case'
        assert run_relative(tmp_path / branch / 'datasets/case/backend') == f'demiwtg/{branch}/datasets/case__backend'



def test_preparation_uses_standard_pipeline_layers():
    import os
    root = Path(__file__).resolve().parents[2]
    assert not any((root / name).exists() for name in
                   ('data', 'runtime', 'runtime.py', 'inspection', 'configs', 'operators', 'prompts', 'tests'))
    assert {p.name for p in root.glob('*.py')} == {'__init__.py'}
    assert {p.name for p in root.iterdir() if p.is_file()} == {'__init__.py', 'README.md', 'requirements.txt'}
    assert all((root / name).is_dir() for name in ('articles', 'images'))
    # Shared objects now live below preparation/datasets. Prune before walking
    # so a source-code check never enumerates millions of production assets.
    excluded = {'datasets', 'runs', 'archive', '__pycache__', 'runtime', '.git'}
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children if name not in excluded]
        for name in files:
            if not name.endswith('.py'):
                continue
            path = Path(directory) / name
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert not any((node.module or '').startswith(prefix) for prefix in
                                   ('preparation.data', 'preparation.runtime', 'preparation.inspection')), path


# 文件名前缀直接体现任务、版本和用途；与目录规范共同检查。
PIPELINES = {
    'preparation/documents': 'documents',
    'preparation/document_embeddings': 'document_embeddings',
    'preparation/taxonomy': 'taxonomy',
    'preparation/image_embeddings': 'image_embeddings',
    'subset': 'subset',
    'preparation/concepts': 'concepts',
    'collect/wiki_documents': 'wiki_documents',
    'collect/qid_identity': 'qid_identity',
    'preparation/qid_images': 'qid_images',
    'preparation/qid_concepts': 'qid_concepts',
    'preparation/qid_review': 'qid_review',
    'preparation/visual_concepts': 'visual_concepts',
    'preparation/images/catalog': 'image_catalog',
    'preparation/images/consolidation': 'image_consolidation',
    'preparation/images/dense_caption': 'dense_caption',
    'curation/t2i_training_samples': 't2i_training_samples', 'curation/edit_training_pairs': 'edit_training_pairs',
    'benchmark/t2i/v1': 't2i_v1_benchmark', 'benchmark/t2i/v2': 't2i_v2_benchmark',
    'benchmark/edit/v1': 'edit_v1_benchmark', 'benchmark/edit/v2': 'edit_v2_benchmark',
    'benchmark/t2i/fine_screening': 't2i_fine_screening',
    'evaluation/t2i/case_annotation': 'case_annotation',
    'preparation/concept_positive_images': 'concept_positive_images',
    'preparation/concept_image_backfill': 'concept_image_backfill',
    'benchmark/t2i/coarse_screening': 't2i_coarse_screening',
    'curation/edit_scene_images': 'edit_scene_images',
    'curation/concept_image_tasks': 'concept_image_tasks',
    'curation/t2i_positive_pairs': 't2i_positive_pairs',
    'curation/image_facts': 'image_facts',
    'evaluation/t2i/v1': 't2i_v1_eval', 'evaluation/t2i/v2': 't2i_v2_eval',
    'evaluation/edit/v1': 'edit_v1_eval',
}


def test_every_pipeline_is_registered_for_layout_checks():
    import os
    root = Path(__file__).resolve().parents[3]
    discovered = set()
    # Prune data/artifacts before descent; filtering rglob results still scans
    # every production object and historical run on the shared filesystem.
    excluded = {'tests', 'runs', 'archive', 'VLMEvalKit', 'datasets', '__pycache__'}
    for branch in ('subset', 'preparation', 'curation', 'benchmark', 'evaluation', 'collect/wiki_documents', 'collect/qid_identity'):
        for directory, children, files in os.walk(root / branch):
            children[:] = [name for name in children if name not in excluded]
            if any(name.endswith('_pipeline.py') for name in files):
                discovered.add(str(Path(directory).relative_to(root)))
    assert discovered == set(PIPELINES)


def test_agents_first_rule_requires_the_project_pipeline_spec():
    """强制规范必须是 AGENTS 的第一条要求，不能沉入历史记录或只加一个可选链接。"""
    root = Path(__file__).resolve().parents[3]
    blocks = (root / 'AGENTS.md').read_text().split('\n\n')
    assert blocks[1] == '## 第一条：必须遵守项目 Pipeline 强制规范'
    assert '必须先阅读并遵守' in blocks[2]
    links = re.findall(r'\[[^\]]+\]\(([^)\s]+)\)', blocks[2])
    spec = root / 'PIPELINE_SPEC.md'
    assert spec.is_file()
    assert any((root / link).resolve() == spec.resolve() for link in links)


@pytest.mark.parametrize('relative', PIPELINES)
def test_fixed_pipeline_layout(relative):
    """New entry files, viewer layers and notebook-defined graphs are not allowed."""
    import json
    root = Path(__file__).resolve().parents[3] / relative
    prefix = PIPELINES[relative]
    # 每条现役 pipeline（包括仍可执行的 V1）都须在 README 首段引用同一权威规范。
    blocks = (root / 'README.md').read_text().split('\n\n')
    assert blocks[0].startswith('# ')
    notice = blocks[1]
    assert '项目强制规范' in notice and '必须先阅读并遵守' in notice, relative
    links = re.findall(r'\[[^\]]+\]\(([^)\s]+)\)', notice)
    spec = Path(__file__).resolve().parents[3] / 'PIPELINE_SPEC.md'
    assert any((root / link).resolve() == spec.resolve() for link in links), relative
    pipeline_source = (root / (prefix + '_pipeline.py')).read_text()
    assert 'lance.write_dataset(' in pipeline_source or '.write_lance(' in pipeline_source
    assert '.read_lance(' in pipeline_source
    assert not any(name in pipeline_source for name in
                   ('lance_checkpoint', 'checkpoint_lance', 'commit_stage_ref'))
    expected = {'__init__.py', 'README.md', prefix + '_debug.ipynb', prefix + '_pipeline.py'}
    assert {p.name for p in root.iterdir() if p.is_file()} == expected
    directories = {'operators', 'prompts', 'configs', 'tests', 'runs', 'datasets', '__pycache__', '_demiflow'}
    directories.add('reviews' if relative.startswith('curation/') else 'archive')
    if relative == 'curation/edit_scene_images':
        directories.add('archive')
    if relative in {'benchmark/t2i/v2', 'curation/edit_scene_images'}:
        directories.add('reviews')
    assert {p.name for p in root.iterdir() if p.is_dir()} <= directories
    for name in (('operators', 'prompts', 'tests') if relative.startswith('preparation/') else ('operators', 'prompts')):
        assert (root / name).is_dir()
    book = json.loads((root / (prefix + '_debug.ipynb')).read_text())
    assert len(book['cells']) == (3 if relative == 'curation/t2i_positive_pairs' else 6 if relative in {'subset', 'preparation/images/catalog', 'benchmark/t2i/fine_screening', 'preparation/concept_image_backfill', 'preparation/concept_positive_images', 'preparation/concepts'}
                                  else 5 if relative == 'preparation/qid_review'
                                  else 7 if relative in {'curation/edit_scene_images', 'evaluation/t2i/v2', 'curation/concept_image_tasks'}
                                  else 10 if relative == 'evaluation/t2i/case_annotation'
                                  else 1 if relative == 'curation/t2i_training_samples'
                                  else 2 if relative in {'curation/edit_training_pairs', 'benchmark/t2i/coarse_screening'} else 4)
    cells = book['cells']
    if relative == 'preparation/concept_positive_images':
        # 保留两格生产迁移说明，执行与只读查看仍为原来的四个代码格。
        assert [cell['cell_type'] for cell in cells] == ['markdown'] * 2 + ['code'] * 4
    if relative == 'curation/edit_scene_images':
        assert [cell['id'] for cell in cells] == [
            'imports', 'configuration', 'execution', 'inspection',
            'retrieval_probe_configuration', 'retrieval_probe_execution', 'retrieval_probe_inspection']
    for cell in cells:
        if cell['cell_type'] == 'code':
            assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                           for node in ast.walk(ast.parse(''.join(cell['source']))))
    for path in [root / (prefix + '_pipeline.py'), *(root / 'operators').rglob('*.py')]:
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                # S02.3 的具名联合编排：只允许 documents 入口调用编码正式入口，
                # 行算子仍不能反向导入任何 pipeline。
                if (relative == 'preparation/documents' and path == root / 'documents_pipeline.py'
                        and module == 'preparation.document_embeddings.document_embeddings_pipeline'):
                    assert [(alias.name, alias.asname) for alias in node.names] in [
                        [('config', 'encode_config')], [('config', 'encode_config'), ('run_pipeline', 'encode')]]
                    continue
                assert not any(part in {'ops', 'debug', 'native', 'tests', 'pipeline', 'IPython', 'nbformat'}
                               or part.endswith('_pipeline') for part in module.split('.')), path
                assert module != 'demiflow.execution.notebook', path
                assert module != 'demiflow.lance.run', path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {'exec', 'compile'}, path


@pytest.mark.parametrize('relative',['preparation/qid_images','preparation/qid_concepts'])
def test_qid_readme_records_design_and_operational_contract(relative):
    root=Path(__file__).resolve().parents[3]
    spec=(root/'PIPELINE_SPEC.md').read_text()
    assert 'README 是本流程定位、业务逻辑和设计方案的持续维护入口' in spec
    readme=(root/relative/'README.md').read_text()
    for topic in ('定位','输入','字段','阶段','配置','恢复','验收','下游'):
        assert topic in readme, (relative,topic)


@pytest.mark.parametrize('relative', PIPELINES)
def test_pipeline_entries_do_not_call_other_pipeline_entries(relative):
    """S02.3: each active entry and its notebook own one pipeline lifecycle."""
    import json
    root = Path(__file__).resolve().parents[3]
    prefix = PIPELINES[relative]
    own = relative.replace('/', '.') + '.' + prefix + '_pipeline'
    entries = {name.replace('/', '.') + '.' + value + '_pipeline'
               for name, value in PIPELINES.items()}
    paths = [root / relative / (prefix + '_pipeline.py'),
             *(root / relative / 'operators').rglob('*.py')]
    sources = [(str(path), path.read_text()) for path in paths]
    book = json.loads((root / relative / (prefix + '_debug.ipynb')).read_text())
    sources += [(relative + ':cell-' + str(i), ''.join(cell['source']))
                for i, cell in enumerate(book['cells']) if cell['cell_type'] == 'code']
    for label, source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                imports = {module, *[module + '.' + alias.name for alias in node.names]}
            else:
                imports = set()
            assert not (imports & (entries - {own})), label
            # Also prevent subprocess / dynamic-import entry strings.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in entries - {own}, label
