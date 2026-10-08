"""题目Review的有界只读展示：原题、定稿、固定清单及真实调用输入。"""
import base64
import hashlib
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import lance
from PIL import Image
from demiflow.operator_llm.call_ref import read_call
from .case_viewer import _rows, MAX_TEXT_BYTES, MAX_MEDIA_BYTES, MAX_DOCUMENT_BYTES
from .images import image_data_url, positive_image_data_url
from .run_tables import RunTables


def recorded_codex_response(call, root):
    """Read a successful response or the actual recording of a failed session."""
    if call.get('partial_response') is not None:
        return call['partial_response']
    ref = call['response_ref']
    try:
        return read_call(ref, root)
    except KeyError:
        if 'journal_path' not in ref:
            raise
        error = read_call({**ref, 'kind': 'error'}, root)
        return error['call']['partial_response']


def _question_review_snapshot(project, run_id):
    """固定当前提交版本，只读全批的短字段；完整输入和图片留到选页后读取。"""
    project=Path(project);root=project.parent;owner=project/'benchmark/t2i/v2'
    settings=json.loads((owner/'runs'/run_id/'config.json').read_text())
    state=RunTables(root,str(owner/'datasets'/f'records__{run_id}.lance')).load() or {}
    progress=state.get('question_review') or {}
    ref=progress.get('outputs',{}).get('question_reviews')
    if state.get('phase')=='question_review_running' and Path(progress['stage_uri']).exists():
        version=lance.dataset(progress['stage_uri']).version
        if version>progress['start_version']:
            ref={'uri':progress['stage_uri'],'version':version}
    overview=_rows(ref,['source_task_id','concept','review_status'])
    return {'settings':settings,'state':state,'ref':ref,'overview':overview}


