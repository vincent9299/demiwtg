"""只读对比按原题身份配对，显式补测覆盖同题并保留来源。"""
import json
from copy import deepcopy

from benchmark.t2i.v2.operators import question_review_viewer as viewer


def test_comparison_pairs_by_original_id_and_uses_explicit_retry(monkeypatch):
    def case(key, status):
        return {'source_task_id':key, 'concept':key, 'review_status':status,
                'review_reason':status, 'request_ref':{'request_id':key+status}}

    fixtures={
        'old':{'cases':[case('b','ready'),case('a','ready'),case('old-only','ready')]},
        'new':{'cases':[case('a','failed'),case('b','ready')]},
        'retry':{'cases':[case('a','ready'),case('new-only','hold')]},
    }
    for name,payload in fixtures.items():
        payload.update(meta={'run':name},media={'same':'data:image/jpeg;base64,eA=='})
    before=deepcopy(fixtures)
    monkeypatch.setattr(viewer,'_question_review_payload',lambda project,run:fixtures[run])
    document,meta=viewer.build_question_review_comparison('.', 'old', ['new','retry'])
    payload=json.loads(document.split('<script id="review-data" type="application/json">',1)[1].split('</script>',1)[0])
    pairs={pair['source_task_id']:pair for pair in payload['cases']}
    assert meta['recorded']==4 and meta['paired']==2
    assert pairs['b']['left']['source_task_id']==pairs['b']['right']['source_task_id']=='b'
    assert pairs['a']['right']['source_run']=='retry'
    assert pairs['a']['right']['review_status']=='ready'
    assert pairs['a']['right']['prior_results'][0]['status']=='failed'
    assert pairs['old-only']['right'] is None and pairs['new-only']['left'] is None
    assert len(payload['media'])==1 and fixtures==before
