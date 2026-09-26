"""Single-cell training ETL: registered inputs, real Lance, mocked model responses."""
import ast
import asyncio
import importlib
import json
from pathlib import Path

import httpx
import nbformat
import pytest

from demiflow import data
from preparation.tests.publication_fixtures import inputs
from project import resolve_root
from curation.t2i.operaters.results import TRAINING_SAMPLES, decode_audit
from curation.t2i.tests.test_pipeline import setup_run, fixture_response


@pytest.mark.parametrize('accept', [True, False])
def test_notebook_published_inputs_training_export_and_inline_images(inputs, monkeypatch, accept):
    concepts = ['莜面栲栳栳', '猪鼻龟']
    _, sources, _ = setup_run(inputs, monkeypatch, concepts=(*concepts, '未选择'))
    root = resolve_root()
    posted = []

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'glm/glm-5.3-flash'}]})
        payload = json.loads(request.content)
        posted.append(payload)
        assert payload['model'] == 'glm/glm-5.3-flash'
        assert payload['max_tokens'] == 16384
        text = json.dumps(payload['messages'], ensure_ascii=False)
        assert '未选择' not in text
        if '你是 T2I 训练样本的设计者' in text:
            response = fixture_response('design_candidates', str(len(posted)))
            response['candidates'][0]['input_materials'] = [1]
            response['candidates'][0]['draft']['instruction'] += ' <script>fixture</script>'
        else:
            assert '你审核一条 T2I 训练候选' in text
            response = fixture_response('review_sample', str(len(posted)), accept=accept)
        return httpx.Response(200, json={'model': payload['model'], 'choices': [
            {'message': {'content': json.dumps({'result': response})}, 'finish_reason': 'stop'}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)))
    import IPython.display
    shown = []
    monkeypatch.setattr(IPython.display, 'display', shown.append)
    book = nbformat.read(Path(__file__).resolve().parents[1] / 't2i_train_debug.ipynb', as_version=4)
    nbformat.validate(book)
    assert len(book.cells) == 1
    source = book.cells[0].source.replace('DATA_ROOT = PROJECT.parent', f'DATA_ROOT = Path({str(root)!r})')
    source = source.replace('ARTICLE_SOURCES = [{"uri": "demiwtg/preparation/datasets/articles.lance", "version": 4}]', 'ARTICLE_SOURCES = []')
    source = source.replace('VISUAL_SOURCES = [{"uri": "demiwtg/preparation/datasets/images.lance", "version": 5}]', f'VISUAL_SOURCES = {sources!r}')
    code = compile(source, 't2i_train_debug.ipynb:training-etl', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    scope = {}
    asyncio.run(eval(code, scope))

    import lance
    output = lance.dataset(str(scope['TABLE_URI']), version=scope['VERSION'])
    assert output.schema == TRAINING_SAMPLES
    assert output.count_rows() == (4 if accept else 0)
    assert scope['TABLE_URI'].parent == root / 'demiwtg/curation/t2i/datasets'
    assert scope['TABLE_URI'].name.startswith('training_samples__glm53_training_')
    samples = output.to_table().to_pylist()
    if accept:
        assert {row['concept'] for row in samples} == set(concepts)
    html = shown[-1].data
    assert html.count('<tr') == len(samples) + 1
    assert '<script>fixture</script>' not in html
    if accept:
        assert '&lt;script&gt;fixture&lt;/script&gt;' in html
        assert html.count('<details>') == len(samples) * 2  # reference + target
        assert 'data:image/' in html and ';base64,' in html
    else:
        incomplete = scope['state']['stages']['incomplete']['dataset_ref']
        failures = (data.read_lance(str(root / incomplete['relative_uri']),
            version=incomplete['lance_version']).map(decode_audit).take_all())
        assert failures
    calls = len(posted)
    assert calls > 0
    asyncio.run(eval(code, scope))
    assert len(posted) == calls  # Same run reads committed tables, without new calls.
    assert scope['samples'] == samples


def test_retained_modules_have_no_dangling_training_imports():
    root = Path(__file__).resolve().parents[1]
    importlib.import_module('curation.t2i')
    for path in (root / 'operaters').rglob('*.py'):
        relative = path.relative_to(root).with_suffix('')
        importlib.import_module('curation.t2i.' + '.'.join(relative.parts))
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('curation.t2i.operaters.'):
                imported = importlib.import_module(node.module)
                for name in node.names:
                    assert hasattr(imported, name.name), (path, node.module, name.name)
