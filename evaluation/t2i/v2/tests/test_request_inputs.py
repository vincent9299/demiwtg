"""实际请求展示必须区分被选图片与候选池。"""
import json
from demiflow.objects import LocalObjectStore
from evaluation.t2i.v2.operators.answers import historical_answer_identity, answer_identity, PrepareAnswer, answer_template
from evaluation.t2i.v2.operators.run_tables import RunTables
from evaluation.t2i.v2.operators.request_view import answer_view
from evaluation.t2i.v2.tests.test_pipeline import picture
from evaluation.t2i.v2.t2i_v2_eval_pipeline import config
from project import resolve_root


def test_historical_input_reads_selected_refs_not_candidate_pool():
    root=resolve_root();ref=LocalObjectStore(root/'objects').put(picture()).to_dict()
    q={'task_id':'q','instruction':'当时实际文本','authoring_variant':'with_positive_images',
       'authoring_images_json':json.dumps([{'kind':'image','object_ref':ref}]*5)}
    models=config(answer_models=[{'backend':'modelhub','model':'fixture','answer_mode':mode} for mode in ('text_only','positive_images')])['answers']
    for i,m in enumerate(models):
        refs=[{'number':1,'object_ref':ref}] if i else []
        records=RunTables(root,f'demiwtg/evaluation/t2i/v2/datasets/records__view__arm{i:02d}.lance')
        records.start_answer(historical_answer_identity(m,q),{'instruction':'已落盘实际正文','reference_images':refs,'config':m})
        v=answer_view(root,'view',i,m,q,{},lambda ref,side:ref['sha256'])
        assert v['text']=='已落盘实际正文' and len(v['images'])==i
        assert '所附图像' not in v['text']
    assert answer_identity(models[1],q)!=historical_answer_identity(models[1],q)


def test_missing_call_does_not_render_new_template_as_history():
    root=resolve_root();m=config(answer_models=[{'backend':'modelhub','model':'fixture'}])['answers'][0]
    v=answer_view(root,'missing',0,m,{'task_id':'q','instruction':'原始题目'}, {},lambda *a:None)
    assert not v['available'] and not v['images'] and 'text' not in v


def test_reused_historical_answer_follows_original_request_directory():
    root=resolve_root();m=config(answer_models=[{'backend':'modelhub','model':'fixture'}])['answers'][0]
    q={'task_id':'q','instruction':'原题'}
    old=RunTables(root,'demiwtg/evaluation/t2i/v2/datasets/records__original__arm00.lance')
    old.start_answer(historical_answer_identity(m,q),{'instruction':'原调用正文','reference_images':[],'config':m})
    m['reuse_answers_from']={'uri':str(root/'demiwtg/evaluation/t2i/v2/datasets/answer_results__original__arm00.lance'),'version':1}
    v=answer_view(root,'reused',0,m,q,{},lambda *args:None)
    assert v['available'] and v['text']=='原调用正文' and 'original__arm00' in v['source']


def test_imagerag_declares_preserve_without_changing_initial_or_legacy_identity(tmp_path, monkeypatch):
    import base64
    import copy
    import io
    from pathlib import Path
    import pytest
    from PIL import Image
    from demiflow import data
    from demiflow.image_generation import ImageGenerator
    from demiflow.execution.file_ref import JsonArtifactRef
    from evaluation.t2i.v2.operators.answers import answer_bindings
    from evaluation.t2i.v2.operators.imagerag import initial_model

    preset = json.loads((Path(__file__).parents[1] / 'configs/qwen21_imagerag_20261007.json').read_text())
    raw_model = preset['answers'][0]
    model = config(answer_models=[raw_model])['answers'][0]
    assert model['image_encoding'] == 'preserve'
    for filename in ('qwen21_imagerag_20261007.json', 'bagel_imagerag_20261007.json'):
        preset_model = json.loads((Path(__file__).parents[1] / 'configs' / filename).read_text())['answers'][0]
        assert config(answer_models=[preset_model])['answers'][0]['image_encoding'] == 'preserve'
    legacy = copy.deepcopy(model)
    legacy.pop('image_encoding')
    explicit_png = {**legacy, 'image_encoding': 'png'}
    row = {'task_id': 'fixture', 'concept': 'example', 'instruction': 'original question'}
    assert answer_identity(initial_model(model), row) == answer_identity(initial_model(legacy), row)
    assert answer_identity(model, row) != answer_identity(legacy, row)
    assert answer_identity(explicit_png, row) == answer_identity(legacy, row)
    with pytest.raises(ValueError, match='image_encoding'):
        config(answer_models=[{**raw_model, 'image_encoding': 'jpeg'}])

    buf = io.BytesIO()
    with Image.new('RGB', (64, 40), 'orange') as im:
        im.save(buf, format='JPEG')
    original = buf.getvalue()
    ref = LocalObjectStore(tmp_path / 'objects').put(original).to_dict()
    row['rag_json'] = json.dumps({'status': 'retrieved', 'references': [
        {'number': 1, 'caption': 'matching architecture', 'object_ref': ref}]})
    records = RunTables(tmp_path, 'records.lance')
    prepared = PrepareAnswer(model, records)(row)
    assert prepared['answer_ready'] and prepared['answer_images'] == [original]
    model = {**model, 'service': None, 'shared_service': None}
    monkeypatch.setattr(ImageGenerator, 'remote', lambda *a: picture())
    rows = []
    data.from_items([prepared]).map_image_async(
        template=answer_template(model), model=model, inputs=answer_bindings(model),
        output='generated_image', call_output='answer_call', error_output='answer_error',
        journal_path=tmp_path / 'generation.sqlite', object_store=tmp_path / 'out',
        max_requests=1, image_encoding=model['image_encoding'], when=lambda r: r['answer_ready'],
    ).map(lambda r: rows.append(r) or r).run_stream(log_every=0)
    assert rows[0]['answer_error'] is None
    request = JsonArtifactRef(**rows[0]['answer_call']['input_ref']).read()
    assert request['image_encoding'] == 'preserve'
    assert request['body']['image'][0].startswith('data:image/jpeg;')
    assert base64.b64decode(request['body']['image'][0].split(',')[1]) == original
    assert 'matching architecture' in request['body']['prompt'] and 'original question' in request['body']['prompt']
