import json, os, re

def parse_tree(filepath):
    root = {"name": "ROOT", "children": [], "depth": -1}
    stack = [root]
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = re.search(r'[├└]──\s', line)
            if m:
                prefix_len = m.start()
                depth = prefix_len // 4 + 1
                name = line[m.end():].strip()
            else:
                depth = 0
                name = line.strip()
            if not name:
                continue
            node = {"name": name, "children": [], "depth": depth}
            while stack and stack[-1]["depth"] >= depth:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            stack.append(node)
    return root["children"][0] if root["children"] else root

def build_path(n, parent):
    n["_path"] = (parent + " / " + n["name"]) if parent else n["name"]
    for ch in n.get("children", []):
        build_path(ch, n["_path"])

def count_nodes(n):
    c = 1
    for ch in n.get("children", []):
        c += count_nodes(ch)
    return c

def max_depth(n, d=0):
    if not n.get("children"):
        return d
    return max(max_depth(c, d + 1) for c in n["children"])

# Repo-relative paths (run from repo root, consistent with other scripts/).
TREE_PATH = "V2融合世界标签体系_清洗版.txt"
INST_PATH = "data/ip_instances.json"
# Intermediate JSON; regenerable, kept under build/ (not committed).
JSON_OUT = "build/tag_tree.json"

tree = parse_tree(TREE_PATH)
build_path(tree, None)
total = count_nodes(tree)
md = max_depth(tree)
print("Total nodes: %d, Max depth: %d" % (total, md))
print("Root:", tree["name"])

# Merge instances from data/ip_instances.json
if os.path.exists(INST_PATH):
    with open(INST_PATH, "r", encoding="utf-8") as f:
        inst_data = json.load(f)
    instances = inst_data.get("instances", {})
    leaf_map = {}
    def walk(n):
        if not n.get("children"):
            leaf_map[n["_path"]] = n
        else:
            for ch in n["children"]:
                walk(ch)
    walk(tree)
    prefixes = ["V2融合世界标签体系 / IP 分类标签 / ", "V2融合世界标签体系 / ", ""]
    matched = 0
    for key, vals in instances.items():
        for pfx in prefixes:
            full = pfx + key
            if full in leaf_map:
                leaf_map[full]["instances"] = vals
                matched += 1
                break
    print("Instances matched: %d / %d" % (matched, len(instances)))
else:
    print("No ip_instances.json found")

os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
with open(JSON_OUT, "w", encoding="utf-8") as f:
    json.dump(tree, f, ensure_ascii=False)
print("Saved", JSON_OUT)
