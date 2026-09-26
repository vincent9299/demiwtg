"""Business model identities and role-bound native requests."""
import base64
from demiflow.execution.artifacts import digest
from preparation.operaters.inputs import pixels, asset_for_item

MODELS = {"bagel": "BAGEL-7B-MoT", "gemini": "openrouter/google/gemini-3.1-flash-image",
          "qwen2512": "Qwen-Image-2512", "qwen2511": "Qwen-Image-Edit-2511"}

def make_request(question, materials):
    """Same text and original pixels for every native adapter; no hidden rubric.

    Input source comes first (important for Qwen Edit); all models see the same
    explicit roles. Knowledge contains published text and scoped references.
    Raw provenance remains in the run, not in the generation prompt.
    """
    parts, roles = [], []
    def text(value):
        parts.append({"type": "text", "text": value})
    def picture(asset, role, label):
        data, mime, _ = pixels(asset)
        text(f"图像{len(roles)+1}：{label}")
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64," + base64.b64encode(data).decode()}})
        roles.append({"role": role, "path": asset.get("path"), "sha256": asset["sha256"], **({"blob_ref":asset["blob_ref"]} if asset.get("blob_ref") else {})})
    if question.get("edit_source"):
        picture(question["edit_source"], "edit_source", "编辑原图。只在此图上按题面编辑。")
    for item in materials:
        review = item["review"]
        if item["kind"] == "text":
            text("知识参考：\n" + item["text"] + "\n适用范围：" + review.get("support_scope", review.get("scope", ""))
                 + "\n限制：" + str(review.get("limitations") or "仅在已核验范围内使用"))
        else:
            picture(asset_for_item(item), "reference", "知识参考图，不是编辑原图或目标图。支持范围："
                    + review["support_scope"] + "；限制：" + str(review.get("limitations") or "仅在已核验范围内使用"))
    text("请输出一张完成下述任务的图像。\n题面：\n" + question["instruction"])
    messages = [{"role": "user", "content": parts}]
    return {"messages": messages, "image_roles": roles, "input_sha256": digest(messages)}

def verify_request(job):
    request = job["request"]
    if digest(request["messages"]) != request["input_sha256"]:
        raise ValueError("Request content changed")
    images = iter(request["image_roles"])
    for message in request["messages"]:
        for part in message["content"]:
            if part["type"] == "image_url":
                asset = next(images)
                data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)
                if data != pixels(asset)[0] or asset["role"] not in {"reference", "edit_source"}:
                    raise ValueError("Image bytes or role changed")
    if next(images, None) is not None:
        raise ValueError("Unused image binding")
    sources = sum(x["role"] == "edit_source" for x in request["image_roles"])
    if sources != int(job["task_type"] == "edit"):
        raise ValueError("Invalid edit-source count")
    if job["condition"] == "without_knowledge" and (job["knowledge_ids"] or any(x["role"] == "reference" for x in request["image_roles"])):
        raise ValueError("Knowledge leaked into baseline")
