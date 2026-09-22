"""Display all fields of the selected formal result without rewriting its data."""
import html
import json
from pathlib import Path


def _fields(value):
    """Keep every field, including empty values and unfamiliar future fields."""
    return '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + html.escape(
        json.dumps(value, ensure_ascii=False, indent=2)
    ) + '</pre>'


def show_current_results(run, *, concepts=None, limit=None, images=True, audit=False):
    from IPython.display import display, HTML, Markdown, Image
    run = Path(run)
    from curation.preparation.stages import read_stage, stage_uri
    path = Path(stage_uri(run, 'knowledge_base'))
    if not path.exists():
        display(Markdown('本 run 尚无 Lance 知识发布阶段。历史结果请打开冻结查看册。'))
        return
    if limit is not None and limit < 1: raise ValueError('limit must be positive or None')
    records = []
    for row in read_stage(run, 'knowledge_base').iter_rows():
        if concepts is None or row['concept'] in concepts:
            records.append(row)
            if limit is not None and len(records) >= limit: break
    wanted = {im['image_id'] for row in records for t in row['knowledge'] for im in t['content']['images']}
    pixels = {}
    if images and wanted and Path(stage_uri(run,'joint_requests')).exists():
        for row in read_stage(run,'joint_requests').iter_rows():
            for iid,pixel in zip(row['joint_prompt']['image_ids'],row['pixel_images']):
                if iid in wanted:pixels[iid]=pixel
    esc = html.escape
    display(Markdown(f'结果文件：`{path}`；展示 {len(records)} 个概念。'
                     f'概念筛选：`{concepts}`；数量上限：`{limit}`；图片预览：`{images}`。'
                     '展示 pipeline 最终知识的全部字段；audit=True 可另看材料和审核过程。'))
    for row in records:
        output = ['<h2>' + esc(row['concept']) + '</h2>']
        if 'status' in row:
            labels={'visual_only':'独立视觉材料；文章分支未执行','reviewed':'已完成最终 review（模型结果）','failed':'文章处理失败；独立视觉材料状态另列',
                    'insufficient_materials':'没有足够的入选材料','no_supported_knowledge':'review 后无可保留知识'}
            output.append('<p><b>'+esc(labels.get(row['status'],row['status']))+'</b></p>')
            if row.get('status_reason'):output.append('<p>'+esc(row['status_reason'])+'</p>')
            if row['status']=='failed':output.append(_fields(row.get('audit',{}).get('validation_issues',[])))
        if not row['knowledge']:
            output.append('<p>没有保留的知识内容。</p>')
        def show_image(im):
            if images:
                pixel=pixels.get(im['image_id'],'')
                if pixel.startswith('data:image/'):
                    output.append('<figure><img style="max-width:100%;height:auto" src="'+esc(pixel,quote=True)+'"></figure>')
                else:output.append('<p>图片预览不可用：'+esc(im['image_id'])+'</p>')
            if im.get('figure_number'):output.append('<p><b>图 '+str(im['figure_number'])+'</b></p>')
            output.append(_fields(im))
        for topic in row['knowledge']:
            output.append('<h3>' + esc(topic['title']) + '</h3>')
            for pi,p in enumerate(topic['content']['paragraphs']):
                output.append('<p style="white-space:pre-wrap">'+esc(p)+'</p>')
                for im in topic['content']['images']:
                    if im.get('paragraph_index')==pi:show_image(im)
            for im in topic['content']['images']:
                if im.get('paragraph_index') not in range(len(topic['content']['paragraphs'])):show_image(im)
            output.append('<p><b>参考来源（完整字段）：</b></p>')
            for ref in topic['references']:
                url = ref.get('url', '')
                if url.startswith(('http://', 'https://')):
                    output.append('<a href="' + esc(url, quote=True) + '" target="_blank" rel="noopener noreferrer">' + esc(ref['title']) + '</a>')
                output.append(_fields(ref))
            extra_content = {k: v for k, v in topic['content'].items() if k not in {'paragraphs', 'images'}}
            extra_topic = {k: v for k, v in topic.items() if k not in {'title', 'content', 'references'}}
            if extra_content:
                output.append(_fields({'content': extra_content}))
            if extra_topic:
                output.append(_fields(extra_topic))
        if row.get('audit', {}).get('review_notes'):
            output.append('<h3>final_review 审查记录（非知识正文，编号对应待审材料）</h3>')
            output.append('<p style="white-space:pre-wrap">'+esc(row['audit']['review_notes'])+'</p>')
            for ref in row['audit'].get('review_references', []):
                output.append(_fields(ref))
        if audit:
            output.append('<h3>概念、材料及审核完整记录（非最终正文）</h3>')
            for key, value in row.items():
                if key in {'concept', 'knowledge'}:
                    continue
                output.append('<details open><summary>' + esc(key) + '</summary>' + _fields(value) + '</details>')
        display(HTML(''.join(output)))
        if row.get('visual_materials'):
            from curation.preparation.asset_io import asset_bytes
            display(Markdown('### 独立视觉材料（不依赖文章选图）'))
            for visual in row['visual_materials']:
                display(Markdown('**' + visual['image_id'] + '**'))
                if images:
                    asset = visual['image']['bytes']
                    pixel_bytes, _ = asset_bytes(asset.get('path'), asset['sha256'])
                    display(Image(data=pixel_bytes, width=720))
                display(Markdown('```json\n' + json.dumps(visual['publication'], ensure_ascii=False, indent=2) + '\n```'))
    if not records:
        display(Markdown('没有匹配的概念。'))
