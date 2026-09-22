"""Role-aware pixels, conservative near-duplicate exclusion and text/image retrieval."""
import base64
import io
import json
import re
import struct
from pathlib import Path


from PIL import Image, ImageOps
from curation.preparation.asset_io import asset_bytes, asset_file
from curation.preparation.delivery import asset_review_ok, eligible
from curation.preparation.records import digest, read


def pixels(asset):
    """原始字节 + 基础像素指纹。路径只是定位提示，身份是内容 SHA；
    文件缺失时从湖内 Lance Blob 读取（缺失/损坏/读失败分别抛错）。

    MPO 属 JPEG 多帧家族，按 image/jpeg 提供原始字节；其余格式须在
    {JPEG,PNG,WEBP,GIF,MPO} 内，不能解码给模型的格式显式报错。
    """
    data = asset_pixels(asset)
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if im.format not in {"JPEG", "PNG", "WEBP", "GIF", "MPO"}:
            raise ValueError("Unsupported image format")
        mime = "image/jpeg" if im.format in {"JPEG", "MPO"} else Image.MIME[im.format]
        gray = im.convert("L").resize((9, 8))
        samples = gray.tobytes()
        dhash = sum((samples[y * 9 + x] > samples[y * 9 + x + 1]) << (y * 8 + x)
                    for y in range(8) for x in range(8))
    return data, mime, f"{dhash:016x}"


def checked_asset(asset, role):
    if not isinstance(asset, dict) or not asset.get("source"):
        raise ValueError("Image requires provenance")
    data, mime, dhash = pixels(asset)
    review = asset.get("review", {})
    if review.get("sha256") != asset["sha256"] or not asset_review_ok(review, role):
        raise ValueError("Image role lacks a content-bound review: " + role)
    return {**asset, "role": role, "dhash": dhash, "mime": mime}


def _compute_variant_values(data):
    """镜像/缩放/适度中心裁剪的比对指纹；纯内容函数（字节已按 sha 验证）。

    EXIF 元数据损坏（PIL SyntaxError）时退回存储方向——指纹不因可选
    元数据失效。
    """
    values = []
    with Image.open(io.BytesIO(data)) as source:
        try:
            im = ImageOps.exif_transpose(source).convert('L')
        except (SyntaxError, ValueError, struct.error):
            im = source.convert('L')
        # Mirror/resize and moderate centre crops. Not a general duplicate oracle.
        for fraction in (1., .9, .8):
            dx, dy = int(im.width*(1-fraction)/2), int(im.height*(1-fraction)/2)
            crop = im.crop((dx, dy, im.width-dx, im.height-dy))
            for variant in (crop, ImageOps.mirror(crop)):
                samples = variant.resize((9,8)).tobytes()
                values.append(sum((samples[y*9+x] > samples[y*9+x+1]) << (y*8+x)
                                  for y in range(8) for x in range(8)))
    return tuple(values)


_VARIANT_CACHE: dict[str, tuple] = {}


def variant_hashes(asset):
    """缓存键 = 内容 SHA（路径/mtime 不进身份；缓存可重建）。"""
    sha = asset['sha256']
    values = _VARIANT_CACHE.get(sha)
    if values is None:
        data = asset_pixels(asset)
        values = _compute_variant_values(data)
        if len(_VARIANT_CACHE) >= 8192:
            _VARIANT_CACHE.clear()
        _VARIANT_CACHE[sha] = values
    return values


def duplicate(a, b):
    if a.get("sha256") == b.get("sha256"):
        return True
    if a.get("duplicate_group") and a.get("duplicate_group") == b.get("duplicate_group"):
        return True
    if (bool(a.get('dhash') and b.get('dhash')) and
            (int(a['dhash'],16) ^ int(b['dhash'],16)).bit_count() <= 4):
        return True
    if all(x.get('sha256') and (x.get('path') or x.get('blob_ref')) for x in (a,b)):
        return any((x ^ y).bit_count() <= 4 for x in variant_hashes(a) for y in variant_hashes(b))
    return False


