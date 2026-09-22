"""Complete review notebooks with actual pixels, full materials and request text."""
from curation.preparation.records import run_state, run_manifest
import base64
import copy
import json
from pathlib import Path


import nbformat as nb

from curation.preparation.materials import pixels
from curation.preparation.records import digest, read, rows, read_record, run_records


def review_directory(run):
    """Notebook source/presentation belongs to code; raw run data stays in Lance."""
    from curation.preparation.records import ROOT
    run = Path(run).resolve()
    from curation.preparation.records import run_relative
    relative = Path(run_relative(run)).relative_to('runs/pipeline')
    return ROOT / 'curation' / relative.parts[0] / 'reviews' / Path(*relative.parts[1:])



def display_json(value):
    # Only remove duplicated base64 from JSON: every real pixel is displayed below.
    value = copy.deepcopy(value)
    def redact(obj):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key == "url" and isinstance(val, str) and val.startswith("data:image/"):
                    obj[key] = "[实际像素见同案例图片；完整data URL保存在请求JSON]"
                else:
                    redact(val)
        elif isinstance(obj, list):
            for val in obj:
                redact(val)
    redact(value)
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def image_cell(asset, label):
    try:
        data, mime, _ = pixels(asset)
        if mime != "image/png" and mime != "image/jpeg":
            # Display conversion only, never a new reference/target or model input.
            from PIL import Image
            import io
            buffer = io.BytesIO()
            with Image.open(io.BytesIO(data)) as im:
                im.convert("RGB").save(buffer, format="PNG")
            data, mime = buffer.getvalue(), "image/png"
        encoded = base64.b64encode(data).decode()
        return nb.v4.new_code_cell(source=f"# {label}\n# 原始文件：{asset['path']}\n# sha256：{asset['sha256']}",
            execution_count=None, outputs=[nb.v4.new_output("display_data", data={mime: encoded, "text/plain": label})])
    except (ValueError, OSError) as error:
        return nb.v4.new_markdown_cell(label + "：图片无法读取：" + str(error))


def write_review(run):
    run = Path(run).resolve()
    state = run_state(run)
    last = list(state["stages"])[-1]
    from curation.preparation.records import saved_stage
    data = saved_stage(run, last)
    # ready/incomplete are filtered outputs; use the complete export checkpoint.
    if "export" in state["stages"]:
        data = saved_stage(run, "export")
    manifest = run_manifest(run)
    cells = [nb.v4.new_markdown_cell(f"# V2 {state['branch']} 完整案例\n\n"
        "保留全部成功、待完成与失败；模型审核/助手审核不等于人工golden。"
        "参考图、编辑原图、监督目标分别展示，所有图文不截断。"),
        nb.v4.new_markdown_cell("## 冻结输入与阶段\n\n" + display_json({"manifest": manifest, "execution": state}))]
    for row in data:
        cells.append(nb.v4.new_markdown_cell(f"## {row.get('task_id', row.get('concept'))} · {row.get('plan', {}).get('concept', row.get('concept'))} · {row.get('status', row.get('publication_status'))}\n\n"
            + display_json({k: v for k, v in row.items() if k not in {"materials", "answer_materials"}})))
        for label, materials in (("构题选材", row.get("materials", [])),
                                 ("实际作答材料（来源见绑定／检索记录）", row.get("answer_materials", []))):
            cells.append(nb.v4.new_markdown_cell("### " + label))
            for item in materials:
                cells.append(nb.v4.new_markdown_cell(display_json(item)))
                if item["kind"] == "image":
                    cells.append(image_cell(item["asset"], label + " / 参考图 " + item["item_id"]))
        for n, asset in enumerate(row.get('local_source_candidates', []) + row.get('source_candidates', []), 1):
            cells.append(image_cell(asset, f'检索候选{n}：未经批准不能充当编辑原图'))
        for key, label in (("edit_source", "编辑原图"), ("target", "监督目标（不进入作答输入）")):
            asset = row.get(key) or row.get("plan", {}).get(key)
            if asset:
                cells.append(image_cell(asset, label))
        for number, asset in enumerate(row.get('design_targets', []), 1):
            cells.append(image_cell(asset, f'构题候选目标 {number}（不是作答参考）'))
        for stage in ('design_candidates', 'select_edit_source_external', 'discover', 'select_focus', 'select_edit_source', "construct", "review_task", "review_target"):
            binding = row.get(stage + "_binding")
            if binding:
                cells.append(nb.v4.new_markdown_cell("### 完整实际请求：" + stage + "\n\n" + display_json(read_record(binding["request_ref"]))))
    note = nb.v4.new_notebook(cells=cells, metadata={"kernelspec": {"display_name": "Python 3 (demiwtg env)", "language": "python", "name": "demiwtg"}})
    revision = state["stages"].get("export", state["stages"][last])["version"]
    destination = review_directory(run)
    path = destination / "reviews" / f"{revision}.ipynb"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        nb.write(note, path)
    run_records(run).put('review_notebook', {'path': str(path), 'revision': revision}, immutable=False)
    return path
