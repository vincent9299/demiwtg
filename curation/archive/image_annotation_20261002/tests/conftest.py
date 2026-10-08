"""隔离公共图库与模拟端点；不读取生产数据或调用真实模型。"""
import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lance
import pyarrow as pa
import pytest
from demiflow import data
from demiflow.lance.transaction import registered_table_edit
from demiflow.objects import LocalObjectStore
from PIL import Image
from preparation.images.catalog.operaters.schema import IMAGES


def response():
    return {'description': {'caption': '纯色方块位于画面中央。', 'representation': 'photo',
            'view_tags': ['front'], 'objects': [{'name': '方块', 'location': '中央', 'visible_features': '纯色'}],
            'text_regions': [], 'observability_issues': [], 'uncertainties': []},
            'richness': 3, 'richness_reason': '单一纯色主体，可见信息少。'}


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMIWTG_DATASETS_ROOT', str(tmp_path / 'datasets'))
    from project import resolve_root
    root = resolve_root()
    store = LocalObjectStore(root / 'objects')
    rows = []
    for color in ('red', 'green', 'blue'):
        out = io.BytesIO()
        Image.new('RGB', (32, 24), color).save(out, format='PNG')
        raw = out.getvalue()
        rows.append({'sha256': hashlib.sha256(raw).hexdigest(), 'image_uri': store.put(raw).uri,
                     'descriptions': [], 'image_scores': [], 'concept_matches': [], 'concept_scores': [],
                     'published_concepts': ['foreign-owner'], 'release_ids': ['retained-release'],
                     'future_column': color})
    target = root / 'datasets/images.lance'
    schema = pa.schema([*IMAGES, ('future_column', pa.string())])
    with registered_table_edit(root, 'datasets/images.lance', schema_name='curated_images', schema_version='v1'):
        data.from_arrow(pa.Table.from_pylist(rows, schema=schema)).write_lance(str(target), mode='overwrite', schema=schema)
    return {'root': root, 'rows': rows, 'target': target,
            'source': {'uri': 'datasets/images.lance', 'version': lance.dataset(str(target)).version}}


@pytest.fixture
def endpoint():
    state = {'requests': [], 'get_count': 0, 'payload': response(), 'status': 200}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state['get_count'] += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'fixture'}]}).encode())
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append(request)
            self.send_response(state['status'])
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            body = {'model': 'fixture', 'choices': [{'message': {'content': json.dumps({'result': state['payload']})},
                                                     'finish_reason': 'stop'}]}
            self.wfile.write(json.dumps(body).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['url'] = 'http://127.0.0.1:' + str(server.server_port) + '/v1'
    yield state
    server.shutdown()
    server.server_close()
    thread.join()