def _question_review_payload(project, run_id, *, page=1, page_size=None, snapshot=None):
    project=Path(project);root=project.parent
    snapshot=snapshot or _question_review_snapshot(project,run_id)
    settings,state,ref,overview=(snapshot[k] for k in ('settings','state','ref','overview'))
    if type(page) is not int or page<1 or (page_size is not None and
            (type(page_size) is not int or not 1<=page_size<=20)):
        raise ValueError('Review page must be positive; page_size must be 1..20')
    pages=max(1,(len(overview)+page_size-1)//page_size) if page_size else 1
    if page>pages:raise ValueError('Review page exceeds the fixed snapshot')
    selected=overview[(page-1)*page_size:page*page_size] if page_size else overview
    predicate=None
    if page_size:
        predicate='source_task_id IN ('+','.join("'"+r['source_task_id'].replace("'","''")+"'" for r in selected)+')' if selected else 'false'
    rows=_rows(ref,['concept','source_task_id','task_id','review_status','review_reason','original_instruction',
        'original_test_points_json','instruction','test_points','requirements_json','requires_new_answer',
        'question_revision','review_result_json','review_call_json','authoring_images_json','authoring_variant'],
        predicate=predicate)
    order={row['source_task_id']:index for index,row in enumerate(selected)}
    rows.sort(key=lambda row:order[row['source_task_id']])
    cases=[];media={};text_bytes=0;media_bytes=0
    for row in rows:
        call=json.loads(row['review_call_json'] or '{}');texts=[];images=[];request=None
        if call.get('request_ref'):
            request=read_call(call['request_ref'],root)
            for message in (request.get('payload') or request).get('messages',[]):
                content=message['content'];content=[{'type':'text','text':content}] if isinstance(content,str) else content
                texts.extend(part['text'] for part in content if part['type']=='text')
                images.extend(part['image_url']['url'] for part in content if part['type']=='image_url')
        actual=[];references=json.loads(row['authoring_images_json'] or '[]')
        for i,image in enumerate(images):
            item={'number':i+1}
            try:
                image_ref=references[i]['object_ref'];item['reference']=image_ref
                encoded=(image_data_url(image_ref) if row['authoring_variant']=='standard' else positive_image_data_url(image_ref))
                if isinstance(image,dict):
                    if hashlib.sha256(encoded.encode()).hexdigest()!=image.get('data_uri_sha256'):
                        raise ValueError('实际请求图片摘要不匹配，未替换为候选图片')
                elif encoded!=image:
                    raise ValueError('实际请求图片内容不匹配')
                key=image_ref['sha256']
                if key not in media:
                    with Image.open(io.BytesIO(base64.b64decode(encoded.split(',',1)[1]))) as picture:
                        picture.thumbnail((240,240));buffer=io.BytesIO()
                        picture.convert('RGB').save(buffer,format='JPEG',quality=78)
                    value='data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()
                    media_bytes+=len(value)
                    if media_bytes>MAX_MEDIA_BYTES:raise ValueError('Review预览媒体超过64MiB')
                    media[key]=value
                item['media']=key
            except (OSError, ValueError, IndexError, KeyError) as error:
                if 'MiB' in str(error):raise
                item['error']=str(error)
            actual.append(item)
        native_tools=[];callbacks=call.get('environment',{}).get('observations',[])
        if call.get('response_ref') and call.get('runtime')=='codex':
            response=recorded_codex_response(call,root)
            callbacks=response.get('observations',callbacks)
            for event in response.get('events',[]):
                item=event.get('params',{}).get('item',{})
                if event.get('method')=='item/completed' and item.get('type') in {'commandExecution','viewImage','webSearch'}:
                    native_tools.append({key:item[key] for key in (
                        'id','type','status','command','cwd','aggregatedOutput','exitCode','path',
                        'query','action','results') if key in item})
        result=json.loads(row['review_result_json'] or 'null')
        case={k:v for k,v in row.items() if k not in {'review_call_json','authoring_images_json','review_result_json'}}
        recorded_settings=(request or {}).get('settings', {})
        case.update(result=result,actual_request='\n'.join(texts) if texts else None,
                    actual_images=actual,native_tool_events=native_tools,operator_observations=callbacks,
                    available_references=references,
                    request_ref=call.get('request_ref'),response_ref=call.get('response_ref'),
                    actual_parameters=({'model':request.get('model'),'prompt_version':request.get('prompt_version'),
                        'operators':(request.get('environment') or {}).get('operators',[]),
                        **{key:recorded_settings.get(key) for key in ('reasoning_effort','web_search','shell_tool','view_image')}}
                        if request else None))
        text_bytes+=len(json.dumps(case,ensure_ascii=False).encode())
        if text_bytes>MAX_TEXT_BYTES:raise ValueError('Review查看文字超过16MiB，未截断')
        cases.append(case)
    meta={'run':run_id,'updated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'prompt_versions':sorted({case['actual_parameters']['prompt_version'].split('/')[0]
            for case in cases if (case.get('actual_parameters') or {}).get('prompt_version')}),
        'phase':state.get('phase','not_submitted'),'complete':state.get('complete',False),
        'expected':settings['sample_size'],'recorded':len(overview),'counts':dict(Counter(row['review_status'] for row in overview)),
        'shown':len(cases),'page':page,'pages':pages,'page_size':page_size,
        'source':settings['review_source'],'results':ref,'released':state.get('candidates')}
    return {'meta':meta,'cases':cases,'media':media}


def build_question_review_browser(project, run_id, *, page=1, page_size=None, snapshot=None):
    data=_question_review_payload(project,run_id,page=page,page_size=page_size,snapshot=snapshot)
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    document=Path(__file__).with_suffix('.html').read_text().replace('__REVIEW_DATA__',payload)
    if len(document.encode())>MAX_DOCUMENT_BYTES:raise ValueError('Review页面超过96MiB')
    return document,data['meta']


