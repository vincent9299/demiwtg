#!/usr/bin/env python3
"""Generate file://-friendly data for tag_tree_explorer.html (no HTTP server needed).

The viewer normally fetch()es taxonomy.json + concepts.json (datasets/demiwtg/meta/), which
browsers BLOCK under the file:// protocol (null origin). This script wraps each JSON
as a classic <script> that assigns a global (window.__TAXONOMY__ / window.__CONCEPTS__),
so the viewer works on double-click with NO running server.

概念行来自 concepts.json（四字段契约：name/aliases/carriers/taxonomy）；docs 层草稿
（state/collect/concepts_docs_draft.jsonl，desc 退役后的知识文本落点）在构建时以
docs 字段 join 进概念行，供详情面板展示。

Generated artifacts (gitignored, NOT data) go to viewer/build/:
    build/taxonomy.js / build/concepts.js          sidecars (default)
    build/imgs.js                                    概念 → 图片索引（路径 + VLM 打分）
                                                       （由 datasets/demiwtg/meta/instance_images.jsonl
                                                       ——统一权威主清单（2026-09-06 起，原 images.jsonl
                                                       已收官退役）——现场聚合，
                                                       每项 {p, km, ri, cap}：相对路径/kb_match/richness/caption，
                                                       按 kb_match 降序（同分按 richness 降序）；
                                                       路径为 ../datasets/demiwtg/blobs/... 原图，不生成缩略图；
                                                       需经 HTTP 服务打开查看器才能显示图片，
                                                       双击 file:// 时浏览器禁止读取父目录资源）
    build/tag_tree_explorer.standalone.html              single self-contained file
                                                           （standalone 不含图片：不内嵌字节）

Usage:
    python3 viewer/build_viewer.py                 # write build/taxonomy.js + build/concepts.js + build/imgs.js (sidecar, default)
    python3 viewer/build_viewer.py --standalone     # write build/tag_tree_explorer.standalone.html (single self-contained file)
    python3 viewer/build_viewer.py --standalone --out my_viewer.html

Regenerate after ANY change to taxonomy.json, concepts.json, the docs draft
(state/collect/concepts_docs_draft.jsonl) or instance_images.jsonl.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "viewer" / "build"
TAX = ROOT / "datasets" / "demiwtg" / "meta" / "taxonomy.json"
CONCEPTS = ROOT / "datasets" / "demiwtg" / "meta" / "concepts.json"
DOCS_DRAFT = ROOT / "state" / "collect" / "concepts_docs_draft.jsonl"
OUT_TAX = BUILD / "taxonomy.js"
OUT_CONCEPTS = BUILD / "concepts.js"
VIEWER = ROOT / "viewer" / "tag_tree_explorer.html"
IMAGES_JSONL = ROOT / "datasets" / "demiwtg" / "meta" / "instance_images.jsonl"
BLOBS = ROOT / "datasets" / "demiwtg" / "blobs"
IMGS_JS = BUILD / "imgs.js"

# Marker inserted into tag_tree_explorer.html (the sidecar <script> references).
# NOTE: the ?v= query is a browser cache buster; bump it when sidecar contents change.
SIDECAR_MARK = (
    '<script src="build/taxonomy.js?v=5"></script>\n'
    '<script src="build/concepts.js?v=5"></script>\n'
    '<script src="build/imgs.js?v=5"></script>'
)
INLINE_REPL = (
    '<script>window.__TAXONOMY__ = __TAX__;window.__CONCEPTS__ = __META__;'
    'window.__IMGS__ = null;</script>'
)


def _load():
    """读三源：树 + 概念行 + docs 层草稿（草稿 body 以 docs 字段 join 进概念行）。"""
    tax = json.loads(TAX.read_text(encoding="utf-8"))
    meta = json.loads(CONCEPTS.read_text(encoding="utf-8"))
    docs = {}
    if DOCS_DRAFT.exists():
        with open(DOCS_DRAFT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                docs[r["name"]] = r.get("body") or ""
    for c in meta.get("concepts", []):
        if c.get("name") in docs:
            c["docs"] = docs[c["name"]]
    return tax, meta


def build_sidecar():
    tax, meta = _load()
    OUT_TAX.write_text(
        "window.__TAXONOMY__ = " + json.dumps(tax, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    OUT_CONCEPTS.write_text(
        "window.__CONCEPTS__ = " + json.dumps(meta, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"sidecar written: {OUT_TAX.name} ({OUT_TAX.stat().st_size/1e6:.1f} MB), "
          f"{OUT_CONCEPTS.name} ({OUT_CONCEPTS.stat().st_size/1e6:.1f} MB)")
    build_imgs_js()
    print("双击 tag_tree_explorer.html 即可使用（图片需经 HTTP 服务打开，见 imgs.js 注释）。")


# ---------------------------------------------------------------------------
# 实例原图索引：由 datasets/demiwtg/meta/instance_images.jsonl（统一权威主清单，
# 2026-09-06 起；原 images.jsonl 已收官退役）现场聚合，
# 不再依赖派生索引文件（避免双份存储的一致性问题）。
# 不复制/不缩图：imgs.js 只存相对路径 ../datasets/demiwtg/blobs/<aa>/<sha256>.<ext>
# （相对 viewer/tag_tree_explorer.html 所在目录），需以仓库根为站点根起 HTTP 服务
# （如 python3 -m http.server），浏览器才能加载。
# ---------------------------------------------------------------------------

def _by_score(entries):
    # VLM 打分降序：kb_match 优先，同分按 richness，未打分的排最后
    def key(e):
        km = e.get("km")
        ri = e.get("ri")
        return (-(km if km is not None else -1),
                -(ri if ri is not None else -1))
    return sorted(entries, key=key)


def build_imgs_js():
    if not IMAGES_JSONL.exists():
        print("[warn] instance_images.jsonl 不存在，imgs.js 未生成。")
        return
    blobs_present = BLOBS.is_dir()
    if not blobs_present:
        print("[warn] blobs/ 尚未就位（全量包未解压），跳过存在性检查，"
              "图片路径按 metadata 清单全量写入。")
    idx: dict[str, dict[str, dict]] = {}
    with open(IMAGES_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not rec.get("sha256"):
                continue
            if rec.get("kb_match") is None and rec.get("richness") is None:
                continue  # 精选口径：只收录 VLM 打标行（未打标原池在 instance_images.jsonl 里随取）
            for name in rec.get("instances") or []:
                idx.setdefault(name, {})[rec["sha256"]] = rec  # 同 sha 去重（存量遗留重复键）
    out = {}
    for name, by_sha in idx.items():
        entries = []
        for r in by_sha.values():
            sha = r["sha256"]
            ext = r.get("ext", "jpg")
            rel = f"../datasets/demiwtg/blobs/{sha[:2]}/{sha}.{ext}"
            if blobs_present and not (BLOBS / sha[:2] / f"{sha}.{ext}").exists():
                continue
            e = {"p": rel}
            if r.get("kb_match") is not None:
                e["km"] = r["kb_match"]
            if r.get("richness") is not None:
                e["ri"] = r["richness"]
            if r.get("caption"):
                e["cap"] = r["caption"][:100]
            entries.append(e)
        if entries:
            out[name] = _by_score(entries)[:50]  # 每实体精选 top-50（按打分）
    IMGS_JS.write_text(
        "window.__IMGS__ = " + json.dumps(out, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"imgs written: {IMGS_JS.name} ({IMGS_JS.stat().st_size/1e6:.1f} MB, "
          f"{len(out)} 个实体有图)")


def build_standalone(out_path: pathlib.Path):
    tax, meta = _load()
    if not VIEWER.exists():
        sys.exit(f"viewer not found: {VIEWER}")
    html = VIEWER.read_text(encoding="utf-8")
    if SIDECAR_MARK not in html:
        sys.exit("sidecar marker not found in viewer; viewer may be out of sync with build_viewer.py")
    # Inline the data block by substituting placeholders inside the replacement string.
    inline = INLINE_REPL.replace(
        "__TAX__", json.dumps(tax, ensure_ascii=False)
    ).replace(
        "__META__", json.dumps(meta, ensure_ascii=False)
    )
    html = html.replace(SIDECAR_MARK, inline, 1)
    out_path.write_text(html, encoding="utf-8")
    print(f"standalone written: {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    print("单文件、零设置，双击即用，可任意拷贝。")


def main():
    ap = argparse.ArgumentParser(description="Build file://-friendly viewer data (no server).")
    ap.add_argument("--standalone", action="store_true", help="emit a single self-contained HTML")
    ap.add_argument("--out", type=str, default=None, help="output path for --standalone")
    args = ap.parse_args()
    BUILD.mkdir(exist_ok=True)
    if args.standalone:
        out = pathlib.Path(args.out) if args.out else (BUILD / "tag_tree_explorer.standalone.html")
        build_standalone(out)
    else:
        build_sidecar()


if __name__ == "__main__":
    main()
