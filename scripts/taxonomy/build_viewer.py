#!/usr/bin/env python3
"""Generate file://-friendly data for tag_tree_explorer.html (no HTTP server needed).

The viewer normally fetch()es data/taxonomy.json + data/instances.json, which
browsers BLOCK under the file:// protocol (null origin). This script wraps each JSON
as a classic <script> that assigns a global (window.__TAXONOMY__ / window.__INSTANCES__),
so the viewer works on double-click with NO running server.

Generated artifacts (gitignored, NOT data) go to build/:
    build/taxonomy.js / build/instances.js          sidecars (default)
    build/tag_tree_explorer.standalone.html              single self-contained file

Usage:
    python3 scripts/taxonomy/build_viewer.py                 # write build/taxonomy.js + build/instances.js (sidecar, default)
    python3 scripts/taxonomy/build_viewer.py --standalone     # write build/tag_tree_explorer.standalone.html (single self-contained file)
    python3 scripts/taxonomy/build_viewer.py --standalone --out my_viewer.html

Regenerate after ANY change to data/taxonomy.json or data/instances.json.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BUILD = ROOT / "taxonomy-instances" / "build"
TAX = ROOT / "taxonomy-instances" / "data" / "taxonomy.json"
META = ROOT / "taxonomy-instances" / "data" / "instances.json"
OUT_TAX = BUILD / "taxonomy.js"
OUT_META = BUILD / "instances.js"
VIEWER = ROOT / "taxonomy-instances" / "tag_tree_explorer.html"

# Marker inserted into tag_tree_explorer.html (the two sidecar <script src> tags).
SIDECAR_MARK = (
    '<script src="build/taxonomy.js"></script>\n'
    '<script src="build/instances.js"></script>'
)
INLINE_REPL = (
    '<script>window.__TAXONOMY__ = __TAX__;window.__INSTANCES__ = __META__;</script>'
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
    print("双击 tag_tree_explorer.html 即可使用，无需服务器。")


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