def question_review_browser_controls(project, run_id, *, page_size=10, height=4000):
    """返回Notebook只读选页控件；每批最多20题，刷新不提交模型。"""
    import ipywidgets as widgets
    from .case_viewer import notebook_browser

    if type(page_size) is not int or not 1<=page_size<=20:
        raise ValueError('Review page_size must be 1..20')
    project=Path(project);directory=project/'benchmark/t2i/v2/runs'/run_id
    snapshot=_question_review_snapshot(project,run_id)
    picker=widgets.Dropdown(description='明细批次',layout=widgets.Layout(width='520px'))
    refresh=widgets.Button(description='刷新全量进度',icon='refresh')
    previous=widgets.Button(description='上一批');following=widgets.Button(description='下一批')
    status=widgets.HTML()
    view=widgets.HTML()
    handles={'busy':False}

    def options():
        rows=snapshot['overview']
        return [(f'{i//page_size+1}：{i+1}–{min(i+page_size,len(rows))} / {len(rows)} · '+rows[i]['concept'],i//page_size+1)
                for i in range(0,len(rows),page_size)] or [('尚无已保存结果',1)]

    def render():
        document,meta=build_question_review_browser(project,run_id,page=picker.value,page_size=page_size,snapshot=snapshot)
        relative=f'runs/{run_id}/question_review_page_{picker.value:03d}.html'
        target=project/'benchmark/t2i/v2'/relative
        temporary=target.with_suffix('.html.tmp');temporary.write_text(document);temporary.replace(target)
        previous.disabled=picker.value<=1;following.disabled=picker.value>=meta['pages']
        status.value=f"已保存 {meta['recorded']} / {meta['expected']} 题 · 当前第 {meta['page']} / {meta['pages']} 批；批内可逐题翻页、搜索。刷新只读取后台结果。"
        view.value=notebook_browser(document,relative,height=height)

    def change(event):
        if not handles['busy']:
            try:render()
            except Exception as error:
                from html import escape
                status.value='查看失败，未截断内容：'+escape(str(error))

    def reload_results(_):
        nonlocal snapshot
        handles['busy']=True
        try:
            snapshot=_question_review_snapshot(project,run_id)
            old=picker.value;picker.options=options();picker.value=min(old,len(picker.options))
        finally:handles['busy']=False
        change(None)

    picker.options=options();picker.value=1
    picker.observe(change,names='value');refresh.on_click(reload_results)
    previous.on_click(lambda _:setattr(picker,'value',picker.value-1))
    following.on_click(lambda _:setattr(picker,'value',picker.value+1))
    render()
    return widgets.VBox([widgets.HBox([refresh,previous,following]),picker,status,view])


def build_question_review_comparison(project, left_run, right_runs, *, initial_concept=''):
    """按原题身份只读对比；右侧按显式运行顺序采用最后一条，不按状态挑结果。"""
    if (not isinstance(left_run,str) or not isinstance(right_runs,(list,tuple))
            or not 1<=len(right_runs)<=7 or any(not isinstance(run,str) for run in right_runs)
            or len(set([left_run,*right_runs]))!=1+len(right_runs)):
        raise ValueError('Review comparison requires 2..8 distinct, explicit runs')
    sides=[{},{}];media={};sources=[];text_bytes=0;media_bytes=0
    for index,run in enumerate([left_run,*right_runs]):
        payload=_question_review_payload(project,run)
        text_bytes+=len(json.dumps({'meta':payload['meta'],'cases':payload['cases']},ensure_ascii=False).encode())
        if text_bytes>MAX_TEXT_BYTES:raise ValueError('Review对比来源文字超过16MiB')
        sources.append(payload['meta'])
        for key,value in payload['media'].items():
            if key in media:
                if media[key]!=value:raise ValueError('Same image identity has different comparison previews')
                continue
            media_bytes+=len(value)
            if media_bytes>MAX_MEDIA_BYTES:raise ValueError('Review对比媒体超过64MiB')
            media[key]=value
        side=sides[0 if index==0 else 1]
        for case in payload['cases']:
            key=case['source_task_id']
            previous=side.get(key)
            history=[] if previous is None else [*previous['prior_results'],{
                'run':previous['source_run'],'status':previous['review_status'],
                'reason':previous['review_reason'],'request_ref':previous['request_ref']}]
            side[key]={**case,'source_run':run,'prior_results':history}
    keys=list(dict.fromkeys([*sides[0],*sides[1]]))
    if len(keys)>1000:raise ValueError('Review comparison exceeds 1000 questions')
    pairs=[{'source_task_id':key,'concept':(sides[0].get(key) or sides[1][key])['concept'],
            'left':sides[0].get(key),'right':sides[1].get(key)} for key in keys]
    meta={'updated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
          'recorded':len(pairs),'paired':sum(bool(p['left'] and p['right']) for p in pairs),
          'left_run':left_run,'right_runs':list(right_runs),'sources':sources}
    encoded=json.dumps({'meta':meta,'cases':pairs,'media':media,'initial_concept':initial_concept},
                       ensure_ascii=False).replace('<','\\u003c')
    template=Path(__file__).with_name('question_review_comparison.html').read_text()
    document=template.replace('__REVIEW_DATA__',encoded)
    if len(document.encode())>MAX_DOCUMENT_BYTES:raise ValueError('Review对比页面超过96MiB')
    return document,meta
