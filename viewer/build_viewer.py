#!/usr/bin/env python3
"""Generate file://-friendly data for tag_tree_explorer.html (no HTTP server needed).

The viewer normally fetch()es data/taxonomy.json + data/instances.json, which
browsers BLOCK under the file:// protocol (null origin). This script wraps each JSON
as a classic <script> that assigns a global (window.__TAXONOMY__ / window.__INSTANCES__),
so the viewer works on double-click with NO running server.

Generated artifacts (gitignored, NOT data) go to viewer/build/:
    build/taxonomy.js / build/instances.js          sidecars (default)
    build/imgs.js                                    实例 → 图片索引（路径 + VLM 打分）
                                                      （由 data/dataset/meta/images.jsonl 现场聚合，
                                                      每项 {p, km, ri, cap}：相对路径/kb_match/richness/caption，
                                                      按 kb_match 降序（同分按 richness 降序）；
                                                      路径为 ../data/dataset/blobs/... 原图，不生成缩略图；
                                                      需经 HTTP 服务打开查看器才能显示图片，
                                                      双击 file:// 时浏览器禁止读取父目录资源）
    build/tag_tree_explorer.standalone.html              single self-contained file
                                                          （standalone 不含图片：不内嵌字节）

Usage:
    python3 viewer/build_viewer.py                 # write build/taxonomy.js + build/instances.js + build/imgs.js (sidecar, default)
    python3 viewer/build_viewer.py --standalone     # write build/tag_tree_explorer.standalone.html (single self-contained file)
    python3 viewer/build_viewer.py --standalone --out my_viewer.html

Regenerate after ANY change to data/taxonomy.json, data/instances.json or images.jsonl.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "viewer" / "build"
TAX = ROOT / "data" / "taxonomy" / "taxonomy.json"
META = ROOT / "data" / "taxonomy" / "instances.json"
OUT_TAX = BUILD / "taxonomy.js"
OUT_META = BUILD / "instances.js"
VIEWER = ROOT / "viewer" / "tag_tree_explorer.html"
IMAGES_JSONL = ROOT / "data" / "dataset" / "meta" / "images.jsonl"
BLOBS = ROOT / "data" / "dataset" / "blobs"
IMGS_JS = BUILD / "imgs.js"

# Marker inserted into tag_tree_explorer.html (the sidecar <script src> references).
SIDECAR_MARK = (
    '<script src="build/taxonomy.js"></script>\n'
    '<script src="build/instances.js"></script>\n'
    '<script src="build/imgs.js"></script>'
)
INLINE_REPL = (
    '<script>window.__TAXONOMY__ = __TAX__;window.__INSTANCES__ = __META__;'
    'window.__IMGS__ = null;</script>'
)


def _load():
    tax = json.loads(TAX.read_text(encoding="utf-8"))
    meta = json.loads(META.read_text(encoding="utf-8"))
    return tax, meta


def build_sidecar():
    tax, meta = _load()
    OUT_TAX.write_text(
        "window.__TAXONOMY__ = " + json.dumps(tax, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    OUT_META.write_text(
        "window.__INSTANCES__ = " + json.dumps(meta, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"sidecar written: {OUT_TAX.name} ({OUT_TAX.stat().st_size/1e6:.1f} MB), "
          f"{OUT_META.name} ({OUT_META.stat().st_size/1e6:.1f} MB)")
    build_imgs_js()
    print("双击 tag_tree_explorer.html 即可使用（图片需经 HTTP 服务打开，见 imgs.js 注释）。")


# ---------------------------------------------------------------------------
# 实例原图索引：由 dataset/meta/images.jsonl（唯一真相主清单）现场聚合，
# 不再依赖派生索引文件（避免双份存储的一致性问题）。
# 不复制/不缩图：imgs.js 只存相对路径 ../data/dataset/blobs/<aa>/<sha256>.<ext>
# （相对 viewer/tag_tree_explorer.html 所在目录），需以仓库根为站点根起 HTTP 服务
# （如 python3 -m http.server），浏览器才能加载。
# ---------------------------------------------------------------------------

def _sorted_recs(recs):
    def key(r):
        rank = r.get("source_rank")
        tiers = r.get("tiers") or []
        return ((99 if rank is None else rank),
                (min(tiers) if tiers else 99),
                r.get("sha256", ""))
    return sorted(recs, key=key)


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
        print("[warn] images.jsonl 不存在，imgs.js 未生成。")
        return
    idx: dict[str, list] = {}
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
            for name in rec.get("instances") or []:
                idx.setdefault(name, []).append(rec)
    out = {}
    for name, recs in idx.items():
        entries = []
        for r in _sorted_recs(recs):
            sha = r.get("sha256", "")
            if not sha:
                continue
            rel = f"../data/dataset/blobs/{sha[:2]}/{sha}.{r.get('ext', 'jpg')}"
            if not (BLOBS / sha[:2] / f"{sha}.{r.get('ext', 'jpg')}").exists():
                continue
            e = {"p": rel}
            if r.get("kb_match") is not None:
                e["km"] = r["kb_match"]
            if r.get("richness") is not None:
                e["ri"] = r["richness"]
            if r.get("caption"):
                e["cap"] = r["caption"]
            entries.append(e)
        if entries:
            out[name] = _by_score(entries)
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