def reference_options(materials, targets):
    """Original evidence numbers compatible with all supplied targets."""
    blocked = {n for n, item in enumerate(materials, 1)
               if item['kind'] == 'image'
               and any(duplicate(item['asset'], target) for target in targets)}
    figures = {(item['concept'], item.get('image_id', item['item_id']))
               for n, item in enumerate(materials, 1)
               if item['kind'] == 'image' and n not in blocked}
    eligible, excluded = [], []
    for n, item in enumerate(materials, 1):
        dependencies = {(item['concept'], iid) for iid in
                        item.get('publication', {}).get('visual_dependencies', [])}
        reason = ('target_or_source_near_duplicate' if n in blocked else
                  'required_figure_excluded' if dependencies - figures else None)
        if reason:
            excluded.append({'evidence': n, 'item_id': item['item_id'], 'reason': reason})
        else:
            eligible.append(n)
    return {'evidence': eligible, 'excluded': excluded}


class SplitGuard:
    def __init__(self, registry):
        self.registry = dict(registry) if isinstance(registry, dict) else read(registry)
        if self.registry.get("schema") != "v4-split-registry/1":
            raise ValueError("An explicit split registry is required")
        self.test = self.registry["formal_test"]
        self.images = []
        for asset in self.test.get("images", []):
            _, _, dhash = pixels(asset)
            self.images.append({**asset, "dhash": dhash})

    def check_plan(self, plan, branch):
        if branch == "training" and plan["split"] != "train":
            raise ValueError("Training plans must have split=train")
        if branch == "benchmark" and plan["split"] not in {"development", "test"}:
            raise ValueError("Benchmark plans must have split=development or test")
        if plan["split"] == "test" and self.registry.get("scope") != "frozen_formal_test":
            raise ValueError("Formal export requires a frozen formal-test registry")
        if plan["split"] == "test" and (plan["concept"] not in self.test.get("concepts", []) or
                plan["rule_family"] not in self.test.get("rule_families", [])):
            raise ValueError("Formal-test concept and rule family must be reserved before export")
        if branch == "training":
            if plan["concept"] in self.test.get("concepts", []):
                raise ValueError("Formal-test concept cannot enter training")
            if plan["rule_family"] in self.test.get("rule_families", []):
                raise ValueError("Formal-test rule family cannot enter training")

    def allows_asset(self, asset, training=False):
        if training:
            sources = json.dumps(asset.get("source", {}), ensure_ascii=False)
            if any(url in sources for url in self.test.get("source_urls", [])):
                return False
        return not any(duplicate(asset, other) for other in self.images)

    def allows_item(self, item, training=False):
        if item["kind"] == "image":
            _, _, dhash = pixels(item["asset"])
            if not self.allows_asset({**item["asset"], "dhash": dhash}):
                return False
        if training:
            if item["concept"] in self.test.get("concepts", []):
                return False
            raw_sources = json.dumps(item.get("references", item.get("asset", {}).get("source", {})), ensure_ascii=False)
            if any(url in raw_sources for url in self.test.get("source_urls", [])):
                return False
        text_hash = digest(item.get("text", ""))
        return text_hash not in self.test.get("answer_text_sha256", [])

    def check_instruction(self, instruction, training=False):
        key = digest(instruction.strip())
        if key in self.test.get("answer_text_sha256", []) or (training and key in self.test.get("question_sha256", [])):
            raise ValueError("Formal-test question/answer text leakage")


def tokens(text):
    text = text.lower()
    latin = re.findall(r"[a-z0-9]+", text)
    chinese = re.findall(r"[\u3400-\u9fff]+", text)
    return set(latin + [s[i:i+2] for s in chinese for i in range(max(1, len(s)-1))])


def search_text(item):
    if item["kind"] == "text":
        return item["concept"] + " " + item["text"]
    review = item["review"]
    # Captions rank candidates; eligibility still requires source/pixel review.
    return " ".join(str(x or "") for x in (item["concept"], review.get("support_scope"),
                    item.get("selection_review", {}).get("visible_information")))


