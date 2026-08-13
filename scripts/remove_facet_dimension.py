#!/usr/bin/env python3
"""Remove the entire `传播范围`/`呈现载体` facet dimension from the tag tree.

A facet wrapper is any node whose name ends with `—传播范围` or `—呈现载体`
(e.g. `影视明星类别—传播范围`). The wrapper and its whole subtree are deleted.
The tree is re-rendered so parent connectors flip correctly (a parent whose last
child was a facet becomes `└──`). Matching instance entries in
`data/ip_instances.json` are also removed.

Default: preview only. Pass --write to apply.
"""
import re, json, sys

PATH = "data/V2融合世界标签体系_清洗版.txt"
INST = "data/ip_instances.json"

SUFFIXES = ()  # populated at runtime by collect_suffixes()


class Node:
    __slots__ = ("name", "depth", "children", "parent")
    def __init__(self, name, depth):
        self.name = name
        self.depth = depth
        self.children = []
        self.parent = None


def parse(text):
    root = None
    stack = []
    for line in text.split("\n"):
        if line.strip() == "":
            continue
        m = re.search(r'[├└]──\s', line)
        if m:
            depth = m.start() // 4 + 1
            name = line[m.end():].strip()
        else:
            depth = 0
            name = line.strip()
        node = Node(name, depth)
        if depth == 0:
            root = node
        else:
            while stack and stack[-1].depth >= depth:
                stack.pop()
            parent = stack[-1] if stack else root
            node.parent = parent
            parent.children.append(node)
            stack.append(node)
    return root


def is_facet(n):
    return n.name.endswith(SUFFIXES)


def collect_suffixes(root):
    """A facet-dimension wrapper is any node whose name contains a '—'
    separator followed by a dimension word (e.g. '足球—完好状态').
    Collect every distinct suffix after the last '—' so the rule survives
    new dimension words without hardcoding them."""
    suffixes = set()
    def walk(n):
        if "—" in n.name:
            suffixes.add(n.name[n.name.rindex("—"):])
        for c in n.children:
            walk(c)
    walk(root)
    return suffixes


def collect_delete(root):
    deleted = set()
    def walk(n):
        if n in deleted:
            return
        if is_facet(n):
            # mark n and all descendants
            stack = [n]
            while stack:
                cur = stack.pop()
                if cur in deleted:
                    continue
                deleted.add(cur)
                stack.extend(cur.children)
        else:
            for c in n.children:
                walk(c)
    walk(root)
    return deleted


def render(node):
    if node.parent is None:
        return node.name
    chain = []
    a = node.parent
    while a is not None:
        chain.append(a)
        a = a.parent
    chain_rev = list(reversed(chain))  # top-down: depth1 .. parent
    parts = []
    for anc in chain_rev:
        if anc.parent is None:
            continue  # depth1 node: no rail above
        parts.append("    " if anc is anc.parent.children[-1] else "│   ")
    prefix = "".join(parts)
    conn = "└── " if node is node.parent.children[-1] else "├── "
    return prefix + conn + node.name


def main():
    apply = "--write" in sys.argv
    text = open(PATH, encoding="utf-8").read()
    root = parse(text)

    global SUFFIXES
    SUFFIXES = tuple(collect_suffixes(root))

    deleted = collect_delete(root)
    # rebuild children lists excluding deleted nodes
    def prune(n):
        n.children = [c for c in n.children if c not in deleted]
        for c in n.children:
            prune(c)
    prune(root)

    out = [root.name]
    def walk(n):
        for ch in n.children:
            out.append(render(ch))
            walk(ch)
    walk(root)
    new_text = "\n".join(out) + "\n"

    # JSON instances
    data = json.load(open(INST, encoding="utf-8"))
    inst = data.get("instances", {})
    new_inst = {}
    removed_keys = []
    for k, v in inst.items():
        parts = k.split(" / ")
        if any(p.endswith(SUFFIXES) for p in parts):
            removed_keys.append(k)
            continue
        new_inst[k] = v

    old_nodes = text.count("\n") + 1
    new_nodes = new_text.count("\n") + 1
    print(f"Tree nodes: {old_nodes} -> {new_nodes} (removed {len(deleted)})")
    print(f"Instance keys removed: {len(removed_keys)}")
    remaining = sum(1 for _ in re.finditer(r'[├└]──\s.*(' + "|".join(re.escape(s) for s in SUFFIXES) + r')', new_text))
    print(f"Remaining {SUFFIXES} wrappers in tree: {remaining}")

    if not apply:
        print("\n[preview] pass --write to apply.")
        return

    open(PATH, "w", encoding="utf-8").write(new_text)
    data["instances"] = new_inst
    json.dump(data, open(INST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nApplied.")


if __name__ == "__main__":
    main()
