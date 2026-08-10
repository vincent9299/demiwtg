#!/usr/bin/env python3
"""Parse the V2 taxonomy tree text and emit a self-contained HTML tree viewer.

Usage:
    python3 scripts/build_tree_view.py \
        -i "V2融合世界标签体系_清洗版.txt" \
        -o output/taxonomy_tree.html
"""
import argparse
import html
import re
import sys
from collections import Counter

NODE_RE = re.compile(r'^((?:[│  ]{4})*)([├└]── (.*))$')


def parse_tree(lines):
    root_name = lines[0].strip()
    root = {"name": root_name, "children": [], "depth": 0}
    stack = [(0, root)]
    for lineno, line in enumerate(lines[1:], start=2):
        m = NODE_RE.match(line)
        if not m:
            print(f"[warn] line {lineno}: cannot parse: {line!r}", file=sys.stderr)
            continue
        prefix, _, name = m.groups()
        depth = len(prefix) // 4 + 1
        node = {"name": name, "children": [], "depth": depth}
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1]["children"].append(node)
        stack.append((depth, node))
    return root


def count_leaves(node):
    if not node["children"]:
        return 1
    return sum(count_leaves(c) for c in node["children"])


def count_nodes(node):
    return 1 + sum(count_nodes(c) for c in node["children"])


def build_html(root):
    total_nodes = count_nodes(root)
    tree_html = render_node(root, is_root=True)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(root['name'])} · 树形浏览器</title>