def retrieve(items, query, guard, *, excluded=(), training=False, text_limit=3, image_limit=2):
    # Excluding a target also excludes text that requires that figure. Apply
    # before ranking so dependent text cannot reintroduce its content indirectly.
    if excluded:
        options = reference_options(items, excluded)
        allowed = set(options['evidence'])
        role_excluded = options['excluded']
        items = [item for n, item in enumerate(items, 1) if n in allowed]
    else:
        role_excluded = []
    query_tokens = tokens(query)
    candidates = []
    excluded_reasons = list(role_excluded)
    for item in items:
        if not eligible(item):
            continue
        try:
            if not guard.allows_item(item, training):
                excluded_reasons.append({"item_id": item["item_id"], "reason": "formal_test_reservation"})
                continue
            if item["kind"] == "image":
                _, _, dhash = pixels(item["asset"])
                asset = {**item["asset"], "dhash": dhash}
                if any(duplicate(asset, other) for other in excluded):
                    excluded_reasons.append({"item_id": item["item_id"], "reason": "target_or_source_near_duplicate"})
                    continue
            term_set = tokens(search_text(item))
            score = len(query_tokens & term_set) / max(1, len(query_tokens))
            if score:
                candidates.append((score, item))
        except (ValueError, OSError) as error:
            excluded_reasons.append({"item_id": item["item_id"], "reason": str(error)})
    selected = []
    counts = {"text": 0, "image": 0}
    limits = {"text": text_limit, "image": image_limit}
    for score, item in sorted(candidates, key=lambda pair: (-pair[0], pair[1]["item_id"])):
        if counts[item["kind"]] < limits[item["kind"]]:
            selected.append(item)
            counts[item["kind"]] += 1
    return selected, {"method": "lexical_zh_bigrams_en_tokens/1", "query": query,
                      "ranks": [{"item_id": item["item_id"], "score": score} for score, item in candidates],
                      "selected_ids": [i["item_id"] for i in selected], "excluded": excluded_reasons}


def asset_for_item(item):
    return checked_asset({**item["asset"], "review": {**item["review"], "sha256": item["asset"]["sha256"]}}, "reference")


def model_content(instruction, items, edit_source=None, target=None):
    content = [{"type": "text", "text": instruction}]
    mapping = []
    for number, item in enumerate(items, 1):
        if item["kind"] == "text":
            context = [{k: source[k] for k in ("source_id", "text", "context_before", "context_after", "sections",
                                               "title", "url", "raw_source_sha256", "document_sha256") if k in source}
                       for source in item["sources"]]
            payload = {"knowledge": item["text"], "review_scope": item["review"]["scope"],
                       "limitations": item["review"].get("limitations"),
                       "sources": context, "references": item["references"]}
            content.append({"type": "text", "text": f"资料{number}（知识文本及完整原文上下文）\n" + json.dumps(payload, ensure_ascii=False)})
        else:
            asset = asset_for_item(item)
            add_image(content, asset, f"资料{number}：检索参考图；只支持 {item['review']['support_scope']}。"
                      f"限制：{item['review'].get('limitations', '仅限已核验支持范围')}。不能当待编辑原图。"
                      + '\n来源和发布位置（caption只辅助定位，不是事实证明）：' + json.dumps({
                          'source': asset.get('source'), 'origin': asset.get('origin', 'not_verified'),
                          'placement': item.get('placement'), 'publication': item.get('publication')}, ensure_ascii=False))
            mapping.append({"number": number, "item_id": item["item_id"], "role": "reference", "sha256": asset["sha256"], "path": asset.get("path"), "blob_ref": asset.get("blob_ref")})
    if edit_source:
        add_image(content, edit_source, "编辑原图：在这张图上修改；不是知识参考或答案。")
        mapping.append({"role": "edit_source", "sha256": edit_source["sha256"], "path": edit_source.get("path"), "blob_ref": edit_source.get("blob_ref")})
    if target:
        add_image(content, target, "监督目标：本次仅供冻结任务的目标审核，不得进入作答输入。")
        mapping.append({"role": "target", "sha256": target["sha256"], "path": target.get("path"), "blob_ref": target.get("blob_ref")})
    return content, mapping


def add_image(content, asset, label):
    data, mime, _ = pixels(asset)
    content.extend([{"type": "text", "text": label},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}}])


def asset_pixels(asset):
    if asset.get('blob_ref'):
        from demiflow.lance.blobs import BlobRef
        from project import resolve_root
        ref = BlobRef(**asset['blob_ref'])
        if ref.sha256 != asset['sha256']: raise ValueError('Conflicting Blob identity')
        return ref.read(resolve_root())
    return asset_bytes(asset.get('path'), asset['sha256'])[0]