<style>
:root {{
  --bg: #fafaf8;
  --fg: #1f2328;
  --line: #d0d7de;
  --accent: #0969da;
  --muted: #656d76;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; font: 14px/1.55 -apple-system, "PingFang SC", "Hiragino Sans GB",
        "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--fg);
}}
header {{
  position: sticky; top: 0; z-index: 10;
  background: #fff; border-bottom: 1px solid var(--line);
  padding: 10px 16px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
}}
header h1 {{ font-size: 16px; margin: 0 12px 0 0; }}
header .stats {{ color: var(--muted); font-size: 12px; margin-right: auto; }}
header input {{ padding: 5px 10px; border: 1px solid var(--line); border-radius: 6px; width: 220px; }}
header button {{
  padding: 5px 10px; border: 1px solid var(--line); border-radius: 6px;
  background: #fff; cursor: pointer; font-size: 12px;
}}
header button:hover {{ background: #f3f4f6; }}
.tree {{ padding: 14px 16px 40px; }}
ul.rt {{ list-style: none; margin: 0; padding-left: 0; }}
li {{ margin: 0; padding-left: 0; }}
details {{ margin: 0; }}
summary {{
  cursor: pointer; list-style: none; padding: 2px 6px; border-radius: 4px;
  white-space: nowrap;
}}
summary::-webkit-details-marker {{ display: none; }}
summary::before {{
  content: "▸"; display: inline-block; width: 1.1em; color: var(--muted);
  transition: transform .12s ease;
}}
details[open] > summary::before {{ content: "▾"; }}
summary:hover {{ background: #eef2f7; }}
li.leaf > span {{
  padding: 2px 6px; border-radius: 4px; white-space: nowrap;
  display: inline-block;
}}
li.leaf > span:hover {{ background: #eef2f7; }}
li.leaf > span::before {{ content: "·"; display: inline-block; width: 1.1em; color: var(--muted); }}
details > ul {{ list-style: none; margin: 0; padding-left: 1.4em; border-left: 1px solid var(--line); }}
mark {{ background: #fff2a8; border-radius: 2px; padding: 0 1px; }}
.count {{ color: var(--muted); font-size: 11px; margin-left: 6px; font-weight: normal; }}
.empty {{ color: var(--muted); padding: 20px; text-align: center; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(root['name'])}</h1>
  <span class="stats" id="stats"></span>
  <input id="q" type="search" placeholder="搜索节点…" autocomplete="off">
  <button id="expandAll">全部展开</button>
  <button id="collapseAll">全部折叠</button>
</header>
<div class="tree" id="tree">
{tree_html}
</div>
<script>
const tree = document.getElementById('tree');
const q = document.getElementById('q');
const stats = document.getElementById('stats');
const NODES = {total_nodes};

function descendants(d) {{ return d.querySelectorAll('details').length; }}

function updateStats() {{
  stats.textContent = NODES + ' 个节点 · 输入关键词可过滤';
}}

function setAll(open) {{
  const dets = tree.querySelectorAll('details');
  dets.forEach(d => {{ if (open) d.setAttribute('open',''); else d.removeAttribute('open'); }});
}}

function walk(details, fn) {{ fn(details); details.querySelectorAll(':scope > ul > li > details').forEach(c => walk(c, fn)); }}

function highlight() {{
  const kw = q.value.trim();
  if (!kw) {{
    tree.querySelectorAll('mark').forEach(m => m.replaceWith(m.textContent));
    tree.classList.remove('filtering');
    return;
  }}
  const re = new RegExp(escapeRe(kw), 'gi');
  tree.classList.add('filtering');
  const dets = tree.querySelectorAll('details');
  const leafSpans = tree.querySelectorAll('li.leaf > span');
  dets.forEach(d => {{
    const s = d.querySelector(':scope > summary');
    const name = s.childNodes[0];
    s.childNodes[0].replaceWith(markify(name.textContent, re));
    const count = d.querySelector(':scope > summary .count');
    if (count) count.textContent = ' ' + descendants(d) + ' 后代';
  }});
  leafSpans.forEach(span => span.replaceWith(markify(span.textContent, re)));
  // decide visibility: keep node if itself or any descendant matches
  dets.forEach(d => {{
    const visible = d.textContent.match(re);
    let p = d;
    while (p) {{
      p.style.display = visible ? '' : 'none';
      p = p.parentElement.closest('details');
    }}
  }});
  dets.forEach(d => {{
    const li = d.parentElement;
    if (li.style.display === 'none') return;
    const kids = li.querySelector(':scope > ul');
    if (kids && !kids.textContent.match(re)) {{ d.removeAttribute('open'); }}
  }});
}}

function markify(text, re) {{
  const safe = text.replace(/[<>]/g, c => c === '<' ? '&lt;' : '&gt;');
  const el = document.createElement('span');
  el.innerHTML = safe.replace(re, m => '<mark>' + m + '</mark>');
  return el;
}}

function escapeRe(s) {{ return s.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'); }}

document.getElementById('expandAll').onclick = () => setAll(true);
document.getElementById('collapseAll').onclick = () => setAll(false);
q.addEventListener('input', highlight);
updateStats();
</script>
</body>
</html>
"""


def render_node(node, is_root=False):
    kids = node["children"]
    if not kids:
        return f'<li class="leaf"><span>{html.escape(node["name"])}</span></li>'
    body = "".join(render_node(c) for c in kids)
    if is_root:
        return f'<ul class="rt"><li><details open><summary>{html.escape(node["name"])}</summary><ul>{body}</ul></details></li></ul>'
    count = sum(count_leaves(c) for c in kids)
    return (f'<li><details><summary>{html.escape(node["name"])}'
            f'<span class="count">{count} 项</span></summary><ul>{body}</ul></details></li>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", default="V2融合世界标签体系_清洗版.txt")
    ap.add_argument("-o", "--output", default="output/taxonomy_tree.html")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        lines = f.read().splitlines()

    root = parse_tree(lines)
    html_out = build_html(root)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"nodes: {count_nodes(root)}, depth: {max(n['depth'] for n in flatten(root))}")
    print(f"written: {args.output} ({len(html_out)/1024:.0f} KB)")


def flatten(node):
    yield node
    for c in node["children"]:
        yield from flatten(c)


if __name__ == "__main__":
    main()
